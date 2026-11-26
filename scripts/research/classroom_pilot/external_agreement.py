"""Score published predictions in the pinned ASR Evaluation checkout, without inference."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from score import errors, tokens

REV = "6aba1e38927578a68b968daca4de426a0534a419"
SCENARIOS = ("clean_read", "natural_hard", "accented_english", "babble_10db")


def by_id(rows):
    indexed = {row["clip_id"]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError("Duplicate source clip ID")
    return indexed


def summarize(rows):
    words = sum(r["words"] for r in rows)
    error_count = sum(r["errors"] for r in rows)
    return {
        "clips": len(rows), "audio_seconds": round(sum(r["seconds"] for r in rows), 3),
        "reference_words": words, "whisper_errors": error_count,
        "wer_pct": round(100 * error_count / words, 2) if words else None,
        "error_free_clips": sum(r["errors"] == 0 for r in rows),
    }


def sensitivity_words(text):
    text = re.sub(r"<[^<>]+>", " ", text)
    return tuple(word for word in tokens(text) if word not in {"uh", "um"})


def analyze(checkout):
    if subprocess.check_output(["git", "-C", checkout, "rev-parse", "HEAD"], text=True).strip() != REV:
        raise ValueError("Expected the pinned public release")
    hashes = {}

    def read(path):
        data = (checkout / path).read_bytes()
        committed = subprocess.check_output(["git", "-C", checkout, "show", f"{REV}:{path}"])
        if data != committed:
            raise ValueError(f"Changed input: {path}")
        hashes[path] = hashlib.sha256(data).hexdigest()
        return data

    manifest = by_id(json.loads(read("data/manifest.json"))["clips"])
    result = {}
    sources = {}
    for scenario in SCENARIOS:
        outputs = {}
        for model in ("Whisper", "Canary"):
            lines = read(f"results-gpu/raw/{model}/{scenario}.jsonl").splitlines()
            outputs[model] = by_id([json.loads(line) for line in lines])
        ids = {key for key, row in manifest.items() if row["scenario"] == scenario}
        if not ids or not ids == outputs["Whisper"].keys() == outputs["Canary"].keys():
            raise ValueError(f"Incomplete pairing: {scenario}")
        rows = []
        edacc_sensitivity = []
        for key in sorted(ids):
            clip = manifest[key]
            a, b = (outputs[model][key] for model in ("Whisper", "Canary"))
            if any(r["scenario"] != scenario or r["reference"] != clip["reference"] for r in (a, b)):
                raise ValueError(f"Source reference or scenario mismatch: {key}")
            label, check = tokens(a["hypothesis"]), tokens(b["hypothesis"])
            # Decide acceptance before comparing either text to the reference.
            keep = bool(label) and label == check
            ref = tokens(clip["reference"])
            rows.append({
                "clip": key, "source_id": clip["source_utterance_id"], "seconds": clip["duration_s"],
                "words": len(ref), "errors": errors(ref, label), "agree": keep,
            })
            if scenario == "accented_english":
                clean_ref = sensitivity_words(clip["reference"])
                edacc_sensitivity.append({
                    **rows[-1], "words": len(clean_ref),
                    "errors": errors(clean_ref, sensitivity_words(a["hypothesis"])),
                })
        accepted = [r for r in rows if r["agree"]]
        rejected = [r for r in rows if not r["agree"]]
        result[scenario] = {"all": summarize(rows), "agree": summarize(accepted),
                            "disagree": summarize(rejected)}
        if edacc_sensitivity:
            result[scenario]["posthoc_no_events_uh_um_same_mask"] = {
                "all": summarize(edacc_sensitivity),
                "agree": summarize([r for r in edacc_sensitivity if r["agree"]]),
            }
        sources[scenario] = {(manifest[k]["source_utterance_id"], manifest[k]["reference"]) for k in ids}
    if len(sources["clean_read"]) != result["clean_read"]["all"]["clips"] or sources["clean_read"] != sources["babble_10db"]:
        raise ValueError("Clean and babble conditions must use the same source speech")
    return {"source_commit": REV, "input_sha256": hashes, "scenarios": result}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("checkout", type=Path)
    print(json.dumps(analyze(cli.parse_args().checkout), indent=2))
