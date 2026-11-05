# LectureFlow 文档入口

LectureFlow 是面向英语课堂的本地 macOS 录音、英文转写与中文翻译应用。当前发布信息、实现差异和下一步统一见 [STATUS](STATUS.md)；本文不维护第二份进度。

## 按目的阅读

| 你要了解什么 | 阅读入口 |
|---|---|
| 安装并开始使用 | [使用指南](USER_GUIDE.md) |
| 软件应该怎样工作、哪些规则不能改 | [已确认需求](PRODUCT.md) |
| 当前做到哪、有什么问题、下一步是否已批准 | [当前状态与需求追踪](STATUS.md) |
| 模块和数据如何协作 | [架构](ARCHITECTURE.md) → 按主题深入 |
| 如何开发、检查和发布 | [开发指南](DEVELOPMENT.md) |
| 为什么这样决定、旧需求是否仍有效 | [决定依据](design/DECISIONS.md) |
| 声音实验具体测了什么 | [语音增强研究](research/SPEECH_RESEARCH-2026-11-05.md) |
| 口音复核为何没收益、下一步依据 | [离线归因与候选对照](research/ACCENT_DIAGNOSIS-2026-11-05.md) |
| 真实课堂的原音、课件与中文回改对照 | [课堂基线与有限对照](research/CLASSROOM_BASELINE-2026-11-05.md) |
| 中文回改串段修复前后的具体结果 | [目标隔离修复与离线对照](research/TRANSLATION_ISOLATION-2026-11-05.md) |
| 找旧文档、旧测试或原始 PRD | [整理对照](archive/2026-11-05/MIGRATION.md)与[历史入口](archive/README.md) |

日常主文档只有本页、USER_GUIDE、PRODUCT、STATUS、ARCHITECTURE、DEVELOPMENT 六份。根目录保留 RELEASE 文件和旧研究链接入口以兼容公开链接及发布脚本，不作为另一套当前设计。

## 维护与读取规则

1. AI 开始任务先读仓库 AGENTS、PRODUCT、STATUS；根据任务再读对应 design 文档。改变已有规则时沿来源回查原始 PRD 和相关确认记录，不必每次通读所有历史。
2. PRODUCT 记录已确认行为；STATUS 记录实现／验证／批准状态；design 记录技术细节；research 记录实验；archive 仅追溯。不同类型的内容互相引用，不重复维护事实。
3. 用户明确确认的变更才可替代旧需求。文件日期新、模型能力强、代码已经这样写，都不自动构成产品批准；冲突未决就列出，不猜测。
4. 每次变更同步所属文档和 STATUS 的具体行；记录证据与限制。文档必须区分已批准未做、部分实现、已实现未验收与已验证范围。
5. 不把私人录音、课件、逐字转写、完整个人路径或原对话加入仓库。事实摘要和批准范围可记录，私有证据只留本机。
