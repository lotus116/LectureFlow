"""Post-hoc diagnostic for repeated phrases. Use hypotheses alone to trigger one redecode."""

import json
import sys
import wave
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from rapidfuzz.distance import Levenshtein
from score import tokens
from speaker_lora import read_pcm
from transformers import WhisperForConditionalGeneration, WhisperProcessor

raw, exp, ckpt = map(Path, sys.argv[1:])
processor = WhisperProcessor.from_pretrained(ckpt, local_files_only=True)
torch.set_num_threads(2)
model = WhisperForConditionalGeneration.from_pretrained(
    ckpt, local_files_only=True, attn_implementation="sdpa"
).eval()
for folder in ("duration", "recitation"):
    directory = exp / folder
    refs = {r["id"]: r for r in json.loads((directory / "windows.json").read_text())}
    baseline = [
        json.loads(s) for s in (directory / "base-predictions.jsonl").read_text().splitlines()
    ]
    baseline = [r for r in baseline if refs[r["id"]].get("split", "test") == "test"]
    assert len(baseline) == (48 if folder == "duration" else 24)

    def repeated(hyp):
        ws = tokens(processor.tokenizer.normalize(hyp))
        return any(n >= 3 for n in Counter(zip(*(ws[i:] for i in range(6)), strict=False)).values())

    mask = [r["id"] for r in baseline if repeated(r["hypothesis"])]
    (directory / "repetition-mask.json").write_text(json.dumps(mask))
    with (
        torch.inference_mode(),
        (directory / "repetition-predictions.jsonl").open("x", buffering=1) as log,
    ):
        for r in baseline:
            id_ = r["id"]
            row = refs[id_]
            hyp = r["hypothesis"]
            if id_ in mask:
                if folder == "duration":
                    pcm = read_pcm(raw, row)
                else:
                    with wave.open(str(directory / (id_ + ".wav"))) as w:
                        pcm = (
                            np.frombuffer(w.readframes(w.getnframes()), "<i2").astype("float32")
                            / 32768
                        )
                x = processor.feature_extractor(
                    pcm, sampling_rate=16000, return_tensors="pt"
                ).input_features
                y = model.generate(
                    x,
                    do_sample=False,
                    num_beams=1,
                    max_new_tokens=96,
                    no_repeat_ngram_size=6,
                )
                hyp = processor.tokenizer.decode(y[0], skip_special_tokens=True)
            ref = tokens(processor.tokenizer.normalize(row["text"]))
            e = Levenshtein.distance(ref, tokens(processor.tokenizer.normalize(hyp)))
            print(
                json.dumps(
                    dict(
                        id=id_,
                        lecture=row["lecture"],
                        hypothesis=hyp,
                        triggered=id_ in mask,
                        errors=e,
                        words=len(ref),
                        base_errors=r["errors"],
                    )
                ),
                file=log,
            )
