# 架构与模块契约

## 数据与生命周期

Course 和 Lecture 使用稳定 UUID。展示名与身份分离，真实目录采用可读名称加短 ID，避免同名冲突。每节课堂包含 Audio、Documents、Transcripts、Summaries；SQLite 保存索引与关联，文件保持可导出。外部 Finder 修改需要后续重建索引流程。

AudioSegment 保存 source、原始文件名、采集时间、duration、order、chunks 和内容哈希。音频块是实现细节，片段才是用户操作单位。全文使用 segment_id + source_start/end 作为稳定锚点，按当前 order 派生课堂逻辑时间；无法回放的未录音实时字幕需要单独会话锚点。

TranscriptVersion 关联不可变片段顺序快照、模型/术语版本与来源模式。实时识别原文不可被修订覆盖。派生译文保存 revision 与 previous_id。Summary 关联转写版本和课件哈希。素材变化提升 lecture revision，使旧结果过期。

Task 保存 queued/running/paused/cancelled/failed/completed 和已提交检查点。进度按音频源偏移保存，不使用 UI 窗口存活状态。模型运行在专属 worker，存储提交完成后才前移检查点。任务恢复必须幂等。

## 服务边界

| 模块 | 输入 | 输出与约束 |
|---|---|---|
| CourseManager | 文件操作意图、稳定 ID | 受管副本、目录/索引一致；删除确认 |
| SessionController | 录音/英文输出/翻译开关 | capture=任一功能；ASR=英文或翻译；writer=仅录音 |
| AudioService | AVAudioEngine PCM | 独立写入队列与有界 ASR 队列；单调时钟、连续采样编号 |
| ASRAdapter | PCM/文件、术语、模型配置 | 临时/稳定原文、源区间、置信信息（引擎提供时）、错误 |
| TranslationAdapter | 英文、有限上下文、术语表 | 中文文本、推理元数据；本地模型，无自动云端回退 |
| RevisionEngine | 稳定原文和后续上下文 | 最近 1–3 句的版本链；过窗锁定 |
| TaskManager | 范围与不可变素材快照 | 检查点、状态事件、取消/恢复；不与窗口绑定 |
| StorageService | 模型事件、任务检查点、文件引用 | SQLite 事务/WAL；分块文件原子封存；恢复日志 |
| SummaryService | 版本化转写与课件 | 本地 Prompt 或主动云端总结；密钥由 Keychain 获取 |

## IPC 方向

开发版 SwiftUI 通过本地 Python 子进程执行 JSON 命令；实时 PCM 与字幕通过专属 stdin/stdout JSON 行传输，不开放网络端口。资料库与字幕先持久化，再发布界面事件。未来独立服务协议仍可调整，不与特定模型绑定。

采集队列最多保留 32 个 tap 副本，独立的字幕管道与 Python 队列各最多 8 个 250ms 音频包。字幕跟不上时显示中断状态，不阻塞录音；磁盘保存异常则停止采集并保留已有文件。

录音为一个逻辑片段下的多个 CAF 块，封存后写入带哈希的 manifest，旧离线处理器可按源区间跨块解码。录音与字幕保存共享采集 ID 和相对起点。

SQLite V2 新增实时会话、当前字幕及不可变修订历史。字幕输出开关在持久化边界执行，关闭英文后不将识别原文藏入元数据。默认维护最近三个语音片段的中文修订窗口，更旧内容锁定。

OS 模型锁避免离线和实时模型重复常驻；实时优先请求让离线 worker 在区间边界保存检查点并让出模型。只录音不启动模型进程。

## 当前技术决定

- SwiftUI 与 AVAudioEngine 沿用 PRD。
- Python 3.12 项目内环境；ASR 候选使用固定发布版 `mlx-qwen3-asr==0.4.4`。
- 翻译候选是 Hy-MT2-1.8B，优先验证 MLX 原生架构；GGUF/STQ 路径保留兼容性检查。
- 模型缓存放在项目目录，下载与本地推理分别执行；本地基准默认禁止 Hugging Face 联网。
- 不在 Phase 0 添加账号、云同步、商业实时 ASR 或复杂 Agent。
