"""Compare supervised and pseudo-labeled Whisper LoRA on fixed lecture-held-out audio."""

import argparse
import json
import random
import wave
from collections import defaultdict
from pathlib import Path
from time import perf_counter

import numpy as np
import torch
from peft import LoraConfig, get_peft_model
from score import errors, tokens
from transformers import WhisperForConditionalGeneration, WhisperProcessor

SEED = 26


def read_pcm(folder, row):
    with wave.open(str(folder / f"lec{row['lecture']}.wav")) as wav:
        lo, hi = round(16000 * row["start"]), round(16000 * row["end"])
        wav.setpos(lo)
        raw = wav.readframes(hi - lo)
        assert wav.getframerate() == 16000 and len(raw) == 2 * (hi - lo)
        return np.frombuffer(raw, "<i2").astype("float32") / 32768


def fit_and_score(folder, checkpoint, out, device):
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("Requested CUDA device is unavailable")
    if device.type == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("Requested Apple MPS device is unavailable")
    torch.set_num_threads(3)
    proc = WhisperProcessor.from_pretrained(checkpoint, local_files_only=True)
    rows = json.loads((folder / "split.json").read_text())
    train = [i for i, r in enumerate(rows) if r["split"] == "train"]
    assert len(train) == 48 and {rows[i]["lecture"] for i in train} == {2, 3}
    assert {r["lecture"] for r in rows if r["split"] == "test"} == {5}
    features = [
        proc.feature_extractor(
            read_pcm(folder, r), sampling_rate=16000, return_tensors="pt"
        ).input_features[0]
        for r in rows
    ]

    def evaluate(model, arm):
        stats = defaultdict(lambda: defaultdict(int))
        model.eval()
        with torch.inference_mode(), (out / f"{arm}-predictions.jsonl").open("x") as log:
            for i, r in enumerate(rows):
                if r["split"] == "train":
                    continue
                pred = model.generate(
                    features[i][None].to(device), do_sample=False, num_beams=1, max_new_tokens=96
                )
                hyp = proc.tokenizer.decode(pred[0], skip_special_tokens=True)
                print(
                    json.dumps(dict(lecture=r["lecture"], start=r["start"], hypothesis=hyp)),
                    file=log,
                )
                acc = stats[r["split"]]
                for name, norm in (("basic", lambda s: s), ("english", proc.tokenizer._normalize)):
                    ref, heard = tokens(norm(r["text"])), tokens(norm(hyp))
                    acc[name + "_words"] += len(ref)
                    acc[name + "_errors"] += errors(ref, heard)
                acc["windows"] += 1
        return dict(stats)

    def load():
        return WhisperForConditionalGeneration.from_pretrained(
            checkpoint, local_files_only=True, attn_implementation="sdpa"
        ).to(device)

    base = load()
    results = {
        "checkpoint": (checkpoint / "SOURCE_COMMIT").read_text().strip(),
        "device": str(device),
        "torch": str(torch.__version__),
        "splits": {},
        "arms": {},
    }
    for split in ("train", "dev", "test"):
        subset = [r for r in rows if r["split"] == split]
        results["splits"][split] = {
            "windows": len(subset),
            "seconds": sum(r["end"] - r["start"] for r in subset),
        }
    results["arms"]["base"] = {"evaluation": evaluate(base, "base")}
    del base
    for arm in ("publisher", "pseudo"):
        if arm == "pseudo":
            pseudo_rows = [
                json.loads(s) for s in (folder / "pseudo.jsonl").read_text().splitlines()
            ]
            pseudo = {(r["lecture"], r["start"]): r["pseudo"] for r in pseudo_rows}
            assert len(pseudo) == len(train)
        labels = {}
        for i in train:
            r = rows[i]
            text = r["text"] if arm == "publisher" else pseudo[r["lecture"], r["start"]]
            # Decoder start is supplied during the label shift.
            labels[i] = torch.tensor(proc.tokenizer(text).input_ids[1:], dtype=torch.long)
        torch.manual_seed(SEED)
        model = get_peft_model(
            load(),
            LoraConfig(
                r=4, lora_alpha=8, lora_dropout=0, target_modules=["q_proj", "v_proj"], bias="none"
            ),
        )
        model.train()
        optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=1e-4)
        steps = 0
        start = perf_counter()
        for epoch in range(2):
            order = train.copy()
            random.Random(SEED + epoch).shuffle(order)
            for k in range(0, len(order), 2):
                batch = order[k : k + 2]
                x = torch.stack([features[i] for i in batch]).to(device)
                y = torch.nn.utils.rnn.pad_sequence(
                    [labels[i] for i in batch], batch_first=True, padding_value=-100
                ).to(device)
                optimizer.zero_grad(set_to_none=True)
                loss = model(input_features=x, labels=y, use_cache=False).loss
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite training loss")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                steps += 1
            print(arm, "epoch", epoch + 1, "steps", steps, flush=True)
        model.save_pretrained(out / (arm + "-adapter"))
        results["arms"][arm] = {
            "train_steps": steps,
            "fit_seconds": perf_counter() - start,
            "trainable_parameters": sum(p.numel() for p in model.parameters() if p.requires_grad),
            "evaluation": evaluate(model, arm),
        }
        (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        del model, optimizer
    return results


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("data", type=Path)
    cli.add_argument("checkpoint", type=Path)
    cli.add_argument("output", type=Path)
    cli.add_argument("--device", required=True, choices=["cpu", "cuda", "mps"])
    a = cli.parse_args()
    a.output.mkdir(exist_ok=False, parents=True)
    fit_and_score(a.data, a.checkpoint, a.output, torch.device(a.device))
