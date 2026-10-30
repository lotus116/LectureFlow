# Phase 0 验证协议

## 固定候选与来源

| 候选 | 本轮权重 | 固定提交 |
|---|---|---|
| Qwen3-ASR-0.6B 8-bit | `moona3k/mlx-qwen3-asr-0.6b-8bit` | `83ce2a8ef9a382d2ba2209aab383bc51fff366f5` |
| Qwen3-ASR-1.7B 8-bit | `moona3k/mlx-qwen3-asr-1.7b-8bit` | `22c8abe6a6772122dda5905967d7496d1d3e8dd2` |
| Hy-MT2-1.8B 4-bit | `mlx-community/Hy-MT2-1.8B-4bit` | `e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912` |

ASR 权重来自所选运行时作者发布的转换版本；翻译权重是社区转换版本，尚需与官方基线做质量/数值对照。项目不加载仓库自定义 Python 代码。

- [ASR 运行时文档](https://github.com/moona3k/mlx-qwen3-asr)
- [Hy-MT2 官方项目与提示格式](https://github.com/Tencent-Hunyuan/Hy-MT2)
- [MLX LM 实现的模型架构](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/models/hunyuan_v1_dense.py)

依赖版本保存于 `uv.lock`。本机实际版本和设备信息保存于 `reports/private/environment-metal.json`。

## 样本与隐私

本轮用户指定 Reverse engineering and malware analysis/record。只读检查发现 3 段 M4A，约 175.88 分钟。文件顺序和是否属于同堂课尚未由用户确认，不直接作为正式课堂导入顺序。

`local/recordings.json` 保存源路径、文件大小、原始 SHA-256 和估算时长。基准先使用 `7905-L4-01.m4a` 的 60–90 秒，解码为 16kHz 单声道 PCM，源文件保持不变。

`local/terms-experimental.txt` 和术语对照是依据样本主题手工准备的实验提示，**不是已经实现的课件提取结果**，不能当作 LF-06 的验收证据。已有转写也不自动作为人工真值。

## 对照矩阵

每组独立进程、同一裁剪片段、1 次首轮与 2 次暖运行：

1. 0.6B 无术语提示。
2. 1.7B 无术语提示。
3. 0.6B 有实验术语提示和翻译对照。
4. 1.7B 有实验术语提示和翻译对照。

记录英文原文、中文、生成截断、加载时间、ASR 秒数/RTF、翻译首 token/总时间、进程峰值 RSS，以及工具加入内存采样后可获得的 MLX 活跃/缓存/峰值内存。缺少的旧指标不回填或估算。

## 流式探针

```sh
.venv/bin/lectureflow stream-probe '/absolute/path/recording.m4a' \
  --start 60 --seconds 30 --chunk-seconds 2 \
  --output reports/private/stream.json
```

每块音频按原始真实时间的块尾时刻可用；后续可用时刻不因前一块推理变慢而顺延，才能观察积压。模型加载计时单列，推理包含首次执行开销。

这个探针串行执行 ASR 和翻译，最多翻译最近 ASR 窗口的 40 个词，12 秒 ASR 活动窗口。报告的 lag 是相对**输入音频块末尾**的输出时间，不能当作话语结束到有意义中文的语义延迟。旧窗口保留英文结果；正式字幕修订、句子边界与跨窗口翻译仍未实现。

只允许最多 300 秒，防止把验证工具误当作三小时生产服务。正式长时测试应使用 Phase 2 的有界队列、持续写入器及增量事件日志。

## 尚待完成的阶段门槛

- 从三份原始录音选择更多口音/快语速/专业词/噪声样本，避免只测一个 30 秒片段。
- 对选择区间人工核验原文，计算 WER/专业词准确率；听音核验中文语义。
- 用真实课件生成术语提示，开展有/无增强的对照。
- 验证 MLX 社区翻译权重和官方模型输出一致性；解释 Transformers 的 RoPE 元数据警告。
- 标注话语和可翻译语义边界，测量首个有用中文、稳定译文延迟分布。
- 完成 30 分钟和 3 小时真实持续运行、设备/休眠/磁盘异常及断网测试。

短测通过仅表示值得继续，Phase 0 不因此自动关闭。
