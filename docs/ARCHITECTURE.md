# 当前架构与实现边界

产品目标见 [PRODUCT](PRODUCT.md)，缺口和验证见 [STATUS](STATUS.md)。本页描述 0.6.3 代码，不把未来方案当现有服务。

## 数据与身份

Course→Lecture 使用稳定 ID；课程统一拥有资料和辅助术语，Lecture 拥有录音、实时会话与课后转写。展示名称不充当身份。真实目录与 SQLite 配合；旧数据库／目录可仍含 Documents、Summaries 等兼容结构，但总结接口已禁用，不代表产品保留总结功能。

AudioSegment 保存源、顺序、时长、块和哈希；用户片段为开始到结束，内部暂停块隐藏。字幕保留采集 ID／源片段与源时间，课后逻辑时间按任务的顺序快照派生；未录音字幕没有可回放原音。重排素材不原位改写旧版本。

课后版本绑定不可变素材、模型和术语快照；人工修改与历史受保护。实时当前字幕与修订历史由 live_store 持久化，保存边界遵守英文／中文开关。实时人工编辑入口及统一导出仍存在 A03 缺口。

## 模块与数据流

| 实现入口 | 职责与边界 |
|---|---|
| [原生应用](../native/Sources/LectureFlow/)／[Core](../native/Sources/LectureFlowCore/) | SwiftUI、AVAudioEngine、权限、独立写入和子进程通信 |
| [live.py](../src/lectureflow/live.py)／[live_store.py](../src/lectureflow/live_store.py) | 流式识别、必做中文回改、可选课件 polish、当前字幕及修订保存 |
| [recordings.py](../src/lectureflow/recordings.py) | CAF 块登记、哈希、恢复与跨块解码；不修改源录音 |
| [worker.py](../src/lectureflow/worker.py)／[library.py](../src/lectureflow/library.py) | 后台任务、快照、检查点与事务；课后相邻上下文仍缺 A02 |
| [documents.py](../src/lectureflow/documents.py)／[knowledge.py](../src/lectureflow/knowledge.py) | 课程资料、解析、术语和补全；增量能力缺口见 A04/A05 |
| [adapters.py](../src/lectureflow/adapters.py)／[models.py](../src/lectureflow/models.py) | 本地 Qwen ASR 与 HyMT 翻译、固定模型版本和下载 |
| [exporting.py](../src/lectureflow/exporting.py)／[files.py](../src/lectureflow/files.py) | 转写导出、受管文件与废纸篓；没有云总结导出 |

SwiftUI 通过本地 Python 子进程 JSON 命令和 stdin/stdout JSON 行交换 PCM／事件，不开放推理网络端口。模型下载联网不等于上传课堂内容。应用的所有者入口和协议详见 [DEVELOPMENT](DEVELOPMENT.md)。

## 调度、恢复与容量

- 录音写入不等待 ASR／翻译；原生 tap 复制队列有界，字幕管道另行排队。
- 原生待写字幕音频上限八包；Python 输入队列为 240 包，音频通常每包 0.25 秒，控制消息也占容量。持续超载仍可丢字幕音频，不能因此声称原音已丢或永不丢字幕。
- ASR 片段最长约 12 秒；最近三段保留可回改窗口。稳定初译和前两段上下文回改按目标合并且保留最初等待时间，移出窗口／正常停止前完成再锁定。
- 课件二次识别是另一个可选单槽任务，仍可能被更新替代；新口音原音复核队列未实现。具体当前参数见 [实时设计](design/REALTIME.md)。
- 模型使用 OS 锁，实时优先让后台在区间提交点让路；只有录音时不启动模型。当前 Python 推理串行，不能假定一次模型调用可随时抢占。
- 离线任务有持久化检查点；实时回改待办仍在内存。正常退出等待保存与字幕进程结束，不等于强杀后可恢复所有待办。

## 运行路径与模型

[RuntimePaths](../native/Sources/LectureFlow/RuntimePaths.swift)区分两种环境：开发版使用项目环境与项目 data/models；独立包使用内置 Python、用户应用支持目录的 Models/Library。环境注入与已保存 libraryPath 可覆盖默认库位置；兼容检测可能沿用已有开发资料库，不能声称每次安装都会迁移到默认目录。备份迁移是经用户批准的具体操作。

模型候选由 models.py 固定提交，依赖由 uv.lock 固定；已有任务保留模型快照。ASR 默认固定英语，中文翻译使用本地 HyMT，无自动云回退。独立包可搬移、默认不内置模型；开发包依赖源码目录。详见 [开发／打包](DEVELOPMENT.md)。

## 按主题深入

- [实时识别、翻译回改、退出与诊断](design/REALTIME.md)
- [课程资料和术语可信度](design/MATERIALS.md)
- [视觉资源与仍适用的约束](design/VISUAL.md)
- [待讨论的缺口与语音方案](design/OPEN_ITEMS.md)
- [原始架构快照](archive/2026-11-05/snapshot/docs/ARCHITECTURE.md)仅供历史追溯
