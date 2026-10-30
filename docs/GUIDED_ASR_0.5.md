# 0.5 课件辅助识别与总结

对应 LF-06、LF-10。上传课程/本课 PDF·PPT 后自动参与识别增强与课后总结上下文；不要求逐词确认。

## 实现要点

1. **选择器**（`inference_policy.select_asr_hints`）：按当前识别草稿做容错检索，选出少量拼写偏好。词数/长度是起始可调参数，不是安全保证。
2. **离线双遍**（`worker.recognize_unit`）：第一遍纯音频并保留 `raw_english`；有相关提示时第二遍再识别；保守接受门不确定则保留第一遍。旧任务里的整表 `context` 仍被忽略。
3. **总结检索**（`summaries.preview`）：课堂转写为主；课件按相关页补充，并标明「参考课件（补充，非课堂原话）」；云端仍须预览确认。
4. **实时**：流式临时结果仍纯音频；句界后最多挂起 1 个 polish，不与流式会话并发，不积压，不阻塞录音进程。

## 效果探针（非完整 WER）

样本：既有 L4 30 秒片段（`reports/private/`，不入库）。模型：asr-small。

| 条件 | 结果 |
|---|---|
| 相关合成 OS 课件术语 | 选出 `CreateFile`/`ReadFile`；第二遍仅标点/大小写微调被接受；未见词表灌入句首。`musical` 等明显误识别本轮未纠正。 |
| 无关 NLP 课件 | 无相关术语 → 不跑第二遍；转写与纯音频一致，无灌词。 |
| 故意灌词增强文本 | 接受门拒绝（`unsupported_hint_cluster` 等），保留第一遍。 |

探针命令：`python scripts/probe_guided_asr.py --audio … --synthetic-topic --unrelated-document … --output reports/private/guided-asr-effect-l4.json`

功能回归（93 Python 项）不能代替上述效果核对。完整 WER、延迟、续航与三小时评估仍按原安排暂缓。

## 已知限制

- Qwen3-ASR 仅有 system-prompt 式 `context`，无独立热词/置信度接口。
- 接受门偏保守：宁可漏改，也不易放行数字、否定或提示词堆叠。
- 图片型 PPT / 弱相关长教材的增益有限；需用户实际课堂反馈再调预算与门控。
