"""Prospective public SlideSpeech transfer test with a causal, short-block English draft."""
import argparse
import hashlib
import io
import itertools
import json
import tarfile
import time
import wave
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import numpy as np

from audio_probe import recognizer, transcribe
from score import errors, tokens

RATE = 16000
WINDOWS = 10
KEEP = 4
SEED = "LectureFlow-SlideSpeech-dev-transfer-20261126"
RESULTS = Path(__file__).resolve().parents[3] / "docs/research/results"


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def save(path, data):
    with path.open("x") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def choose_span(rows):
    # Prefer an interior excerpt. Eligibility uses times only.
    rows = sorted(rows)
    for i in sorted(range(len(rows)), key=lambda k: (abs(k - len(rows) // 5), k)):
        first = rows[i][0]
        if i and rows[i - 1][1] > first:
            continue
        covered = 0
        prev = first
        for j in range(i, len(rows)):
            start, end, _ = rows[j]
            if start < prev or start - prev > 3 * RATE:
                break
            covered += end - start
            prev = end
            span = end - first
            if span > 115 * RATE:
                break
            if span >= 90 * RATE and covered / span >= 0.85:
                if j + 1 == len(rows) or rows[j + 1][0] >= end:
                    return first, end, [r[2] for r in rows[i:j + 1]], covered
    return None


def prepare(folder, archive):
    if digest(archive) != "b647e3240b35f6c618c169b20ac1280d9a972c4c44c1185192aa86be058d8bba":
        raise ValueError("Unexpected SlideSpeech dev archive")
    with tarfile.open(folder / "wavid2channel.tar.gz") as mapping:
        ids = json.load(mapping.extractfile("data/dev/wavid2channel.json"))
    earlier = set()
    for filename in ("2026-11-25-slidespeech.json", "2026-11-25-slidespeech-selective.json"):
        old = json.loads((RESULTS / filename).read_text())
        rows = old["baselines"]["rows"] if "baselines" in old else old["rows"]
        earlier.update(row["video_id"] for row in rows)
    assert len(earlier) == 12
    segments = defaultdict(list)
    for line in (folder / "dev/segments").read_text().splitlines():
        uid, name, lo, hi = line.split()
        segments[name].append((int(Decimal(lo) * RATE), int(Decimal(hi) * RATE), uid))
    paths = dict(line.split() for line in (folder / "dev/wav.scp").read_text().splitlines())
    chosen = {}
    for name in sorted(ids, key=lambda n: hashlib.sha256(f"{SEED}:{n}".encode()).digest()):
        if ids[name] in earlier or ids[name] in {v["video"] for v in chosen.values()}:
            continue
        span = choose_span(segments[name])
        if span:
            lo, hi, uids, covered = span
            chosen[name] = dict(video=ids[name], member=paths[name], lo=lo, hi=hi,
                                utterances=uids, annotated_frames=covered)
        if len(chosen) == 4:
            break
    if len(chosen) != 4:
        raise ValueError(f"Only {len(chosen)} eligible new sources")
    with tarfile.open(archive) as tar:
        for name, row in chosen.items():
            data = tar.extractfile(row["member"]).read()
            with wave.open(io.BytesIO(data)) as wav:
                assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (RATE, 1, 2)
                wav.setpos(row["lo"])
                raw = wav.readframes(row["hi"] - row["lo"])
            assert len(raw) == 2 * (row["hi"] - row["lo"])
            (folder / (name + ".pcm")).write_bytes(raw)
            row["pcm_sha256"] = hashlib.sha256(raw).hexdigest()
    save(folder / "sources.json", chosen)


def rollback(early, final):
    prefix = 0
    for a, b in zip(early, final):
        if a != b:
            break
        prefix += 1
    return len(early) - prefix


def run(folder, model):
    sources = json.loads((folder / "sources.json").read_text())
    engine = recognizer(model, [], 2)
    samples = {}
    for name, row in sources.items():
        path = folder / (name + ".pcm")
        assert digest(path) == row["pcm_sha256"]
        pcm = np.fromfile(path, "<i2").astype("float32") / 32768
        # Under ten frames of trailing zero padding equalize the ten windows.
        samples[name] = np.array_split(np.pad(pcm, (0, (-len(pcm)) % WINDOWS)), WINDOWS)
    transcribe(engine, next(iter(samples.values()))[0][:2*RATE], RATE, "English")
    with (folder / "calls.jsonl").open("x", buffering=1) as log:
        def call(name, index, phase, pcm):
            start = time.perf_counter()
            text = transcribe(engine, pcm, RATE, "English")
            seconds = time.perf_counter() - start
            print(json.dumps(dict(source=name, window=index, phase=phase, seconds=seconds, text=text)), file=log)
            return tokens(text)

        selected = {}
        for name, windows in samples.items():
            churn = []
            for i, window in enumerate(windows):
                count = 0
                for h, half in enumerate(np.array_split(window, 2)):
                    early = call(name, i, f"preview{h}", half[:len(half)//2])
                    final = call(name, i, f"draft{h}", half)
                    count += rollback(early, final)
                churn.append(count)
            selected[name] = {"retracted_words": churn,
                              "selected": sorted(sorted(range(WINDOWS), key=lambda k: (-churn[k], k))[:KEEP])}
            print(name, "draft complete", flush=True)
        save(folder / "selected-before-candidates.json", selected)
        for name, windows in samples.items():
            for i, window in enumerate(windows):
                call(name, i, "candidate", window)
            print(name, "candidates complete", flush=True)


def score(folder):
    from rapidfuzz.distance import Levenshtein
    sources = json.loads((folder / "sources.json").read_text())
    selected = json.loads((folder / "selected-before-candidates.json").read_text())
    records = [json.loads(line) for line in (folder / "calls.jsonl").read_text().splitlines()]
    calls = {(r["source"], r["window"], r["phase"]): r for r in records}
    assert len(calls) == len(records) == 50 * len(sources)
    refs = dict(line.split(maxsplit=1) for line in (folder / "dev/text").read_text().splitlines() if line.strip())
    out = {}
    for name, row in sources.items():
        ref = tokens(" ".join(refs[u] for u in row["utterances"]))
        draft = [tuple(itertools.chain.from_iterable(tokens(calls[name,i,f"draft{h}"]["text"]) for h in range(2))) for i in range(WINDOWS)]
        full = [tokens(calls[name,i,"candidate"]["text"]) for i in range(WINDOWS)]
        def dist(mask):
            hyp = tuple(itertools.chain.from_iterable(full[i] if i in mask else draft[i] for i in range(WINDOWS)))
            return Levenshtein.distance(ref, hyp)
        mask = selected[name]["selected"]
        random_scores = [dist(m) for m in itertools.combinations(range(WINDOWS), KEEP)]
        ds, fs, ss = dist(()), dist(range(WINDOWS)), dist(mask)
        assert ds == errors(ref, sum(draft, ())) and fs == errors(ref, sum(full, ()))
        out[name] = dict(video=row["video"], start_frame=row["lo"], end_frame=row["hi"],
                         speech_interval_s=(row["hi"]-row["lo"])/RATE, words=len(ref),
                         draft=ds, full=fs, selected=ss, **{"frozen_mask": mask},
                         churn=selected[name]["retracted_words"],
                         random_sum=sum(random_scores), random_n=len(random_scores),
                         oracle_min_at_same_budget=min(random_scores), random_max=max(random_scores),
                         random_mean=sum(random_scores)/len(random_scores),
                         random_better_equal=sum(v<=ss for v in random_scores),
                         candidate_decode_full_s=sum(calls[name,i,"candidate"]["seconds"] for i in range(WINDOWS)),
                         candidate_decode_selected_s=sum(calls[name,i,"candidate"]["seconds"] for i in mask),
                         preview_decode_s=sum(calls[name,i,f"preview{h}"]["seconds"] for i in range(WINDOWS) for h in range(2)),
                         draft_decode_s=sum(calls[name,i,f"draft{h}"]["seconds"] for i in range(WINDOWS) for h in range(2)))
    return {"sources_sha256": digest(folder/"sources.json"), "mask_sha256": digest(folder/"selected-before-candidates.json"),
            "calls_sha256": digest(folder/"calls.jsonl"), "scored_calls": len(calls), "by_video": out}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("stage", choices=("prepare","run","score"))
    cli.add_argument("folder", type=Path)
    cli.add_argument("asset", type=Path, nargs="?")
    args = cli.parse_args()
    if args.stage == "prepare":
        prepare(args.folder, args.asset)
    elif args.stage == "run":
        run(args.folder, args.asset)
    else:
        print(json.dumps(score(args.folder), indent=2))
