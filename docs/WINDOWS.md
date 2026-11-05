# Windows 实验版 MVP

本次贡献按 Windows MVP 和草稿 PR 范围实施，尚非正式 Windows 发行版。
现有 macOS 产品规则和发布流程仍以 PRODUCT、STATUS 为准。

## 启动

Windows x64、Python 3.11/3.12（包含 Tk）与 uv。建议使用较短的源码目录，避免 Windows 路径长度限制。

```powershell
uv sync --frozen --extra dev --extra windows
uv run --frozen --extra windows lectureflow-windows --prepare-models
uv run --frozen --extra windows lectureflow-windows
```

模型准备是主动联网下载约 0.4 GB 权重；不会读取或上传课程内容。
模型为固定版本的 faster-whisper tiny.en 和 Marian opus-mt-en-zh，CPU 运行。
首次准备后可离线运行。模型许可分别见上游模型页面，不将权重提交仓库。

- [英语识别模型](https://huggingface.co/Systran/faster-whisper-tiny.en)
- [英译中模型](https://huggingface.co/Helsinki-NLP/opus-mt-en-zh)

默认使用 LOCALAPPDATA 下独立的 LectureFlow-Windows-Preview 目录。
可通过 `--library-root data/windows-preview/Library` 改用项目内实验库；准备模型与启动时应使用相同参数。
模型选择对话框中分别选择 Models/asr 和 Models/translation 文件夹。
不要将实验入口连接到唯一的现有 Mac 资料库；请先备份。

## 已实现范围

- 创建课程与 Lecture，选择 Lecture 后导入录音，保留源文件与受管副本。
- 默认麦克风开始／结束录音；独立线程写 WAV，有界队列，明确报告写入异常。
- 原始 WAV 始终保留在实验库旁的 Recordings；导入失败时仍可手工重新导入。
- 用户主动启动课后英文识别与中文翻译，约 30 秒区间提交，保留重复运行的历史版本。
- 查看最近结果，打开 TXT、Markdown、SRT、JSON 导出目录；时间戳为处理区间而非逐词对齐。
- 任务运行期间阻止退出，避免正常退出丢弃正在处理的结果。

## 尚未实现与已知限制

实时／悬浮字幕、中文上下文回改、口音复核、课件和术语增强、录音暂停继续、设备选择、任务暂停取消恢复界面、转写编辑、废纸篓与安装包尚未实现。
本入口只处理自己新建的任务，不自动恢复崩溃后任务；已提交区间和原音保留。
不宣称与原 Mac 模型质量一致。Marian 翻译不使用术语或相邻上下文。

Windows 全量测试仍存在目录移动（打开的录音锁文件）、目录 fsync 和符号链接权限问题，相关整理操作未暴露在 MVP 界面。
长路径、硬件权限／切换、休眠、磁盘满、三小时运行和真实课堂准确率均未验收。
下载支持缓存重试，暂无下载进度／暂停界面。

## 验证

本机 Windows：54 项已有主流程测试通过（另排除 2 项符号链接权限测试）；新增 3 项测试覆盖跨进程锁、模拟推理全流程与历史导出、Tk 启动。
真实 CPU 模型加载、单句英译中及 Windows SAPI 合成录音的端到端识别、翻译和导出均已运行成功。合成测试识别出了完整的两句英文，中文结果已保存；这不是课堂质量评估。
麦克风采集未实录验收；合成输入不代表真实课堂质量。

```powershell
uv run --frozen --extra dev python -m pytest -q tests/test_windows.py
uv run --frozen --extra dev ruff check src tests scripts/smoke-live.py
```
