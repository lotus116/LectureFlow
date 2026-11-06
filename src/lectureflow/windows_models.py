"""Experimental CPU adapters. Only explicitly selected local weights are loaded."""

from pathlib import Path


class WhisperASR:
    def __init__(self, path: Path):
        from faster_whisper import WhisperModel

        for name in ("model.bin", "config.json", "tokenizer.json"):
            if not (path / name).is_file():
                raise ValueError(f"识别模型缺少 {name}，请选择完整的 faster-whisper 本地目录")
        self.model = WhisperModel(str(path), device="cpu", compute_type="int8",
                                  local_files_only=True)

    def transcribe(self, audio: Path, context: str = "") -> dict:
        segments, info = self.model.transcribe(str(audio), language="en", beam_size=5,
                                                initial_prompt=context or None)
        return {"text": " ".join(s.text.strip() for s in segments),
                "language": info.language, "truncated": False}


class MarianTranslator:
    def __init__(self, path: Path):
        from transformers import MarianMTModel, MarianTokenizer

        if not path.is_dir():
            raise ValueError("请选择完整的本地 Marian 英译中模型目录")
        self.tokenizer = MarianTokenizer.from_pretrained(str(path), local_files_only=True)
        self.model = MarianMTModel.from_pretrained(
            str(path), local_files_only=True, weights_only=True,
        ).eval()

    def translate(self, text: str, glossary="", context="", following="") -> dict:
        import torch

        if not text.strip():
            return {"text": "", "truncated": False}
        inputs = self.tokenizer(text, return_tensors="pt", truncation=False)
        if inputs.input_ids.shape[-1] > self.model.config.max_position_embeddings:
            raise ValueError("翻译输入过长，本区间未提交")
        with torch.inference_mode():
            output = self.model.generate(**inputs, max_new_tokens=512)
        return {"text": self.tokenizer.decode(output[0], skip_special_tokens=True),
                "truncated": output[0, -1].item() != self.tokenizer.eos_token_id}


CATALOG = {
    "asr": ("Systran/faster-whisper-tiny.en", "0d3d19a32d3338f10357c0889762bd8d64bbdeba"),
    "translation": ("Helsinki-NLP/opus-mt-en-zh", "408d9bc410a388e1d9aef112a2daba955b945255"),
}


def prepare(root: Path):
    """Explicit online download; never reads classroom data."""
    from huggingface_hub import snapshot_download

    from .runtime import configure

    configure(root / "Cache", online=True)
    try:
        for name, (repo, revision) in CATALOG.items():
            snapshot_download(repo, revision=revision, local_dir=root / name,
                              allow_patterns=["*.json", "*.safetensors", "*.bin",
                                              "*.spm", "*.txt"])
    finally:
        configure(root / "Cache")


QWEN_SPEC = ("Qwen/Qwen3-ASR-1.7B", "7278e1e70fe206f11671096ffdd38061171dd6e5")


class QwenCPUASR:
    """Explicit CPU/FP32 path. No CUDA selection or automatic GPU offload."""

    def __init__(self, path: Path, language="English"):
        if language not in {"English", "auto"}:
            raise ValueError("不支持的语言设置")
        if not (path / "config.json").is_file():
            raise ValueError("请选择完整的本地 Qwen3-ASR 模型目录")
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except ImportError as error:
            raise ValueError("请安装 windows 与 windows-qwen 可选依赖") from error
        torch.set_num_threads(8)
        self.language = language
        self.model = Qwen3ASRModel.from_pretrained(
            str(path.resolve()), dtype=torch.float32, device_map="cpu",
            max_inference_batch_size=1, max_new_tokens=512, local_files_only=True,
        )

    def transcribe(self, audio: Path, context: str = "") -> dict:
        result = self.model.transcribe(
            audio=str(audio), context=context,
            language=None if self.language == "auto" else self.language,
        )[0]
        return {"text": result.text, "language": result.language,
                "truncated": False, "truncation_status": "not_exposed_by_qwen_wrapper",
                "device": "cpu", "dtype": "float32"}


def prepare_qwen(root: Path):
    from huggingface_hub import snapshot_download

    from .runtime import configure

    configure(root / "Cache", online=True)
    try:
        snapshot_download(QWEN_SPEC[0], revision=QWEN_SPEC[1], local_dir=root / "qwen-asr",
                          allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja"])
    finally:
        configure(root / "Cache")
