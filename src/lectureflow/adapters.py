"""Model imports are lazy; domain tools and tests do not require GPU access."""

from __future__ import annotations

import re
import time
from pathlib import Path

from .translation_numbers import repair_numbers
from .translation_policy import translation_prompt, validate_translation


class QwenASR:
    def __init__(self, path: Path):
        from mlx_qwen3_asr import Session

        self.session = Session(model=str(path))
        self.language = "English"

    def transcribe(self, audio: Path, context: str = "") -> dict:
        result = self.session.transcribe(
            str(audio), context=context, language=self.language, return_chunks=True,
        )
        return {
            "text": result.text, "language": result.language, "chunks": result.chunks,
            "truncated": result.truncated, "finish_reason": result.finish_reason,
        }


class HyMTTranslator:
    def __init__(self, path: Path):
        from mlx_lm import load

        self.model, self.tokenizer = load(
            str(path), tokenizer_config={"trust_remote_code": False},
        )

    def translate(
        self, text: str, glossary: str = "", context: str = "", following: str = "",
    ) -> dict:
        if not text.strip():
            return {"text": "", "first_token_s": None, "total_s": 0, "truncated": False}
        if contains_only_chinese_speech(text):
            return {"text": text, "first_token_s": 0, "total_s": 0, "truncated": False}
        result = self._generate(translation_prompt(text, context, following, glossary))
        validate_translation(text, result["text"])
        result["text"] = repair_numbers(text, result["text"])
        return result

    def translate_term(self, text: str, context: str = "") -> dict:
        # Separate from sentence translation: a source excerpt is not the target.
        prompt = (
            "任务：给出待译术语的简体中文短译名，只输出译名。不要翻译背景、解释或造句。"
            "无法确定译名、代码、组织名或产品名可保留原文。\n"
            + (f"背景（仅作消歧）：{context[:180]}\n" if context else "")
            + f"待译术语：{text}"
        )
        return self._generate(prompt, max_tokens=64)

    def _generate(self, prompt: str, *, max_tokens: int = 512) -> dict:
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        formatted = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True,
        )
        started = time.perf_counter()
        pieces: list[str] = []
        first = None
        final_reason = None
        for response in stream_generate(
            self.model, self.tokenizer, prompt=formatted,
            max_tokens=max_tokens, sampler=make_sampler(temp=0),
        ):
            if response.text and first is None:
                first = time.perf_counter() - started
            pieces.append(response.text)
            final_reason = response.finish_reason
        return {
            "text": "".join(pieces).strip(), "first_token_s": first,
            "total_s": time.perf_counter() - started, "truncated": final_reason == "length",
        }


def contains_only_chinese_speech(text: str) -> bool:
    """Mixed Latin speech still needs translation; never infer language from a label alone."""
    return bool(re.search(r"[㐀-鿿]", text)) and not re.search(r"[A-Za-z]", text)


def source_language(config: dict) -> str | None:
    language = config.get("source_language", "English")
    if language not in {"auto", "English"}:
        raise ValueError("不支持的讲话语言配置")
    return None if language == "auto" else language
