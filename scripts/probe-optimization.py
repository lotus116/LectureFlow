"""CPU-only diagnostics: hint relevance and current-caption translation waiting.

Uses synthetic audio and fake model timing, never a microphone or user library.
Optional --public-study replays stored public ACL drafts; it does not run models.
Compares the committed baseline against the current production scheduler.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lectureflow import inference_policy, knowledge, live

BASELINE = "e75db974ce4ba07b586db007ed542e564d070780"


def load_baseline(name):
    root = Path(__file__).resolve().parent.parent
    source = subprocess.check_output(
        ["git", "show", f"{BASELINE}:src/lectureflow/{name}.py"], cwd=root, text=True
    )
    module = types.ModuleType(f"lectureflow._{name}_baseline")
    module.__package__ = "lectureflow"
    # dataclasses resolves annotations through the module registry.
    import sys
    sys.modules[module.__name__] = module
    exec(compile(source, f"<committed {name} baseline>", "exec"), module.__dict__)
    return module


def synthetic_hints(baseline):
    names = ["Reversing Engineering", "Windows binary", "Windows Internals",
             "Export Directory", "VirtualMemory", "CreateFile", "MemoryProtection"]
    terms = [{"english": name, "confirmed": True, "score": 8} for name in names]
    queries = ["We are in this example.", "I will be in the room.",
               "We use virtural memry and create file in the kernel",
               "Export Directory", "Reversing Engineering", "Use export directry",
               "MemoryProtection"]
    return [{"draft": draft, **{
        mode: list(module.select_asr_hints(terms, [], draft).terms)
        for mode, module in [("baseline", baseline), ("candidate", inference_policy)]
    }} for draft in queries]


def public_retrieval(root, baseline):
    """References/target labels are used only after hint selection for counting."""
    rows = json.loads((root / "scored-acl.json").read_text())
    cohorts = {}
    with zipfile.ZipFile(root / "acl.zip") as archive:
        for split in ["eval", "dev"]:
            xml = archive.read(f"2/acl_6060/{split}/text/xml/ACL.6060.{split}.en-xx.en.xml")
            abstracts = re.findall(r"<abstract>(.*?)</abstract>", xml.decode(), re.S)
            pages = [{"page": i + 1, "text": text} for i, text in enumerate(abstracts)]
            terms = knowledge.analyze(pages, "public-abstracts")["terms"]
            cohorts[split] = (terms, [{"name": "public-abstracts", "pages": pages}])
    result = []
    def fold(text):
        return re.sub(r"[^a-z0-9]", "", text.casefold())
    for row in rows:
        terms, docs = cohorts["dev" if row["id"].startswith("dev-") else "eval"]
        selected = {mode: module.select_asr_hints(terms, docs, row["texts"]["base"])
                    for mode, module in [("baseline", baseline), ("candidate", inference_policy)]}
        result.append({"id": row["id"], **{mode: {
            "hint_count": len(value.terms),
            "target_selected": any(fold(term) == fold(row["term"]) for term in value.terms),
        } for mode, value in selected.items()},
            "changed": selected["baseline"].terms != selected["candidate"].terms})
    return {"queries": len(rows), "changed_queries": sum(row["changed"] for row in result),
            "cohort_extracted_terms": {key: len(value[0]) for key, value in cohorts.items()},
            **{mode: {
                "total_hint_selections": sum(row[mode]["hint_count"] for row in result),
                "target_selected_queries": sum(row[mode]["target_selected"] for row in result),
                "nonempty_queries": sum(row[mode]["hint_count"] > 0 for row in result),
            } for mode in ["baseline", "candidate"]},
            "target_lost_query_ids": [row["id"] for row in result
                if row["baseline"]["target_selected"] and not row["candidate"]["target_selected"]],
            "rows": result}


def scheduler_case(cost, backlog, previous_count, engine_type):
    clock, events, required_jobs = [0.0], [], []
    class Session:
        count = 0
        def init_streaming(self, **_):
            self.count += 1
            return SimpleNamespace(text=f"Synthetic utterance {self.count}")
        def feed_audio(self, pcm, state):
            return state
        def finish_streaming(self, state):
            return state
    class Translator:
        def translate(self, text, **_):
            clock[0] += cost
            return {"text": "translated " + text}
    with patch.object(live.time, "monotonic", lambda: clock[0]):
        engine = engine_type(Session(), Translator(),
            lambda event, *_: events.append({"wall": clock[0], **event}), deferred=True)
        complete = engine.complete_translation
        def record_complete(key):
            job = engine.pending_translations[key]
            complete(key)
            required_jobs.append((job.source_text, job.context, job.following))
        engine.complete_translation = record_complete
        pcm = np.full(4000, .1, dtype=np.float32)
        for index in range(previous_count):
            engine.feed(pcm, index * .25)
            engine.finish()
            clock[0] += .25
            engine.service(backlog)
        for index in range(48):
            engine.feed(pcm, (previous_count + index) * .25)
            clock[0] += .25
            engine.service(backlog)
        engine.finish()
        engine.lock_all()
        target = f"Synthetic utterance {previous_count + 1}"
        relevant = [event for event in events if event["english"] == target]
        first_en = min(event["wall"] for event in relevant)
        translated = [event for event in relevant if event["chinese"]]
        first_zh = min(event["wall"] for event in translated)
        locked = [(event["start"], event["end"], event["english"], event["chinese"])
                  for event in events if event["status"] == "locked"]
        def fingerprint(value):
            return hashlib.sha256(json.dumps(value).encode()).hexdigest()
        return {"english_to_chinese_seconds": round(first_zh - first_en, 3),
                "required_job_fingerprint": fingerprint(required_jobs),
                "locked_caption_fingerprint": fingerprint(locked),
                "first_chinese_status": translated[0]["status"],
                "required_completed": engine.translations_completed,
                "context_revisions_completed": engine.context_translations_completed,
                "pending_after_stop": len(engine.pending_translations)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-study", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = load_baseline("inference_policy")
    baseline_live = load_baseline("live")
    results = {"baseline_commit": BASELINE, "synthetic_hint_queries": synthetic_hints(baseline),
               "scheduler": [{"mt_cost_seconds": cost, "fixed_backlog_packets": backlog,
                   "previous_short_utterances": prior,
                   "baseline": scheduler_case(cost, backlog, prior, baseline_live.CaptionEngine),
                   "production_fix": scheduler_case(cost, backlog, prior, live.CaptionEngine)}
                   for prior in [0, 4] for cost in [.35, .7, 1.3] for backlog in [0, 3, 8]]}
    for row in results["scheduler"]:
        original, candidate = row["baseline"], row["production_fix"]
        row["required_jobs_and_locked_results_equal"] = all(
            original[key] == candidate[key]
            for key in ["required_job_fingerprint", "locked_caption_fingerprint"])
    if args.public_study:
        results["public_retrieval"] = public_retrieval(args.public_study, baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"synthetic_hint_queries": results["synthetic_hint_queries"],
        "scheduler": results["scheduler"],
        "public_retrieval": {k: v for k, v in results.get("public_retrieval", {}).items()
                             if k != "rows"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
