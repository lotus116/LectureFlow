"""Decode the base model on the full training pool to study the editing budget."""

import json
import sys
from pathlib import Path

import torch
from speaker_lora import read_pcm
from transformers import WhisperForConditionalGeneration, WhisperProcessor

src, ckpt, out = map(Path, sys.argv[1:])
torch.set_num_threads(2)
proc = WhisperProcessor.from_pretrained(ckpt, local_files_only=True)
model = WhisperForConditionalGeneration.from_pretrained(
    ckpt, local_files_only=True, attn_implementation="sdpa"
).eval()
with (
    torch.inference_mode(),
    (out / "base-train-pool.jsonl").open("x", buffering=1) as log,
):
    for r in json.loads((out / "windows.json").read_text()):
        if r["split"] != "train":
            continue
        x = proc.feature_extractor(
            read_pcm(src, r), sampling_rate=16000, return_tensors="pt"
        ).input_features
        pred = model.generate(x, do_sample=False, num_beams=1, max_new_tokens=96)
        hyp = proc.tokenizer.decode(pred[0], skip_special_tokens=True)
        print(json.dumps(dict(id=r["id"], hypothesis=hyp)), file=log)
