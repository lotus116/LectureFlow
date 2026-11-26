"""Hold topic similar and change lecturer/recording. Freeze samples using early clock anchors."""

import argparse
import hashlib
import json
import wave
from pathlib import Path

import numpy as np
from audio_probe import recognizer, transcribe
from mit_speaker_data import anchor_rate, audio, cues, tokens
from rapidfuzz.distance import Levenshtein


def prepare(src, out, teacher):
    engine = recognizer(teacher, [], 2)
    windows = []
    for lec in (1, 2):
        rows = cues(src / f"lec{lec}.vtt")
        words = [
            (lo + (i + 0.5) * (hi - lo) / len(tokens(txt)), w)
            for lo, hi, txt in rows
            for i, w in enumerate(tokens(txt))
        ]
        offsets = np.arange(-20, 20.1, 0.25)
        scores = []
        logs = []
        for t in (24, 54, 84, 114):
            hyp = transcribe(engine, audio(src, lec, t, t + 12), 16000, "English")
            lookup = []
            for off in offsets:
                ref = [w for sec, w in words if t <= sec + off < t + 12]
                lookup.append((Levenshtein.distance(ref, tokens(hyp)), len(ref)))
            scores.append(lookup)
            logs.append(dict(start=t, hypothesis=hyp))
        best = min(
            range(len(offsets)),
            key=lambda i: (anchor_rate(scores, i, range(3)), abs(offsets[i])),
        )
        off = float(offsets[best])
        cal = dict(
            offset=off,
            anchor_wer=anchor_rate(scores, best, range(3)),
            heldout_anchor=anchor_rate(scores, best, [3]),
            anchors=logs,
        )
        (out / f"clock{lec}.json").write_text(json.dumps(cal, indent=2))
        if cal["anchor_wer"] > 0.55 or cal["heldout_anchor"] > 0.60 or abs(off) >= 19.75:
            raise ValueError(f"Clock unsupported: {lec} {cal}")
        pool = []
        i = 0
        while i < len(rows):
            a = i
            i += 1
            if rows[a][0] < 150 or rows[a][0] > rows[-1][1] - 20:
                continue
            while (
                i < len(rows)
                and rows[i - 1][1] - rows[a][0] < 9
                and 0 <= rows[i][0] - rows[i - 1][1] <= 1.5
            ):
                i += 1
            lo, hi = rows[a][0] + off, rows[i - 1][1] + off
            if 6 <= hi - lo <= 15:
                pool.append(
                    dict(
                        lecture=lec,
                        id=f"R{lec}-{a}",
                        start=lo,
                        end=hi,
                        text=" ".join(r[2] for r in rows[a:i]),
                    )
                )
        pool.sort(key=lambda r: hashlib.sha256(("lf-topic26" + r["id"]).encode()).digest())
        if len(pool) < 6:
            raise ValueError(f"Too few recitation windows in {lec}")
        for r in pool[:12]:
            with wave.open(str(src / f"lec{lec}.wav")) as w:
                a, b = round(16000 * r["start"]), round(16000 * r["end"])
                w.setpos(a)
                pcm = w.readframes(b - a)
                assert len(pcm) == 2 * (b - a)
            r["pcm_sha256"] = hashlib.sha256(pcm).hexdigest()
            with wave.open(str(out / (r["id"] + ".wav")), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(pcm)
            windows.append(r)
    (out / "windows.json").write_text(json.dumps(windows, indent=2))


def evaluate(out, ckpt, adapter):
    import torch
    from peft import PeftModel
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    torch.set_num_threads(2)
    proc = WhisperProcessor.from_pretrained(ckpt, local_files_only=True)
    model = WhisperForConditionalGeneration.from_pretrained(
        ckpt, local_files_only=True, attn_implementation="sdpa"
    )
    arm = "base"
    if adapter is not None:
        model = PeftModel.from_pretrained(model, adapter)
        arm = adapter.name.removesuffix("-adapter")
    model.eval()
    with (
        torch.inference_mode(),
        (out / f"{arm}-predictions.jsonl").open("x", buffering=1) as f,
    ):
        for r in json.loads((out / "windows.json").read_text()):
            with wave.open(str(out / (r["id"] + ".wav"))) as w:
                raw = w.readframes(w.getnframes())
            assert hashlib.sha256(raw).hexdigest() == r["pcm_sha256"]
            x = proc.feature_extractor(
                np.frombuffer(raw, "<i2").astype("float32") / 32768,
                sampling_rate=16000,
                return_tensors="pt",
            ).input_features
            pred = model.generate(x, do_sample=False, num_beams=1, max_new_tokens=96)
            hyp = proc.tokenizer.decode(pred[0], skip_special_tokens=True)
            ref = tokens(proc.tokenizer.normalize(r["text"]))
            heard = tokens(proc.tokenizer.normalize(hyp))
            print(
                json.dumps(
                    dict(
                        id=r["id"],
                        lecture=r["lecture"],
                        hypothesis=hyp,
                        errors=Levenshtein.distance(ref, heard),
                        words=len(ref),
                    )
                ),
                file=f,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "eval"))
    parser.add_argument("source", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("model", type=Path)
    parser.add_argument("--adapter", type=Path)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.source, args.out, args.model)
    else:
        evaluate(args.out, args.model, args.adapter)
