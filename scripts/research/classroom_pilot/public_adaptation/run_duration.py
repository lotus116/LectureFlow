"""Train nested publisher-caption adapters at identical optimizer budgets."""

import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from rapidfuzz.distance import Levenshtein
from score import tokens
from speaker_lora import read_pcm
from transformers import WhisperForConditionalGeneration, WhisperProcessor

src, ckpt, out = map(Path, sys.argv[1:4])
pseudo = sys.argv[4:] == ["--teacher"]
if sys.argv[4:] and not pseudo:
    raise ValueError("Unexpected option")
torch.set_num_threads(3)
proc = WhisperProcessor.from_pretrained(ckpt, local_files_only=True)
rows = json.loads((out / "windows.json").read_text())
features = [
    proc.feature_extractor(
        read_pcm(src, r), sampling_rate=16000, return_tensors="pt"
    ).input_features[0]
    for r in rows
]
labels = [torch.tensor(proc.tokenizer(r["text"]).input_ids[1:]) for r in rows]


def load():
    return WhisperForConditionalGeneration.from_pretrained(
        ckpt, local_files_only=True, attn_implementation="sdpa"
    )


def normalize(s):
    return tokens(proc.tokenizer._normalize(s))


def batch_xy(batch):
    return torch.stack([features[i] for i in batch]), torch.nn.utils.rnn.pad_sequence(
        [labels[i] for i in batch], batch_first=True, padding_value=-100
    )


train_indices = [i for i, r in enumerate(rows) if r["split"] == "train"]
probe = [i for i in train_indices if rows[i]["min_budget"] == 10][:12]
test = [i for i, r in enumerate(rows) if r["split"] == "test"]


def evaluate(model, arm):
    stats = defaultdict(lambda: defaultdict(float))
    model.eval()
    with (
        torch.inference_mode(),
        (out / f"{arm}-predictions.jsonl").open("x", buffering=1) as f,
    ):
        for i in test + probe:
            x = features[i][None]
            y = torch.tensor(proc.tokenizer(rows[i]["text"]).input_ids[1:])[None]
            pred = model.generate(x, do_sample=False, num_beams=1, max_new_tokens=96)
            hyp = proc.tokenizer.decode(pred[0], skip_special_tokens=True)
            ref, heard = normalize(rows[i]["text"]), normalize(hyp)
            loss = float(model(input_features=x, labels=y, use_cache=False).loss)
            ops = Levenshtein.editops(ref, heard)
            r = dict(
                id=rows[i]["id"],
                split=rows[i]["split"],
                lecture=rows[i]["lecture"],
                hypothesis=hyp,
                reference_norm=ref,
                hypothesis_norm=heard,
                errors=len(ops),
                words=len(ref),
                loss=loss,
                tokens=y.numel(),
                basic_errors=Levenshtein.distance(tokens(rows[i]["text"]), tokens(hyp)),
                basic_words=len(tokens(rows[i]["text"])),
            )
            print(json.dumps(r), file=f)
            g = stats["train_probe" if i in probe else "test_L" + str(rows[i]["lecture"])]
            for k in ("errors", "words", "basic_errors", "basic_words", "tokens"):
                g[k] += r[k]
            g["loss_sum"] += loss * y.numel()
            g["clips"] += 1
    for g in stats.values():
        g["mean_token_loss"] = g["loss_sum"] / g["tokens"]
    (out / f"{arm}-metrics.json").write_text(json.dumps(stats, indent=2))
    print(arm, dict(stats), flush=True)


if not (out / "base-metrics.json").exists():
    evaluate(load(), "base")
if pseudo:
    teacher_rows = [
        json.loads(line) for line in (out / "teacher-train.jsonl").read_text().splitlines()
    ]
    texts = {r["id"]: r["hypothesis"] for r in teacher_rows}
    assert len(texts) == len(teacher_rows) == len(train_indices)
    for i in train_indices:
        labels[i] = torch.tensor(proc.tokenizer(texts[rows[i]["id"]]).input_ids[1:])
for budget in (60,) if pseudo else (10, 30, 60):
    arm = f"lora-{budget}" + ("-teacher" if pseudo else "")
    if (out / f"{arm}-metrics.json").exists():
        continue
    train = [i for i in train_indices if rows[i]["min_budget"] <= budget]
    steps = 192
    assert len(train) <= steps * 2
    rng = random.Random(26)
    schedule = []
    while len(schedule) < steps * 2:
        order = train.copy()
        rng.shuffle(order)
        schedule.extend(order)
    schedule = schedule[: steps * 2]
    assert set(schedule) == set(train)
    torch.manual_seed(26)
    model = get_peft_model(
        load(),
        LoraConfig(
            r=4,
            lora_alpha=8,
            lora_dropout=0,
            target_modules=["q_proj", "v_proj"],
            bias="none",
        ),
    )
    params = [p for p in model.parameters() if p.requires_grad]
    op = torch.optim.AdamW(params, lr=1e-4)
    model.train()
    (out / f"{arm}-config.json").write_text(
        json.dumps(
            dict(
                seed=26,
                steps=steps,
                batch=2,
                lr=1e-4,
                trainable=sum(p.numel() for p in params),
                source_commit=(ckpt / "SOURCE_COMMIT").read_text().strip(),
                train_ids=[rows[i]["id"] for i in train],
                schedule=[rows[i]["id"] for i in schedule],
                actual_seconds=sum(rows[i]["end"] - rows[i]["start"] for i in train),
                torch=str(torch.__version__),
            ),
            indent=2,
        )
    )
    start = time.monotonic()
    with (out / f"{arm}-training.jsonl").open("x", buffering=1) as log:
        for step in range(steps):
            batch = schedule[2 * step : 2 * step + 2]
            x, y = batch_xy(batch)
            op.zero_grad(set_to_none=True)
            loss = model(input_features=x, labels=y, use_cache=False).loss
            if not torch.isfinite(loss):
                raise ValueError("nonfinite loss")
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_(params, 1.0)
            op.step()
            print(
                json.dumps(
                    dict(
                        step=step + 1,
                        loss=loss.item(),
                        grad_norm=float(norm),
                        elapsed_s=time.monotonic() - start,
                    )
                ),
                file=log,
            )
    model.save_pretrained(out / f"{arm}-adapter")
    evaluate(model, arm)
    del model, op
