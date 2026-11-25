"""Decode fixed public segments with no hints, matching abstract hints and swapped hints."""

import argparse
import csv
import hashlib
import io
import json
import math
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from acl_data import talks
from score import report, tokens


def selected_indices(talk):
    return [len(talk.ids) // 3, 2 * len(talk.ids) // 3]


def hints_for_talks(items):
    bags = [Counter(w for w in tokens(t.abstract) if len(w) >= 4) for t in items]
    frequency = Counter(w for bag in bags for w in bag)
    result = []
    for bag in bags:

        def rank(word, term_counts=bag):
            return (-term_counts[word] * math.log((len(items) + 1) / frequency[word]), word)

        result.append(sorted(bag, key=rank)[:4])
    return result


def recognizer(model, hints, threads, *, max_new_tokens=128):
    import sherpa_onnx

    return sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
        conv_frontend=str(model / "conv_frontend.onnx"),
        encoder=str(model / "encoder.int8.onnx"),
        decoder=str(model / "decoder.int8.onnx"),
        tokenizer=str(model / "tokenizer"),
        hotwords=",".join(hints),
        num_threads=threads,
        seed=42,
        max_new_tokens=max_new_tokens,
    )


def transcribe(engine, samples, rate, language=None):
    stream = engine.create_stream()
    if language:
        stream.set_option("language", language)
    stream.accept_waveform(rate, samples)
    engine.decode_stream(stream)
    return stream.result.text


def run(archive, model, output, threads, language=None):
    import soundfile as sf

    items = list(talks(archive))
    hints = hints_for_talks(items)
    rows = []
    for index, talk in enumerate(items):
        clips = []
        rows_here = []
        for pos in selected_indices(talk):
            sent = talk.ids[pos]
            name = f"2/acl_6060/{talk.split}/segmented_wavs/gold/sent_{sent}.wav"
            audio = archive.read(name)
            samples, rate = sf.read(io.BytesIO(audio), dtype="float32")
            if samples.ndim != 1:
                raise ValueError(f"Expected mono in {name}")
            clips.append((samples, rate))
            rows_here.append(
                dict(
                    recording=talk.paper,
                    segment=str(sent),
                    status="scored",
                    reference=talk.references[pos],
                    terms="|".join(hints[index]),
                    seconds=len(samples) / rate,
                    audio_sha256=hashlib.sha256(audio).hexdigest(),
                )
            )
        for mode, words in (
            ("plain", []),
            ("matching", hints[index]),
            ("swapped", hints[(index + 1) % len(items)]),
        ):
            engine = recognizer(model, words, threads)
            for row, (samples, rate) in zip(rows_here, clips, strict=True):
                row[mode] = transcribe(engine, samples, rate, language)
            del engine
        rows.extend(rows_here)
        print(f"{index + 1}/{len(items)} {talk.paper}", flush=True)
    with (output / "predictions.csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    summary = {"duration_s": sum(r["seconds"] for r in rows), "recordings": len(items)}
    for mode in ("matching", "swapped"):
        summary[mode] = report(rows, "plain", mode)
    (output / "audio_summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("model", type=Path)
    parser.add_argument("output", type=Path, help="new private output directory")
    parser.add_argument("--threads", type=int, default=3)
    parser.add_argument("--language", choices=["English"], help="Otherwise infer language")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with ZipFile(args.archive) as archive:
        run(archive, args.model, args.output, args.threads, args.language)
