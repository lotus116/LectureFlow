#!/usr/bin/env python3
"""Compare audio-only vs guided re-recognition on a short local clip.

Writes a private JSON report. Does not upload audio or courseware.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lectureflow.adapters import QwenASR  # noqa: E402
from lectureflow.audio import extract_clip  # noqa: E402
from lectureflow.documents import extract as extract_document  # noqa: E402
from lectureflow.inference_policy import decide_enhancement, select_asr_hints  # noqa: E402
from lectureflow.knowledge import analyze  # noqa: E402
from lectureflow.models import resolve  # noqa: E402
from lectureflow.runtime import configure, sha256, write_json  # noqa: E402
from lectureflow.worker import recognize_unit  # noqa: E402


def materials_from_document(path: Path) -> tuple[list[dict], list[dict], dict]:
    pages = extract_document(path)
    terms = analyze(pages, "probe")["terms"]
    hint_terms = [
        {
            "english": item["english"],
            "score": item["score"],
            "confirmed": bool(item.get("chinese")) or item["score"] >= 6,
        }
        for item in terms
    ]
    documents = [{"name": path.name, "pages": pages}]
    return hint_terms, documents, {"name": path.name, "sha256": sha256(path), "pages": len(pages)}


def materials_from_pages(name: str, pages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    terms = analyze(pages, "probe")["terms"]
    hint_terms = [
        {
            "english": item["english"],
            "score": item["score"],
            "confirmed": bool(item.get("chinese")) or item["score"] >= 6,
        }
        for item in terms
    ]
    documents = [{"name": name, "pages": pages}]
    return hint_terms, documents, {"name": name, "pages": len(pages), "synthetic": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--document", type=Path, help="PDF/PPTX courseware matched to the clip")
    parser.add_argument(
        "--synthetic-topic",
        action="store_true",
        help="Use a short synthetic OS/kernel page aligned to the L4 sample topic",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, default=ROOT / "models")
    parser.add_argument("--asr", default="asr-small")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument(
        "--unrelated-document",
        type=Path,
        help="Optional second document used only for an injection control run",
    )
    args = parser.parse_args()
    configure(args.model_root)
    if args.synthetic_topic:
        hint_terms, documents, doc_info = materials_from_pages(
            "synthetic-os-topic.txt",
            [
                {
                    "page": 1,
                    "text": (
                        "Compatible CPU instruction and system call help user mode "
                        "transition to kernel mode. ReadFile CreateFile NtReadFile "
                        "Windows API kernel code demonstration."
                    ),
                }
            ],
        )
    elif args.document:
        hint_terms, documents, doc_info = materials_from_document(args.document)
    else:
        raise SystemExit("Provide --document or --synthetic-topic")
    clip_dir = args.output.parent / ".probe-clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip = clip_dir / f"{args.audio.stem}-{int(args.start)}-{int(args.seconds)}.wav"
    duration = extract_clip(args.audio, clip, args.start, args.seconds)
    engine = QwenASR(Path(resolve(args.asr, args.model_root, "asr")["path"]))

    baseline = engine.transcribe(clip, "")
    guided_text, guided_meta = recognize_unit(
        engine, clip, {"hint_terms": hint_terms, "documents": documents}
    )
    selection = select_asr_hints(hint_terms, documents, (baseline.get("text") or "").strip())
    raw = guided_meta.get("raw_english") or (baseline.get("text") or "").strip()
    enhanced = (guided_meta.get("enhancement") or {}).get("enhanced_english") or ""
    decision = decide_enhancement(raw, enhanced, selection.terms) if enhanced else None

    control = None
    if args.unrelated_document and args.unrelated_document.exists():
        alien_terms, alien_docs, alien_info = materials_from_document(args.unrelated_document)
        control_text, control_meta = recognize_unit(
            engine, clip, {"hint_terms": alien_terms[:40], "documents": alien_docs}
        )
        control = {
            "document": alien_info,
            "final": control_text,
            "raw": control_meta.get("raw_english"),
            "hints": control_meta.get("courseware_hints"),
            "enhancement": control_meta.get("enhancement"),
            "changed_from_raw": control_text != control_meta.get("raw_english"),
        }

    report = {
        "schema_version": 1,
        "kind": "guided_asr_effect_probe",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "audio": {"name": args.audio.name, "sha256": sha256(args.audio)},
        "document": doc_info,
        "clip": {"path": str(clip), "duration_s": duration, "sha256": sha256(clip)},
        "asr": args.asr,
        "baseline_audio_only": baseline.get("text"),
        "selection": {
            "context": selection.context,
            "terms": list(selection.terms),
            "reason": selection.reason,
        },
        "guided": {
            "final": guided_text,
            "raw": raw,
            "hints": guided_meta.get("courseware_hints"),
            "hint_terms_used": guided_meta.get("hint_terms_used"),
            "enhancement": guided_meta.get("enhancement"),
            "policy": guided_meta.get("asr_input_policy"),
        },
        "diff": {
            "raw_vs_final": raw != guided_text,
            "baseline_vs_final": (baseline.get("text") or "").strip() != guided_text,
            "gate_reason": decision.reason if decision else guided_meta.get("enhancement", {}).get("reason"),
            "accepted": bool((guided_meta.get("enhancement") or {}).get("accepted")),
        },
        "unrelated_control": control,
        "quality": "manual_review_required",
        "note": "Not a full WER study. Inspect corrections, wrong edits, and vocabulary dumps.",
    }
    write_json(args.output, report)
    print(json.dumps({
        "output": str(args.output),
        "raw": raw,
        "final": guided_text,
        "accepted": report["diff"]["accepted"],
        "hints": selection.context,
        "gate": report["diff"]["gate_reason"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
