from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .models import CATALOG
from .runtime import configure, environment, write_json


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="LectureFlow 本地模型验证工具")
    root.add_argument("--model-root", type=Path, default=Path("models"))
    root.add_argument("--library-root", type=Path, default=Path("data/library"))
    commands = root.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="检查环境，可选 GPU 检查")
    doctor.add_argument("--metal", action="store_true")
    doctor.add_argument("--output", type=Path)
    fetch = commands.add_parser("fetch", help="下载固定版本模型，不读取课堂资料")
    fetch.add_argument("models", nargs="+", choices=CATALOG)
    bench = commands.add_parser("benchmark", help="只读音频片段，离线基准")
    bench.add_argument("audio", type=Path)
    bench.add_argument("--asr", choices=["asr-small", "asr-large"], default="asr-small")
    bench.add_argument("--translate", action="store_true")
    bench.add_argument("--start", type=float, default=0)
    bench.add_argument("--seconds", type=float, default=30)
    bench.add_argument("--repeats", type=int, default=2)
    bench.add_argument("--context-file", type=Path)
    bench.add_argument("--glossary-file", type=Path)
    bench.add_argument("--reference", type=Path, help="仅限所选时间区间的人工参考英文")
    bench.add_argument("--output", type=Path, required=True)
    stream = commands.add_parser("stream-probe", help="真实节奏回放；不等同语义延迟验收")
    stream.add_argument("audio", type=Path)
    stream.add_argument("--start", type=float, default=0)
    stream.add_argument("--seconds", type=float, default=30)
    stream.add_argument("--chunk-seconds", type=float, default=2)
    stream.add_argument("--context-file", type=Path)
    stream.add_argument("--output", type=Path, required=True)
    library = commands.add_parser("library", help="课程资料库与后台任务")
    actions = library.add_subparsers(dest="action", required=True)
    actions.add_parser("status")
    course = actions.add_parser("create-course")
    course.add_argument("name")
    lecture = actions.add_parser("create-lecture")
    lecture.add_argument("course_id")
    lecture.add_argument("title")
    imported = actions.add_parser("import")
    imported.add_argument("lecture_id")
    imported.add_argument("files", nargs="+", type=Path)
    reorder = actions.add_parser("reorder")
    reorder.add_argument("lecture_id")
    reorder.add_argument("segment_ids", nargs="+")
    enqueue = actions.add_parser("enqueue")
    enqueue.add_argument("lecture_id")
    enqueue.add_argument("--asr", choices=["asr-small", "asr-large"], default="asr-small")
    enqueue.add_argument("--no-translation", action="store_true")
    enqueue.add_argument("--segment", action="append", dest="segment_ids")
    enqueue.add_argument("--context-file", type=Path)
    enqueue.add_argument("--glossary-file", type=Path)
    control = actions.add_parser("task")
    control.add_argument("task_id")
    control.add_argument("operation", choices=["pause", "resume", "cancel", "retry"])
    content = actions.add_parser("content")
    content.add_argument("version_id")
    export = actions.add_parser("export")
    export.add_argument("version_id")
    commands.add_parser("worker", help="处理队列；关闭界面不会取消任务")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        configure(args.model_root)
        if args.command == "doctor":
            report = environment()
            if args.metal:
                import mlx.core as mx

                report["metal_available"] = mx.metal.is_available()
                report["metal_device"] = mx.device_info()
            if args.output:
                write_json(args.output, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "fetch":
            from .models import fetch

            for alias in args.models:
                record = fetch(alias, args.model_root)
                print(f"Downloaded {alias}: {record['repo']} @ {record['revision']}", flush=True)
        elif args.command == "benchmark":
            from .benchmark import run_benchmark

            report = run_benchmark(
                audio=args.audio, model_root=args.model_root, asr=args.asr,
                translate=args.translate, output=args.output, start=args.start,
                seconds=args.seconds, repeats=args.repeats,
                context=args.context_file.read_text() if args.context_file else "",
                glossary=args.glossary_file.read_text() if args.glossary_file else "",
                reference=args.reference.read_text() if args.reference else None,
            )
            print(f"{report['status']}: {args.output}")
            print("Offline timing only; classroom accuracy and live latency need separate review.")
        elif args.command == "stream-probe":
            from .stream_probe import probe

            report = probe(
                audio=args.audio, model_root=args.model_root, output=args.output,
                start=args.start, seconds=args.seconds, chunk_seconds=args.chunk_seconds,
                context=args.context_file.read_text() if args.context_file else "",
            )
            print(f"{report['status']}: {args.output}")
            print("Chunk lag only; useful semantic latency requires annotated classroom audio.")
        elif args.command == "library":
            from .library import Library

            with Library(args.library_root) as library:
                if args.action == "status":
                    result = library.overview()
                elif args.action == "create-course":
                    result = library.create_course(args.name)
                elif args.action == "create-lecture":
                    result = library.create_lecture(args.course_id, args.title)
                elif args.action == "import":
                    result = library.import_audio(args.lecture_id, args.files)
                elif args.action == "reorder":
                    library.reorder(args.lecture_id, args.segment_ids)
                    result = library.segments(args.lecture_id)
                elif args.action == "enqueue":
                    result = library.enqueue(
                        args.lecture_id, asr=args.asr, translate=not args.no_translation,
                        segment_ids=args.segment_ids,
                        context=args.context_file.read_text() if args.context_file else "",
                        glossary=args.glossary_file.read_text() if args.glossary_file else "",
                    )
                elif args.action == "task":
                    result = library.control(args.task_id, args.operation)
                elif args.action == "content":
                    result = library.version_content(args.version_id)
                elif args.action == "export":
                    from .exporting import export_version

                    result = export_version(library, args.version_id)
                print(json.dumps(result, ensure_ascii=False))
        elif args.command == "worker":
            from .worker import run_worker

            print(json.dumps(run_worker(args.library_root, args.model_root), ensure_ascii=False))
        return 0
    except (ValueError, OSError, ImportError, RuntimeError, sqlite3.Error) as error:
        print(f"LectureFlow: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
