# LectureFlow

面向英语课堂的本地 AI 助手，让录音、双语转写和课程资料放在一起。

**当前可用：** macOS 原生界面、课程与课堂创建、多段录音导入及拖动排序、本地英文识别与中文翻译、后台暂停/继续/取消、转写版本与搜索、Markdown/TXT/SRT/JSON 导出。

**实验功能：** 麦克风录音、英文转写、中文翻译可独立控制，支持有限修订和录音恢复。实时流程已通过文件回放验证，麦克风设备与三小时连续使用仍待验收。

**开发中：** 课件增强、悬浮字幕、完整文件管理、AI 总结。离线字幕时间戳约每 30 秒一段，识别与翻译仍需核对。

## 开始使用

需要 Apple Silicon Mac、macOS 26 以上及已安装的 Xcode。建议至少 16GB 内存；当前固定版本的本地推理依赖要求 macOS 26。

```sh
git clone https://github.com/lotus116/LectureFlow.git
cd LectureFlow
zsh scripts/setup
zsh scripts/run
```

首次安装需联网下载运行环境和约 2GB 模型；之后录音转写与翻译在本地执行。使用高精度模型时，先运行 `.venv/bin/lectureflow fetch asr-large`。

打开应用后：**新建课程 → 新建课堂 → 导入录音 → 调整顺序 → 转写整堂课**。关闭窗口后后台任务继续；“导出”会打开结果文件夹，其中也包含可复制到 AI 工具的总结 Prompt。

实时模式可点“全部开启”，或分别控制三个开关；首次使用需要允许麦克风。关闭“保存录音”后只处理字幕，不保存原始音频。

## 项目结构

```text
native/           macOS 原生界面
src/lectureflow/  本地处理、资料库与任务
tests/            行为与恢复测试
scripts/          安装、启动、构建与检查
docs/             PRD、计划、架构与开发进度
```

录音副本保存在 `data/library/`，模型保存在 `models/`。资料、模型、密钥和私有报告均不提交到仓库。当前为开发版本，应用依赖本项目目录中的运行环境。

[需求文档](docs/PRD-v1.4.md) · [实施计划](docs/IMPLEMENTATION_PLAN.md) · [当前进度](docs/STATUS.md) · [开发指南](docs/DEVELOPMENT.md)
