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

    def translate(
        self, text: str, glossary: str = "", context: str = "", following: str = "",
    ) -> dict:
        if not text.strip():
            return {"text": "", "first_token_s": None, "total_s": 0, "truncated": False}
        prompt = (
            (f"后续讲话（仅作消歧参考，不要翻译或补写）：\n{following[-1000:]}\n"
             if following else "")
            +
            (f"上下文（仅作消歧，勿翻译或补写）：\n{context[-2400:]}\n" if context else "")
            +
            (f"参考术语（仅在原文涉及这些词时使用）：\n{glossary}\n" if glossary else "")
            + "将以下文本翻译为简体中文，注意只需要输出翻译后的结果，不要额外解释：\n"
            + text
        )
        return self._generate(prompt)

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
