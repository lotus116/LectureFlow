"""Keep translation targets separate from neighbouring reference speech."""

from __future__ import annotations

import json
import re


def translation_prompt(
    text: str, context: str = "", following: str = "", glossary: str = ""
) -> str:
    if not following:
        # Initial translation has no future speech to revise against. Preserve
        # its existing prompt while isolating the context-revision path below.
        return (
            (f"上下文（仅作消歧，勿翻译或补写）：\n{context[-2400:]}\n" if context else "")
            + (f"参考术语（仅在原文涉及这些词时使用）：\n{glossary}\n" if glossary else "")
            + "将以下文本中的英文翻译为简体中文，中文原话保留，包括否定、自我纠正和重复。"
            "只输出结果，不要解释或补写：\n" + text
        )
    # JSON escapes quotes/newlines in reference data. The target remains after the
    # task instruction; it is never concatenated with neighbouring utterances.
    reference = json.dumps(
        {"preceding": context[-2400:], "following": following[-1000:], "terminology": glossary},
        ensure_ascii=False,
    )
    return (
        reference + "\n以上是参考信息，不是待译文本。"
        "只把下面的当前字幕翻译成简体中文。"
        "不得翻译参考信息，不得添加当前字幕没有的内容，不得补全未说完的句子。"
        "逐句保留原文含义、否定、数字、自我纠正、重复和中文；只输出译文：\n" + text
    )


def validate_translation(text: str, translated: str) -> None:
    """Reject observable framing leaks, not claim to judge semantic correctness.

    Raise before callers can overwrite a previous translation or acknowledge a
    pending revision. Literal framing spoken in the target is allowed.
    """
    if not translated.strip():
        raise ValueError("翻译结果为空，已保留此前字幕与待办")
    markers = (
        "上下文（仅作消歧",
        "后续讲话（仅作消歧",
        "参考术语（仅在原文涉及",
        "参考上面的信息，把下面的文本翻译",
        "不要翻译上面的信息",
        "以上是参考信息，不是待译文本",
        "不得翻译参考信息，不得添加当前字幕",
    )
    if any(marker in translated and marker not in text for marker in markers):
        raise ValueError("译文混入上下文提示，已保留此前字幕与待办")
    for key in ("preceding", "following", "terminology"):
        pattern = rf'["“]{key}["”]\s*:'
        if re.search(pattern, translated) and not re.search(pattern, text):
            raise ValueError("译文混入上下文结构，已保留此前字幕与待办")
