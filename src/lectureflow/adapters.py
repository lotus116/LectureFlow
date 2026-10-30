"""Model imports are lazy; domain tools and tests do not require GPU access."""

from __future__ import annotations

import time
from pathlib import Path


class QwenASR:
    def __init__(self, path: Path):
        from mlx_qwen3_asr import Session

        self.session = Session(model=str(path))

    def transcribe(self, audio: Path, context: str = "") -> dict:
        result = self.session.transcribe(
            str(audio), context=context, language="English", return_chunks=True,
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

    def translate(self, text: str, glossary: str = "", context: str = "") -> dict:
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        if not text.strip():
            return {"text": "", "first_token_s": None, "total_s": 0, "truncated": False}
        prompt = (
            (f"前文（仅供理解，不要翻译或重复前文）：\n{context[-1000:]}\n" if context else "")
            +
            (f"参考术语（仅在原文涉及这些词时使用）：\n{glossary}\n" if glossary else "")
            + "将以下文本翻译为简体中文，注意只需要输出翻译后的结果，不要额外解释：\n"
            + text
        )
        formatted = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True,
        )
        started = time.perf_counter()
        pieces: list[str] = []
        first = None
        final_reason = None
        for response in stream_generate(
            self.model, self.tokenizer, prompt=formatted,
            max_tokens=512, sampler=make_sampler(temp=0),
        ):
            if response.text and first is None:
                first = time.perf_counter() - started
            pieces.append(response.text)
            final_reason = response.finish_reason
        return {
            "text": "".join(pieces).strip(), "first_token_s": first,
            "total_s": time.perf_counter() - started, "truncated": final_reason == "length",
        }
