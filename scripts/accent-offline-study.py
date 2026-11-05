"""Reference-free ASR ablations; references are used ONLY by the scorer.

Research CLI, not imported by the application. All generated evidence stays in
the caller's output directory. No network inference or automatic model download.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import time
import wave
from collections import Counter
from pathlib import Path


def tokens(text):
    text = re.sub(r"<[^>]+>", " ", text.lower().replace("’", "'"))
    return re.findall(r"[a-z]+(?:'[a-z]+)?|\d+", text)


def score(reference, hypothesis):
    a, b = tokens(reference), tokens(hypothesis)
    dp = [list(range(len(b) + 1))]
    for i, x in enumerate(a, 1):
        row = [i]
        for j, y in enumerate(b, 1):
            row.append(min(row[-1] + 1, dp[-1][j] + 1, dp[-1][j - 1] + (x != y)))
        dp.append(row)
    i, j = len(a), len(b)
    counts = Counter(S=0, D=0, I=0)
    correct = [False] * len(a)
    while i or j:
        if i and j and dp[i][j] == dp[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            correct[i - 1] = a[i - 1] == b[j - 1]
            if not correct[i - 1]:
                counts["S"] += 1
            i, j = i - 1, j - 1
        elif i and dp[i][j] == dp[i - 1][j] + 1:
            counts["D"] += 1
            i -= 1
        else:
            counts["I"] += 1
            j -= 1
    return dict(counts, N=len(a), errors=sum(counts.values()), correct=correct)


def edits(original, proposed):
    a, b = tokens(original), tokens(proposed)
    return [
        (i, j, tuple(b[k:end]))
        for tag, i, j, k, end in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        if tag != "equal"
    ]


PROTECTED = set(
    (
        "no not never none neither nor without cannot can could would should "
        "may might must less more least most above below before after than "
        "one two three four five six seven eight nine ten hundred thousand "
        "million billion zero first second third percent percentage meter "
        "meters metre metres kilogram kilograms gram grams volt volts watt "
        "watts hz khz mhz gb mb kb seconds minutes hours degrees"
    ).split()
)


def local_consensus(original, batch, window, boundaries):
    """Frozen exploratory rule, never receives a reference or its error count.

    Keep exact local edits shared by same-boundary batch and whole-window decode,
    <=3 old/new words, within ONE caption, no protected/nonalphabetical tokens.
    This agreement is not independent proof of correctness; measure its mistakes.
    """
    source = tokens(original)
    shared = set(edits(original, batch)) & set(edits(original, window))
    accepted = []
    for i, j, new in sorted(shared):
        if max(j - i, len(new)) > 3:
            continue
        if any(x in PROTECTED or not x.isalpha() for x in source[i:j] + list(new)):
            continue
        if not any(lo <= i <= j <= hi and (i != j or lo < i < hi) for lo, hi in boundaries):
            continue
        accepted.append((i, j, new))
    result = source[:]
    for i, j, new in reversed(accepted):
        result[i:j] = new
    return " ".join(result), accepted


def read_audio(path):
    import numpy as np

    with wave.open(str(path)) as w:
        assert w.getsampwidth() == 2
        rate, channels = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        pcm = np.interp(
            np.arange(round(len(pcm) * 16000 / rate)) * rate / 16000, np.arange(len(pcm)), pcm
        ).astype("float32")
    return pcm


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run(args):
    import fcntl
    import importlib.metadata
    import resource

    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        TOKENIZERS_PARALLELISM="false",
    )
    import mlx.core as mx

    manifest = json.loads(args.manifest.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / f"{args.mode}.jsonl"
    done = (
        {json.loads(x)["id"] for x in result_path.read_text().splitlines()}
        if result_path.exists()
        else set()
    )
    locks = []
    for name in [".model.lock", ".live-priority.lock"]:
        h = (args.library / name).open("rb")
        fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(h)

    def measure(fn):
        mx.synchronize()
        start = time.perf_counter()
        result = fn()
        mx.synchronize()
        return result, time.perf_counter() - start

    if args.mode in {"qwen", "masked"}:
        from mlx_qwen3_asr import Session

        from lectureflow.accent import AccentReviewer
        from lectureflow.live import CaptionEngine

        session = Session(str(args.model))

        def decode(pcm):
            result = session.transcribe(pcm, language="English", context="")
            if result.truncated:
                raise RuntimeError("Truncated decode; do not silently score it")
            return result.text.strip()

        def stream(pcm):
            events = {}
            engine = CaptionEngine(
                session,
                None,
                lambda e, *_: events.update({e["id"]: dict(e)}),
                translate=False,
                language="English",
                accent=False,
            )
            for i in range(0, len(pcm), 4000):
                engine.feed(pcm[i : i + 4000], i / 16000)
            engine.finish()
            engine.lock_all()
            return [
                {"start": e["start"], "end": e["end"], "text": e["english"]}
                for e in sorted(events.values(), key=lambda e: e["start"])
            ]

        reviewer = AccentReviewer(session)
    else:
        import mlx_whisper
        from mlx_whisper.transcribe import ModelHolder

        ModelHolder.get_model(str(args.model), mx.float16)

        def decode(pcm):
            result = mlx_whisper.transcribe(
                pcm,
                path_or_hf_repo=str(args.model),
                language="en",
                task="transcribe",
                temperature=0.0,
                condition_on_previous_text=False,
                verbose=None,
                word_timestamps=False,
                fp16=True,
            )
            return result["text"].strip()

    first = read_audio(Path(manifest["rows"][0]["audio"]))
    decode(first[: min(len(first), 80000)])
    prior = {}
    if args.mode == "masked":
        prior = {
            r["id"]: r
            for r in map(json.loads, (args.output / "qwen.jsonl").read_text().splitlines())
        }
    metadata = {
        "mode": args.mode,
        "model_name": args.model.name,
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "packages": {p: importlib.metadata.version(p) for p in ["mlx", "mlx-qwen3-asr", "numpy"]},
        "timing": "Synchronized ASR-only compute; excludes load/warmup/I/O. "
        "Not live latency. Single pass, deterministic decoding.",
        "reference_access": "Manifest references not passed to inference or selection.",
    }
    with result_path.open("a") as output:
        for sample in manifest["rows"]:
            if sample["id"] in done:
                continue
            pcm = read_audio(Path(sample["audio"]))
            row = {
                "id": sample["id"],
                "group": sample["group"],
                "speaker": sample["speaker"],
                "audio_seconds": len(pcm) / 16000,
            }
            if args.mode == "masked":
                import numpy as np

                mask = np.zeros(len(pcm), dtype=bool)
                for segment in prior[sample["id"]]["segments"]:
                    mask[round(segment["start"] * 16000) : round(segment["end"] * 16000)] = True
                if mask.all():
                    text, elapsed = prior[sample["id"]]["window"]["text"], 0
                else:
                    text, elapsed = measure(lambda pcm=pcm, mask=mask: decode(pcm * mask))
                row.update(
                    text=text,
                    seconds=elapsed,
                    reused_original=bool(mask.all()),
                    masked_seconds=float((~mask).sum()) / 16000,
                )
            elif args.mode == "whisper":
                text, elapsed = measure(lambda pcm=pcm: decode(pcm))
                row.update(text=text, seconds=elapsed)
            else:
                segments, elapsed = measure(lambda pcm=pcm: stream(pcm))
                baseline = " ".join(e["text"] for e in segments)
                row.update(segments=segments, stream={"text": baseline, "seconds": elapsed})
                batch_parts, batch_seconds, boundaries, count = [], 0, [], 0
                audits, gate_parts, structural_parts = [], [], []
                for i, segment in enumerate(segments):
                    a, b = round(segment["start"] * 16000), round(segment["end"] * 16000)
                    target = pcm[a:b]
                    text, seconds = measure(lambda target=target: decode(target))
                    batch_parts.append(text)
                    batch_seconds += seconds
                    end = count + len(tokens(segment["text"]))
                    boundaries.append((count, end))
                    count = end
                    following, following_pcm = "", pcm[:0]
                    next_segment = segments[i + 1] if i + 1 < len(segments) else None
                    if next_segment and abs(next_segment["start"] - segment["end"]) <= 0.1:
                        following = next_segment["text"]
                        following_pcm = pcm[
                            round(next_segment["start"] * 16000) : round(
                                next_segment["end"] * 16000
                            )
                        ]
                    audit = reviewer.review(segment["text"], following, target, following_pcm)
                    audit.update(
                        start=segment["start"],
                        end=segment["end"],
                        target_seconds=len(target) / 16000,
                        following_seconds=len(following_pcm) / 16000,
                        next_gap=(next_segment["start"] - segment["end"]) if next_segment else None,
                    )
                    audits.append(audit)
                    gate_parts.append(audit["candidate"] if audit["accepted"] else segment["text"])
                    structural_parts.append(audit["candidate"] or segment["text"])
                full, elapsed = measure(lambda pcm=pcm: decode(pcm))
                batch = " ".join(batch_parts)
                local, accepted = local_consensus(baseline, batch, full, boundaries)
                row.update(
                    batch={"text": batch, "seconds": batch_seconds},
                    window={"text": full, "seconds": elapsed},
                    gate={"text": " ".join(gate_parts)},
                    structural={"text": " ".join(structural_parts)},
                    local={"text": local, "edits": accepted},
                    audits=audits,
                )
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
            print("DONE", args.mode, sample["id"], round(row["audio_seconds"], 2), flush=True)
    metadata.update(
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        peak_mlx_bytes=mx.get_peak_memory(),
    )
    dump(args.output / f"{args.mode}-metadata.json", metadata)


def analyze(args):
    manifest = json.loads(args.manifest.read_text())
    refs = {r["id"]: r for r in manifest["rows"]}
    rows = [json.loads(x) for x in (args.output / "qwen.jsonl").read_text().splitlines()]
    if len(rows) != len(refs) or {r["id"] for r in rows} != set(refs):
        raise ValueError("Analysis requires one completed Qwen record for every manifest row")
    whisper_path = args.output / "whisper.jsonl"
    whisper = (
        {r["id"]: r for r in map(json.loads, whisper_path.read_text().splitlines())}
        if whisper_path.exists()
        else {}
    )
    masked_path = args.output / "masked.jsonl"
    masked = (
        {r["id"]: r for r in map(json.loads, masked_path.read_text().splitlines())}
        if masked_path.exists()
        else {}
    )
    for label, additional in [("whisper", whisper), ("masked", masked)]:
        if additional and set(additional) != set(refs):
            raise ValueError(f"Incomplete {label} evidence; do not compare different denominators")
    summaries, details = {}, []
    for row in rows:
        ref = refs[row["id"]]["reference"]
        baseline = score(ref, row["stream"]["text"])
        methods = {k: row[k] for k in ["stream", "batch", "window", "gate", "structural", "local"]}
        if row["id"] in whisper:
            methods["whisper"] = whisper[row["id"]]
        if row["id"] in masked:
            methods["masked"] = masked[row["id"]]
        record = {
            "id": row["id"],
            "group": row["group"],
            "speaker": row["speaker"],
            "reference": ref,
            "methods": {},
        }
        group = summaries.setdefault(
            row["group"],
            {
                "methods": {},
                "reasons": Counter(),
                "with_following": 0,
                "reviews": 0,
                "review_seconds": 0,
            },
        )
        group["reasons"].update(a["reason"] for a in row["audits"])
        group["reviews"] += len(row["audits"])
        group["with_following"] += sum(a["following_seconds"] > 0 for a in row["audits"])
        group["review_seconds"] += sum(a["seconds"] for a in row["audits"])
        for method, result in methods.items():
            s = score(ref, result["text"])
            fixed = sum(not a and b for a, b in zip(baseline["correct"], s["correct"], strict=True))
            harmed = sum(
                a and not b for a, b in zip(baseline["correct"], s["correct"], strict=True)
            )
            aggregate = group["methods"].setdefault(method, Counter())
            aggregate.update({k: s[k] for k in ["S", "D", "I", "N", "errors"]})
            aggregate.update(
                clips=1,
                improved=int(s["errors"] < baseline["errors"]),
                worsened=int(s["errors"] > baseline["errors"]),
                fixed_reference_words=fixed,
                harmed_reference_words=harmed,
                seconds=result.get("seconds", 0),
                audio_seconds=row["audio_seconds"],
            )
            record["methods"][method] = {
                **s,
                "fixed": fixed,
                "harmed": harmed,
                "text": result["text"],
            }
        details.append(record)
    for group in summaries.values():
        for method, value in group["methods"].items():
            value["wer"] = value["errors"] / value["N"]
            value["rtf"] = value["seconds"] / value["audio_seconds"]
            if method in {"gate", "structural", "local"}:
                value["seconds"] = value["rtf"] = None
                value["timing_note"] = "Derived text selection; underlying inference is not free"
            elif method == "masked":
                value["rtf"] = None
                value["timing_note"] = (
                    "Some identical inputs reuse window output; not comparable RTF"
                )
    dump(args.output / "summary.json", summaries)
    dump(args.output / "scored-details.json", details)
    print(json.dumps(summaries, indent=2))


def self_check():
    assert score("a b c", "a x c d")["errors"] == 2
    assert score("a b c", "a c")["D"] == 1
    assert score("<LAUGH> it is", "IT IS.")["errors"] == 0
    s, e = local_consensus("a bad cat", "a good cat", "a good cat", [(0, 3)])
    assert s == "a good cat" and len(e) == 1
    s, e = local_consensus("it can work", "it cannot work", "it cannot work", [(0, 3)])
    assert s == "it can work" and not e
    s, e = local_consensus("a bad cat", "a good cat", "a nice cat", [(0, 3)])
    assert s == "a bad cat" and not e


if __name__ == "__main__":
    self_check()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["qwen", "masked", "whisper", "analyze"])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--library", type=Path)
    args = parser.parse_args()
    if args.mode == "analyze":
        analyze(args)
    else:
        if not args.model or not args.library or not args.model.is_dir():
            parser.error("Provide existing --model and --library; downloads are not performed")
        run(args)
