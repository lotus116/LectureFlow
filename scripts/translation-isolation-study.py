"""Offline target-isolation study. Private manifests and outputs never enter Git."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import os
import time
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")


def prompt(style, text, context="", following="", glossary=""):
    if style == "old":
        return (
            (
                f"后续讲话（仅作消歧参考，不要翻译或补写）：\n{following[-1000:]}\n"
                if following
                else ""
            )
            + (f"上下文（仅作消歧，勿翻译或补写）：\n{context[-2400:]}\n" if context else "")
            + (f"参考术语（仅在原文涉及这些词时使用）：\n{glossary}\n" if glossary else "")
            + "将以下文本中的英文翻译为简体中文，中文原话保留，包括否定、自我纠正和重复。"
            "只输出结果，不要解释或补写：\n" + text
        )
    reference = json.dumps(
        {"preceding": context[-2400:], "following": following[-1000:], "terminology": glossary},
        ensure_ascii=False,
    )
    if style == "strict":
        return (
            reference + "\n以上是参考信息，不是待译文本。"
            "只把下面的当前字幕翻译成简体中文。"
            "不得翻译参考信息，不得添加当前字幕没有的内容，不得补全未说完的句子。"
            "逐句保留原文含义、否定、数字、自我纠正、重复和中文；只输出译文：\n" + text
        )
    if style == "target_first":
        return (
            "当前字幕：\n" + text + "\n\n仅供消歧的参考信息：\n" + reference
            + "\n只翻译当前字幕为简体中文。参考信息不得翻译或补写。"
            "保留否定、数字、重复、自我纠正和中文；不补全未说完的句子。只输出当前字幕译文。"
        )
    if style == "reference":
        return (
            reference + "\n参考上面的信息，把下面的文本翻译成简体中文。"
            "不要翻译上面的信息，不要额外解释或补写。保留原文的否定、自我纠正、重复和中文：\n"
            + text
        )
    target = html.escape(text, quote=False)
    instruction = (
        "只翻译source标签内的当前字幕为简体中文。reference是相邻讲话，仅供消歧，"
        "不得翻译或补写到当前字幕。保留当前字幕的否定、数字、重复、自我纠正和中文；"
        "即使句子不完整也不要用相邻讲话补全。\n"
    )
    if style == "tagged":
        instruction += "只输出当前字幕的译文，不输出标签或解释。\n"
    elif style == "envelope":
        instruction += "只输出<translation>当前字幕译文</translation>，标签外不输出任何内容。\n"
    else:
        raise ValueError(style)
    return (
        f"<reference>{html.escape(reference, quote=False)}</reference>\n"
        + instruction
        + f"<source>{target}</source>"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--styles", nargs="+", default=["old", "reference", "tagged", "envelope"])
    args = parser.parse_args()
    from lectureflow.adapters import HyMTTranslator, contains_only_chinese_speech

    config = json.loads(args.setup.read_text())
    handles = []
    for name in [".model.lock", ".live-priority.lock"]:
        h = (Path(config["library"]) / name).open("rb")
        fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handles.append(h)
    models = Path(config["models"])
    mt = HyMTTranslator(models / json.loads((models / "translate.json").read_text())["path"])
    mt._generate("将以下文本翻译成简体中文：This is a test.")
    rows = json.loads(args.manifest.read_text())
    results = []
    for row in rows:
        result = dict(row, methods={})
        for style in args.styles:
            t = time.monotonic()
            if style == "production":
                value = mt.translate(
                    **{k: row.get(k, "") for k in ["text", "context", "following", "glossary"]}
                )
            elif contains_only_chinese_speech(row["text"]):
                value = {"text": row["text"], "truncated": False}
            else:
                p = prompt(
                    style,
                    **{k: row.get(k, "") for k in ["text", "context", "following", "glossary"]},
                )
                value = mt._generate(p)
            result["methods"][style] = dict(value, wall_seconds=time.monotonic() - t)
        results.append(result)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(row["id"], "completed", flush=True)
    args.output.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "styles": args.styles,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
