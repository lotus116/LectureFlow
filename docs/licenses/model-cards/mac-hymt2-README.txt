---
license: apache-2.0
library_name: mlx
pipeline_tag: translation
base_model:
- Tencent-Hunyuan/Hy-MT2-1.8B
tags:
- mlx
- apple-silicon
- mlx-lm
- translation
- hunyuan_v1_dense
language:
- multilingual
---

Part of the [Hy-MT2 MLX](https://huggingface.co/collections/mlx-community/hy-mt2-6a15a173a4e2d27031541558) collection.

# Hy-MT2-1.8B-4bit (MLX)

Apple MLX weights for [Tencent-Hunyuan/Hy-MT2-1.8B](https://github.com/Tencent-Hunyuan/Hy-MT2), Tencent Hunyuan's multilingual translation model.

## TL;DR

| | |
|---|---|
| **Variant** | 1.8B 4-bit |
| **Best for** | smallest 1.8B checkpoint and Space load smoke test |
| **Runtime** | [`mlx-lm`](https://github.com/ml-explore/mlx-lm) |
| **Official code** | [`Tencent-Hunyuan/Hy-MT2`](https://github.com/Tencent-Hunyuan/Hy-MT2) |
| **MLX code** | [`ailuntx/Hy-MT2-MLX`](https://github.com/ailuntx/Hy-MT2-MLX) |
| **Hardware** | Apple Silicon recommended; HF Spaces CPU fallback is only a load smoke test |

## Quick Start

```bash
pip install mlx-lm
PROMPT=$'Translate the following text into English. Note that you should only output the translated result without any additional explanation:\n\n今天天气真好。'
mlx_lm.generate --model mlx-community/Hy-MT2-1.8B-4bit \
  --prompt "$PROMPT" \
  --max-tokens 128 \
  --temp 0.7 \
  --top-p 0.6 \
  --top-k 20
```

For conversion and staging tools:

```bash
git clone https://github.com/ailuntx/Hy-MT2-MLX.git
cd Hy-MT2-MLX
python scripts/infer_mlx.py --model /path/to/mlx/checkpoint --text "今天天气真好。" --target-lang English
```

## Variants

| Variant | Best for |
|---|---|
| [`Hy-MT2-1.8B-bfloat16`](https://huggingface.co/mlx-community/Hy-MT2-1.8B-bfloat16) | high-quality 1.8B baseline |
| [`Hy-MT2-1.8B-8bit`](https://huggingface.co/mlx-community/Hy-MT2-1.8B-8bit) | smaller 1.8B checkpoint |
| [`Hy-MT2-1.8B-4bit`](https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit) | smallest 1.8B checkpoint |
| [`Hy-MT2-7B-bfloat16`](https://huggingface.co/mlx-community/Hy-MT2-7B-bfloat16) | high-precision 7B conversion |
| [`Hy-MT2-7B-8bit`](https://huggingface.co/mlx-community/Hy-MT2-7B-8bit) | 7B size/quality middle ground |
| [`Hy-MT2-7B-4bit`](https://huggingface.co/mlx-community/Hy-MT2-7B-4bit) | smallest 7B checkpoint |

## Conversion Notes

| Component | Source | MLX handling |
|---|---|---|
| model weights | official dense Hy-MT2 checkpoint | converted with MLX/`mlx-lm` tooling |
| tokenizer/config | official checkpoint | copied through for `mlx-lm` loading |
| quantized variants | bfloat16 MLX baseline | derived with MLX quantization settings |

## Validation

Local Apple Silicon is the intended runtime. The Hy-MT2 HF Space starts and loads the model on Linux CPU fallback, but `cpu-basic` can exceed request timeouts even for very small generation tests.

## License

License follows the upstream Tencent Hunyuan release.

## Citation

```bibtex
@misc{hymt2-mlx,
  title  = {Hy-MT2-MLX: Apple MLX port of Hy-MT2},
  author = {ailuntx},
  year   = {2026},
  url    = {https://github.com/ailuntx/Hy-MT2-MLX},
}

@misc{zheng2026hymt2familyfastefficient,
  title         = {Hy-MT2: A Family of Fast, Efficient and Powerful Multilingual Translation Models in the Wild},
  author        = {Mao Zheng and Zheng Li and Tao Chen and Bo Lv and Mingrui Sun and Mingyang Song and Jinlong Song and Hong Huang and Decheng Wu and Hai Wang and Yifan Song and Yanfeng Chen and Guanwei Zhang},
  year          = {2026},
  eprint        = {2605.22064},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CL},
  url           = {https://arxiv.org/abs/2605.22064},
}
```
