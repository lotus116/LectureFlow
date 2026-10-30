# 开发指南

## 目录与职责

| 目录 / 模块 | 职责 |
|---|---|
| `native/Sources/LectureFlow/` | SwiftUI 界面、资料库状态、Python 子进程通信 |
| `src/lectureflow/library.py` | SQLite、受管录音副本、排序、版本与任务检查点 |
| `src/lectureflow/worker.py` | 独占后台 worker、分段处理、恢复和取消检查 |
| `src/lectureflow/adapters.py` | 本地 ASR 与翻译适配 |
| `src/lectureflow/live.py` / `live_store.py` | 有界实时推理、输出约束、字幕与修订持久化 |
| `src/lectureflow/recordings.py` | CAF 录音登记、恢复、校验与跨块解码 |
| `native/Sources/LectureFlowCore/` | 可测试的采集路由、录音器及推理管道保护 |
| `src/lectureflow/exporting.py` | 双语转写和总结 Prompt 导出 |
| `src/lectureflow/benchmark.py` / `stream_probe.py` | 离线基准与真实节奏短测 |
| `tests/` | 文件保护、任务状态、恢复、时间映射和导出测试 |
| `docs/` | 产品基线、阶段验收、架构和实际状态 |

生成的 `data/`、`models/`、`.venv/`、`.cache/`、`.tools/`、`.build/`、`dist/` 与 `reports/private/` 不进入仓库。不要将个人音频、课件、转写或路径放入测试夹具。

## 常用操作

```sh
zsh scripts/dev          # 固定依赖、代码检查与 Python 行为测试
zsh scripts/build-app    # 构建并本地签名 macOS 应用
zsh scripts/test-native  # 原生合成采集与故障测试，不打开麦克风
zsh scripts/run          # 构建并启动应用
```

无模型、无 GPU 的测试环境：

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev ruff check src tests scripts/smoke-live.py
uv run --frozen --extra dev pytest -q
```

模型版本固定在 `models.py`，依赖固定在 `uv.lock`。更改候选模型必须同时记录评估证据，不能让旧任务在恢复时静默换用新权重。

## 后台接口

SwiftUI 使用本地 Python 子进程的 JSON 命令接口，未开放网络端口。

```sh
.venv/bin/lectureflow library status
.venv/bin/lectureflow library create-course '课程名称'
.venv/bin/lectureflow library create-lecture COURSE_ID '课堂名称'
.venv/bin/lectureflow library import LECTURE_ID '/path/part1.m4a' '/path/part2.m4a'
.venv/bin/lectureflow library enqueue LECTURE_ID
.venv/bin/lectureflow worker
```

`library task TASK_ID pause/resume/cancel/retry` 控制任务；`library content VERSION_ID` 查看结果；`library export VERSION_ID` 重建导出，不重复推理。

暂停/取消在当前约 30 秒处理区间完成后检查，未提交结果丢弃；已经提交的区间和检查点在同一 SQLite 事务中保存。单个 worker 由 OS 文件锁保护；窗口关闭不会终止 worker，进程中断后下次启动从已提交区间恢复。取消是终态，继续处理需创建新版本；失败可重试，暂停可恢复。

## 数据与限制

课堂是持久容器，不是一次任务。排序变化提升素材 revision，旧版本保留原顺序快照并标记过期。音频使用受管副本；导入失败不会提交半个批次。手动改变受管音频会触发哈希检查失败。

JSON 保存原始识别、源片段与源时间、课堂逻辑时间、模型提交和运行元数据。当前 SRT 是约 30 秒处理窗口，尚未逐词对齐。跨文件的翻译会读取有界前文；完整字幕语义修订引擎尚未实现。

开发版 `.app` 的 Info.plist 记录项目运行环境位置，不能单独复制给其他电脑使用。独立打包、模型下载 UI、Keychain 和可选总结 API 属于后续交付。

原生界面可在较早 SDK 上编译，但固定的 MLX 0.32.4 发行包要求 macOS 26，因此完整应用按 macOS 26 以上声明支持。无模型的 CI 不受该推理依赖限制。

## 提交与审查

实时模式的数据流、8 种开关组合及设备验收边界见 [LIVE.md](LIVE.md)。`scripts/smoke-live.py` 可用已有音频验证真正的实时子进程，不读取麦克风。实时首次使用需用户在系统弹窗中授权，构建检查和文件回放不能替代硬件验收。

按小范围功能提交；同时更新实际状态。推送前检查已暂存文件，确认没有录音、模型、用户课件、转写、密钥或个人路径。代码和原生构建检查通过后再推送；模型准确率和长时实时可靠性需要独立实测，CI 通过不能代替产品验收。

## 0.2.0 独立应用与发布

`zsh scripts/package-app --zip` 构建 `dist/LectureFlow.app` 及分发 ZIP，内含可搬移的 Python 运行环境和后端代码，不依赖源码目录。默认不内置模型：首次打开自动后台下载默认识别/翻译模型，可暂停、继续，缓存保留在用户应用支持目录。

可选 `--with-models` 仅供明确需要的离线分发；普通发布不使用。开发应用改为 `dist/dev/LectureFlow.app`，避免覆盖分发包。

源码职责：`native/` 原生界面与采集；`src/lectureflow/` 本地模型、资料库、课件和任务；`tests/` 存储/恢复等行为检查；`scripts/` 环境、构建和检查；`docs/` 产品、使用与实现说明。生成内容放 `.build/`、`dist/` 等忽略目录。

`VERSION` 与 pyproject 版本保持一致。main 的版本/发布流程变更触发一次构建：先运行功能检查，再打包并创建 GitHub Release，上传 ZIP 和 SHA256SUMS 后公开。既有公开版本不自动覆盖。没有创建定时 Codex 任务。
