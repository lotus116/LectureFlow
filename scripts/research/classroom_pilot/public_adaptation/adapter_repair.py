"""Give each adapter the same hypothesis-only repetition fallback as the base model."""

import json
import sys
import wave
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from peft import PeftModel
from rapidfuzz.distance import Levenshtein
from score import tokens
from speaker_lora import read_pcm
from transformers import WhisperForConditionalGeneration, WhisperProcessor

src, exp, ckpt = map(Path, sys.argv[1:4])
arm = sys.argv[4]
torch.set_num_threads(2)
proc = WhisperProcessor.from_pretrained(ckpt, local_files_only=True)
base = WhisperForConditionalGeneration.from_pretrained(
    ckpt, local_files_only=True, attn_implementation="sdpa"
)
model = PeftModel.from_pretrained(base, exp / "duration" / (arm + "-adapter")).eval()
for kind in ("duration", "recitation"):
    folder = exp / kind
    ref = {
        r["id"]: r
        for r in json.loads((folder / "windows.json").read_text())
        if r.get("split", "test") == "test"
    }
    preds = {
        r["id"]: r
        for r in map(json.loads, (folder / (arm + "-predictions.jsonl")).read_text().splitlines())
        if r["id"] in ref
    }
    assert set(preds) == set(ref)
    with (
        torch.inference_mode(),
        (folder / (arm + "-repaired.jsonl")).open("x", buffering=1) as log,
    ):
        for id_, r in ref.items():
            hyp = preds[id_]["hypothesis"]
            ws = tokens(proc.tokenizer.normalize(hyp))
            trigger = any(
                n >= 3 for n in Counter(zip(*(ws[i:] for i in range(6)), strict=False)).values()
            )
            if trigger:
                if kind == "duration":
                    pcm = read_pcm(src, r)
                else:
                    with wave.open(str(folder / (id_ + ".wav"))) as w:
                        pcm = (
                            np.frombuffer(w.readframes(w.getnframes()), "<i2").astype("float32")
                            / 32768
                        )
                x = proc.feature_extractor(
                    pcm, sampling_rate=16000, return_tensors="pt"
                ).input_features
                gen = model.generate(
                    x,
                    do_sample=False,
                    num_beams=1,
                    max_new_tokens=96,
                    no_repeat_ngram_size=6,
                )
                hyp = proc.tokenizer.decode(gen[0], skip_special_tokens=True)
            ws = tokens(proc.tokenizer.normalize(r["text"]))
            e = Levenshtein.distance(ws, tokens(proc.tokenizer.normalize(hyp)))
            print(
                json.dumps(
                    dict(
                        id=id_,
                        lecture=r["lecture"],
                        triggered=trigger,
                        errors=e,
                        words=len(ws),
                        hypothesis=hyp,
                    )
                ),
                file=log,
            )
