<p align="center">
  <img src="docs/assets/hero.png" alt="LectureFlow · 把每一堂课，留在本地。" width="100%">
</p>

<p align="center">
  <strong>专注听课，让录音、英文转写和中文翻译留在你的 Mac。</strong>
</p>

<p align="center">
  <a href="https://github.com/lotus116/LectureFlow/releases/latest"><img src="https://img.shields.io/github/v/release/lotus116/LectureFlow?style=flat-square&label=体验版&color=2563A6" alt="最新公开版本"></a>
  <img src="https://img.shields.io/badge/平台-Apple_Silicon-2563A6?style=flat-square" alt="Apple Silicon">
  <img src="https://img.shields.io/badge/macOS-26%2B-2563A6?style=flat-square" alt="macOS 26 及以上">
  <img src="https://img.shields.io/badge/处理方式-本地运行-367A69?style=flat-square" alt="录音、识别、翻译和资料解析均在本机处理">
</p>

<p align="center">
  <a href="https://github.com/lotus116/LectureFlow/releases/latest"><strong>下载体验版</strong></a>
  &nbsp; · &nbsp;
  <a href="docs/USER_GUIDE.md">使用指南</a>
  &nbsp; · &nbsp;
  <a href="#从源码体验">体验最新源码</a>
  &nbsp; · &nbsp;
  <a href="https://github.com/lotus116/LectureFlow/issues">反馈问题</a>
</p>

<br>

## 从上课到课后，一处完成

| 上课时，专注听 | 下课后，方便查 | 课程资料，放进来就好 |
| :--- | :--- | :--- |
| 录音、暂停和继续；用悬浮双语字幕跟上课堂。也可以仅录音或仅字幕。 | 同一页查看实时记录与课后转写，逐段修改、搜索、复制和导出。 | 批量导入 PDF/PPT，自动整理辅助术语，供课程内所有 Lecture 共享。 |

录音、识别、翻译和资料解析均在本机完成。模型下载完成后，上述处理可离线运行。

## 开始使用

**Apple Silicon Mac · macOS 26+ · 建议至少 16 GB 内存**

1. 从 [发布页](https://github.com/lotus116/LectureFlow/releases/latest) 下载压缩包，解压后将 **LectureFlow.app** 拖入“应用程序”。
2. 新建课程，点击 **开始新 Lecture**，或导入已有录音。
3. 按需添加课程资料；上课看悬浮字幕，课后在转写页查看和整理文字。

发布包自带运行环境，无需 Python 或 Xcode。首次使用需联网准备约 **1.86 GB** 默认模型，支持暂停与继续；准备期间可以先管理课程或仅录音。

> **公开下载与最新源码可能不同步。** 当前源码为 0.6.0 简化版；下载前请查看发布说明。要体验仓库里的最新改动，请按下面的源码步骤启动。

## 从源码体验

需要已安装的 Xcode 与上述系统环境。在终端运行：

```sh
git clone https://github.com/lotus116/LectureFlow.git
cd LectureFlow
zsh scripts/setup
zsh scripts/run
```

已有项目时，先退出旧应用，同步代码后运行 `zsh scripts/run`。开发版依赖项目目录中的环境；构建、测试和独立打包见 [开发指南](docs/DEVELOPMENT.md)。

## 体验版说明

- 识别准确率、翻译质量、实时延迟、续航及三小时连续使用评估仍暂缓，尚未验收。
- 离线字幕时间戳目前约为 30 秒处理区间，尚未逐句精确对齐。
- 图片型课件和复杂版面可能需要本地 OCR 或先转为 PDF；辅助术语仍可能有误。
- 应用使用本地签名，尚未经过 Apple 公证。安装与权限处理见 [使用指南](docs/USER_GUIDE.md)。

反馈时请描述操作、预期和实际结果；无需默认上传原录音、课件或含隐私的日志。

---

<p align="center">
  <a href="docs/STATUS.md">实际进度</a>
  &nbsp; · &nbsp;
  <a href="docs/PRODUCT_DESIGN-2026-10-31.md">产品设计</a>
  &nbsp; · &nbsp;
  <a href="docs/UI_REDESIGN_BRIEF.md">界面设计方向</a>
  &nbsp; · &nbsp;
  <a href="docs/DEVELOPMENT.md">开发与构建</a>
</p>

<p align="center"><sub>琪露诺主题为非官方同人视觉，与原作及玩偶制造商无官方关联。Logo 与横幅使用 AI 辅助创作。</sub></p>
