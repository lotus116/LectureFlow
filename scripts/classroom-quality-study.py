"""Local classroom research only. Never writes to the application library."""

from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import os
import re
import resource
import threading
import time
import wave
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def words(text):
    return re.findall(r"[a-z]+(?:'[a-z]+)?|\d+", text.lower())


def read_audio(path):
    import numpy as np

    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768


class Timed:
    def __init__(self, obj, names):
        self.obj, self.names, self.calls = obj, names, []

    def __getattr__(self, name):
        value = getattr(self.obj, name)
        if name not in self.names:
            return value

        def call(*args, **kwargs):
            started = time.monotonic()
            try:
                return value(*args, **kwargs)
            finally:
                self.calls.append({"method": name, "seconds": time.monotonic() - started})

        return call


def baseline(record, session, translator, output):
    import numpy as np

    from lectureflow.live import CaptionEngine, InputBuffer

    pcm = read_audio(record["audio"])[30 * 16000 : 330 * 16000]
    events = []
    progress = []
    delays = []
    backlogs = []
    waits = []
    q = InputBuffer(capacity=240)
    t0 = time.monotonic()
    asr0, mt0 = len(session.calls), len(translator.calls)

    def emit(e, *_):
        events.append(
            {k: v for k, v in e.items() if not k.startswith("_")} | {"wall": time.monotonic() - t0}
        )

    engine = CaptionEngine(
        session, translator, emit, deferred=True, accent=False, language="English"
    )

    def produce():
        for i in range(0, len(pcm), 4000):
            end = (i + len(pcm[i : i + 4000])) / 16000
            time.sleep(max(0, t0 + end - time.monotonic()))
            q.put({"type": "audio", "start": i / 16000, "pcm": pcm[i : i + 4000], "end": end})
        q.put({"type": "end"})

    worker = threading.Thread(target=produce)
    worker.start()
    while True:
        job = q.get()
        if job["type"] == "end":
            break
        delays.append(max(0, time.monotonic() - t0 - job["end"]))
        backlogs.append(q.backlog())
        engine.feed(job["pcm"], job["start"])
        waits.extend(time.monotonic() - j.queued_at for j in engine.pending_translations.values())
        engine.service(q.backlog())
        progress.append(engine.translation_progress())
    engine.finish()
    engine.lock_all()
    worker.join()
    final = {e["id"]: e for e in events}
    stable = {}
    zh = {}
    for e in events:
        if e["status"] == "stable":
            stable.setdefault(e["id"], e)
        if e["chinese"]:
            zh.setdefault(e["id"], e)

    def lag(items):
        x = [max(0, v["wall"] - v["end"]) for v in items.values()]
        return float(np.percentile(x, 95)) if x else None

    result = {
        "recording": record["id"],
        "seconds": len(pcm) / 16000,
        "wall_seconds": time.monotonic() - t0,
        "events": events,
        "final": list(final.values()),
        "first_stable": list(stable.values()),
        "asr_compute_seconds": sum(c["seconds"] for c in session.calls[asr0:]),
        "mt_compute_seconds": sum(c["seconds"] for c in translator.calls[mt0:]),
        "first_stable_tail_p95": lag(stable),
        "first_chinese_tail_p95": lag(zh),
        "max_packet_delay": max(delays, default=0),
        "max_backlog": max(backlogs, default=0),
        "max_translation_wait_observed": max(waits, default=0),
        "dropped": q.dropped,
        "progress": engine.translation_progress(),
        "max_translation_pending": max(x["translation_pending"] for x in progress),
    }
    dump(output / f"{record['id']}-baseline.json", result)
    print(
        "BASELINE",
        record["id"],
        {k: v for k, v in result.items() if k not in ["events", "final", "first_stable"]},
        flush=True,
    )


def selected(record, base):
    entries = sorted(base["final"], key=lambda e: e["start"])
    chosen = []
    used = set()
    for core in record["cores"]:
        e = min(
            entries, key=lambda e: max(e["start"] - core["anchor"], core["anchor"] - e["end"], 0)
        )
        chosen.append({"id": core["id"], "kind": "fixed", "core": core, "entry": e})
        used.add(e["id"])

    def signal(e):
        ws = words(e["english"])
        return (
            3 * bool(re.search(r"[\u4e00-\u9fff]", e["english"]))
            + 2 * (len(ws) < 4)
            + sum(a == b for a, b in zip(ws, ws[1:], strict=False))
        )

    remaining = [e for e in entries if e["id"] not in used]
    for i, e in enumerate(sorted(remaining, key=lambda e: (-signal(e), e["start"]))[:4]):
        chosen.append(
            {
                "id": f"{record['id']}-hard{i + 1}",
                "kind": "diagnostic",
                "signal": signal(e),
                "entry": e,
            }
        )
        used.add(e["id"])
    remaining = [
        e
        for e in entries
        if e["id"] not in used and len(words(e["english"])) >= 8 and signal(e) == 0
    ]
    for i, e in enumerate(remaining[:2]):
        chosen.append(
            {
                "id": f"{record['id']}-protect{i + 1}",
                "kind": "protection_candidate",
                "signal": 0,
                "entry": e,
            }
        )
    return chosen


def project(prefix, target, decoded):
    """Text-only approximate ownership; preserve raw output and uncertainty."""
    left, mid, out = words(prefix), words(target), words(decoded)
    lo = len(left)
    hi = lo + len(mid)
    ranges = []
    uncertain = []
    ops = []
    for tag, a, b, c, d in difflib.SequenceMatcher(
        None, left + mid, out, autojunk=False
    ).get_opcodes():
        ops.append([tag, a, b, c, d])
        if tag == "insert":
            if lo < a < hi:
                ranges.append((c, d))
            elif a in {lo, hi}:
                uncertain.append("boundary_insertion")
        elif b > lo and a < hi:
            if tag == "equal":
                ranges.append((c + max(lo - a, 0), d - max(b - hi, 0)))
            else:
                if a < lo or b > hi:
                    uncertain.append("cross_boundary_edit")
                if d > c:
                    ranges.append((c, d))
    spans = list(re.finditer(r"[a-z]+(?:'[a-z]+)?|\d+", decoded, re.I))
    if ranges:
        first, last = min(c for c, d in ranges), max(d for c, d in ranges)
        begin, finish = spans[first].start(), spans[last - 1].end()
        if decoded[finish : finish + 1] in {".", "?", "!", ","}:
            finish += 1
        text = decoded[begin:finish]
    else:
        text = ""
    if not text:
        uncertain.append("empty_projection")
    return {"text": text, "uncertain": sorted(set(uncertain)), "alignment": ops}


def ablate(record, session, translator, output):
    from lectureflow.inference_policy import page_draft_score, select_asr_hints, word_tokens

    base = json.loads((output / f"{record['id']}-baseline.json").read_text())
    materials = json.loads(Path(record["materials"]).read_text())
    entries = sorted(base["final"], key=lambda e: e["start"])
    picks = selected(record, base)
    dump(output / f"{record['id']}-selection.json", picks)
    pcm = read_audio(record["audio"])
    results = []

    def decode(start, end, hint):
        t = time.monotonic()
        r = session.transcribe(
            pcm[round((start + 30) * 16000) : round((end + 30) * 16000)],
            language="English",
            context=hint,
        )
        return {
            "raw": r.text,
            "truncated": r.truncated,
            "seconds": time.monotonic() - t,
            "start": start,
            "end": end,
            "hint": hint,
        }

    for pick in picks:
        e = pick["entry"]
        i = next(k for k, v in enumerate(entries) if v["id"] == e["id"])
        context = "\n".join(v["english"] for v in entries[max(0, i - 2) : i])
        following = "\n".join(v["english"] for v in entries[i + 1 : i + 3])
        retrieval_started = time.monotonic()
        hints = select_asr_hints(
            materials["hint_terms"],
            materials["documents"],
            context + " " + e["english"],
            term_limit=6,
            char_limit=160,
        )
        retrieval_seconds = time.monotonic() - retrieval_started
        methods = {}
        row = {
            "retrieval_seconds": retrieval_seconds,
            "id": pick["id"],
            "kind": pick["kind"],
            "target": e,
            "context": context,
            "following": following,
            "hint": {
                "text": hints.context,
                "terms": list(hints.terms),
                "pages": list(hints.pages),
                "reason": hints.reason,
            },
            "methods": methods,
        }
        windows = {
            "A_text": (e["start"], e["end"], None),
            "A_batch": (e["start"], e["end"], ""),
            "C": (e["start"], e["end"], hints.context),
        }
        for future in [0, 5]:
            end = min(305, e["end"] + future)
            start = min(v["start"] for v in entries[: i + 1] if v["start"] >= max(-30, end - 30))
            windows[f"B{future}"] = (start, end, "")
            windows[f"D{future}"] = (start, end, hints.context)
        if pick["kind"] == "fixed" and pick["id"].endswith(("-1", "-2")):
            pages = [
                (page_draft_score(p["text"], word_tokens(context + " " + e["english"])), d, p)
                for d in materials["documents"]
                for p in d["pages"]
            ]
            pages.sort(key=lambda x: (x[0], x[1]["name"], x[2]["page"]))
            unrelated = []
            origin = None
            for _, doc, page in pages:
                candidates = [
                    t["english"]
                    for t in materials["hint_terms"]
                    if any(
                        s["document_id"] == doc["id"] and s["page"] == page["page"]
                        for s in t["sources"]
                    )
                    and t["english"].lower() not in (context + " " + e["english"]).lower()
                ]
                for term in candidates:
                    if len(", ".join(unrelated + [term])) <= 160:
                        unrelated.append(term)
                    if len(unrelated) == 6:
                        break
                if unrelated:
                    origin = {"name": doc["name"], "page": page["page"]}
                    break
            row["unrelated_origin"] = origin
            windows["U"] = (e["start"], e["end"], ", ".join(unrelated))
        cache = {}
        mt_cache = {}
        for method, (start, end, hint) in windows.items():
            key = (start, end, hint)
            if hint is None:
                r = {
                    "raw": e["english"],
                    "truncated": False,
                    "seconds": 0,
                    "start": start,
                    "end": end,
                    "hint": "",
                    "reused": True,
                }
            elif key in cache:
                r = dict(cache[key], reused=True)
            else:
                r = decode(start, end, hint)
                cache[key] = r
            if method in ["A_text", "A_batch", "C", "U"]:
                projection = {"text": r["raw"], "uncertain": []}
            else:
                prefix = " ".join(v["english"] for v in entries[:i] if v["start"] >= start)
                projection = project(prefix, e["english"], r["raw"])
                if start < 0 or any(v["start"] < start < v["end"] for v in entries[:i]):
                    projection["uncertain"].append("partial_or_untranscribed_prefix")
            r["projection"] = projection
            target = projection["text"]
            t = time.monotonic()
            for field, future_text in [("initial_chinese", ""), ("final_chinese", following)]:
                mt_key = (target, context, future_text)
                if mt_key not in mt_cache:
                    mt_cache[mt_key] = translator.translate(
                        target, context=context, following=future_text
                    )
                r[field] = mt_cache[mt_key]["text"]
                r[field + "_truncated"] = mt_cache[mt_key].get("truncated", False)
            r["mt_seconds"] = time.monotonic() - t
            methods[method] = r
        results.append(row)
        dump(output / f"{record['id']}-ablation.json", results)
        print(
            "CANDIDATE", pick["id"], "hints", len(hints.terms), "methods", len(methods), flush=True
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["baseline", "ablate"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--recording", choices=["R", "N", "E"])
    args = parser.parse_args()
    import mlx.core as mx

    if args.phase == "ablate":
        from functools import lru_cache

        from lectureflow import inference_policy

        # Pure-function caches only; same values, bounded research-process memory.
        inference_policy._fold = lru_cache(maxsize=16384)(inference_policy._fold)
        inference_policy._ratio = lru_cache(maxsize=8192)(inference_policy._ratio)
        inference_policy.word_tokens = lru_cache(maxsize=512)(inference_policy.word_tokens)
    from mlx_qwen3_asr import Session

    from lectureflow.adapters import HyMTTranslator

    config = json.loads(args.setup.read_text())
    manifest = json.loads((args.output / "manifest.json").read_text())
    locks = []
    for name in [".model.lock", ".live-priority.lock"]:
        handle = (Path(config["library"]) / name).open("rb")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locks.append(handle)
    models = Path(config["models"])

    def model(key):
        return models / json.loads((models / f"{key}.json").read_text())["path"]

    session = Timed(
        Session(str(model("asr-large"))),
        {"transcribe", "init_streaming", "feed_audio", "finish_streaming"},
    )
    mt = Timed(HyMTTranslator(model("translate")), {"translate"})
    session.transcribe(read_audio(manifest["recordings"][0]["audio"])[:80000], language="English")
    mt.translate("This is a test.")
    for record in manifest["recordings"]:
        if args.recording and record["id"] != args.recording:
            continue
        if args.phase == "baseline":
            baseline(record, session, mt, args.output)
        else:
            ablate(record, session, mt, args.output)
    dump(
        args.output / f"{args.phase}-runtime.json",
        {
            "rss_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "mlx_peak_bytes": mx.get_peak_memory(),
            "asr_calls": session.calls,
            "mt_calls": mt.calls,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )


if __name__ == "__main__":
    main()
