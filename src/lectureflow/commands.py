"""App-facing commands kept separate from the inference/benchmark entry points."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def add(commands, actions) -> None:
    for name in ("rename", "archive", "restore"):
        action = actions.add_parser(name)
        action.add_argument(
            "kind",
            choices=["course", "lecture"] if name == "rename" else ["course", "lecture", "segment"],
        )
        action.add_argument("id")
        if name == "rename":
            action.add_argument("name")
        if name == "archive":
            action.add_argument("--confirmed", action="store_true")
    actions.add_parser("trash")
    actions.add_parser("trash-preview")
    emptied = actions.add_parser("empty-trash")
    emptied.add_argument("--digest", required=True)
    emptied.add_argument("--confirmed", action="store_true")
    move = actions.add_parser("move-lecture")
    move.add_argument("id")
    move.add_argument("course_id")
    transfer = actions.add_parser("transfer")
    transfer.add_argument("id")
    transfer.add_argument("lecture_id")
    transfer.add_argument("--copy", action="store_true")
    order = actions.add_parser("order-courses")
    order.add_argument("ids", nargs="+")
    models = commands.add_parser("models")
    model_actions = models.add_subparsers(dest="models_action", required=True)
    model_actions.add_parser("status")
    reuse = model_actions.add_parser("import-cache")
    reuse.add_argument("source_root", type=Path)
    materials = commands.add_parser("materials")
    methods = materials.add_subparsers(dest="materials_action", required=True)
    methods.add_parser("terms-batch")
    job_control = methods.add_parser("job-control")
    job_control.add_argument("id")
    job_control.add_argument("action", choices=["pause", "resume", "cancel", "retry"])
    for name in ("list", "import", "term-save"):
        method = methods.add_parser(name)
        method.add_argument("kind", choices=["course", "lecture"])
        method.add_argument("owner_id")
        if name == "list":
            method.add_argument("--include-shared", action="store_true")
        if name == "import":
            method.add_argument("source", type=Path)
            method.add_argument("--pages-file", type=Path)
        if name == "term-save":
            method.add_argument("english")
            method.add_argument("--chinese", default="")
            method.add_argument("--confirmed", action="store_true")
            method.add_argument("--id")
    removed = methods.add_parser("remove")
    removed.add_argument("id")
    removed.add_argument("--confirmed", action="store_true")


def read_input() -> dict:
    data = sys.stdin.buffer.read(16_000_001)
    if len(data) > 16_000_000:
        raise ValueError("输入资料过大")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("输入格式有误")
    return result


FILE_ACTIONS = {
    "trash-preview",
    "empty-trash",
    "rename",
    "archive",
    "restore",
    "trash",
    "move-lecture",
    "transfer",
    "order-courses",
}


def library_action(library, args):
    from . import files

    if args.action == "rename":
        files.rename(library, args.kind, args.id, args.name)
    elif args.action == "archive":
        files.archive(library, args.kind, args.id, confirmed=args.confirmed)
    elif args.action == "restore":
        files.restore(library, args.kind, args.id)
    elif args.action == "trash":
        return {"items": files.trash(library)}
    elif args.action in {"trash-preview", "empty-trash"}:
        from . import purging

        return (
            purging.preview(library)
            if args.action == "trash-preview"
            else purging.empty(library, args.digest, confirmed=args.confirmed)
        )
    elif args.action == "move-lecture":
        files.move_lecture(library, args.id, args.course_id)
    elif args.action == "transfer":
        return files.transfer(library, args.id, args.lecture_id, copy=args.copy)
    elif args.action == "order-courses":
        files.order_courses(library, args.ids)
    return {"ok": True}


def dispatch(args) -> dict:
    from . import documents
    from .library import Library

    if args.command == "models":
        from .models import import_cache, status

        if args.models_action == "import-cache":
            return import_cache(args.source_root, args.model_root)
        return {"models": status(args.model_root)}
    if args.command != "materials":
        raise ValueError("未知操作")
    with Library(args.library_root) as library:
        if args.materials_action in {"terms-batch", "job-control"}:
            from . import knowledge

            return (
                knowledge.batch(library, read_input())
                if args.materials_action == "terms-batch"
                else knowledge.control(library, args.id, args.action)
            )
        if args.materials_action == "list":
            return documents.listing(
                library, args.kind, args.owner_id, include_shared=args.include_shared
            )
        if args.materials_action == "import":
            extracted = json.loads(args.pages_file.read_text()) if args.pages_file else None
            return documents.import_document(
                library, args.kind, args.owner_id, args.source, extracted=extracted
            )
        if args.materials_action == "term-save":
            return documents.save_term(
                library,
                args.kind,
                args.owner_id,
                args.english,
                args.chinese,
                args.confirmed,
                args.id,
            )
        documents.remove(library, args.id, confirmed=args.confirmed)
        return {"ok": True}
