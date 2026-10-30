# 课程资料辅助识别与总结 · 交接状态

> 历史交接记录：后续范围已由 [2026-10-31 简化版设计](PRODUCT_DESIGN-2026-10-31.md) 调整。课程上下文、摘要/API 和本课专属资料不再属于新版范围；下文仅描述 0.5.0 历史实现。

状态：0.5.0 已按确认方案实施并推送候选；正式发版仍手动。效果探针见 `GUIDED_ASR_0.5.md`，完整 WER/长时评估仍暂缓。

## 用户目标（已落地方向）

上传课程 PDF/PPT 后自动辅助识别与转写、丰富课后摘要上下文；无需逐词确认。原始识别与历史版本保留；默认本地；云端总结需确认。

## 实现入口

- `src/lectureflow/inference_policy.py` — 选择器、接受门、总结检索
- `src/lectureflow/worker.py` — `recognize_unit` 离线双遍
- `src/lectureflow/live.py` — 句界单槽 polish
- `src/lectureflow/summaries.py` — 课堂为主的相关课件材料
- `scripts/probe_guided_asr.py` — 有/无资料效果探针

## 仍需关注

- 门控保守导致部分真实误识别未改写；预算与阈值需按课堂反馈再调。
- 实时 polish 在句界执行，高负载时主动丢弃增强以保录音与字幕不积压。
