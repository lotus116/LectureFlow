# 开发、检查与发布

先读 [PRODUCT](PRODUCT.md) 与 [STATUS](STATUS.md)，再按 [ARCHITECTURE](ARCHITECTURE.md) 找所属模块。旧交接与早期计划只作为历史，不直接当任务执行。

## 环境与常用命令

使用项目隔离环境、固定 uv.lock；模型候选版本由 src/lectureflow/models.py 固定，不在恢复旧任务时静默换权重。正式包分别面向 Apple Silicon / macOS 26+ 和 Windows 11 x64。下列 zsh 命令适用于 Mac，Windows 构建命令见本页“独立打包与正式发布”。

```sh
zsh scripts/setup
zsh scripts/dev
zsh scripts/build-app
zsh scripts/test-native
zsh scripts/run
```

build-app 产出开发包，依赖项目目录；test-native 包含原生测试与隔离退出探针，不打开麦克风。目录迁移后的旧虚拟环境入口或 Swift 生成缓存可能绑定旧路径，应重建环境／缓存，不把这类失败直接当产品功能回归。

无模型测试：

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev ruff check src tests scripts/smoke-live.py
uv run --frozen --extra dev pytest -q
python3 scripts/check-docs.py
python3 scripts/check-licenses.py
```

已有项目环境可用 `.venv/bin/python -m pytest -q`；环境安装仍应通过 setup／uv 维护。生成目录 data、models、.venv、.cache、.tools、.build、dist、reports/private 不入库。不要用私人课堂音频、课件、转写或完整个人路径做测试夹具。

## 后台接口与数据

以下命令直接调用本机 Python 后台。Windows 翻译子进程只监听本机回环地址 127.0.0.1。

```sh
.venv/bin/python -m lectureflow.cli library status
.venv/bin/python -m lectureflow.cli library create-course '课程名称'
.venv/bin/python -m lectureflow.cli library create-lecture COURSE_ID 'Lecture 名称'
.venv/bin/python -m lectureflow.cli library import LECTURE_ID '/path/part1.m4a'
.venv/bin/python -m lectureflow.cli library enqueue LECTURE_ID
.venv/bin/python -m lectureflow.cli worker
```

`library task TASK_ID pause/resume/cancel/retry` 控制任务；`library content VERSION_ID` 查看结果；`library export VERSION_ID` 重建导出而不重复推理。实际参数以 CLI help 与代码为准。

课后按约 30 秒源区间处理，暂停／取消在检查点边界检查，未提交区间不提交；输出与检查点事务提交，单 worker 持锁后恢复，取消为终态，恢复应幂等。任务保留素材顺序、模型与词表快照；当前课后翻译未传相邻原文（A02），不能用实时中文回改代替此项。

源文件使用受管副本、哈希与源时间映射；重排保留旧版本。JSON 保留元数据，英文／中文保存边界仍适用。exporting 提供纯转写导出，不提供总结 Prompt 或云调用。模块入口见 ARCHITECTURE，具体实时队列／退出协议见 [REALTIME](design/REALTIME.md)。

实时后台的 `live run` 支持 `--temporary-refresh-seconds 1|3`，默认1；3为显式实验节流，仅影响已经显示中文后的临时刷新，首次中文和必要回改保持。正常桌面调用不加该参数，保持原刷新频率。省计算不代表首译一定更快，证据见 STATUS。

## 独立打包与正式发布

Windows x64 使用原生 PowerShell、Python 3.12 与 uv，运行 `scripts/setup-windows.ps1`、`scripts/build-windows.ps1` 和 `scripts/package-windows-release.ps1`。默认模型下载后按固定 SHA-256 核对；打包 EXE 用临时资料库检查后台启动。双平台手动工作流从同一提交构建，两个任务成功后统一生成 SHA256SUMS 并发布。

```sh
zsh scripts/package-app --zip
```

输出 dist/LectureFlow.app 与版本 ZIP，包含可搬移 Python 和后端。默认不内置模型；首次准备模型可联网下载并复用应用支持目录缓存。`--with-models` 仅在明确要求离线分发时使用。logo、USER_GUIDE、licenses 路径为打包依赖，文档整理不能随意移动。

VERSION、pyproject.toml、uv.lock 与 Python 包版本保持一致。更新版本号不自动发布；用户明确要求正式交付后，先确认目标平台。现有 Release macOS and Windows 工作流会同时构建并发布两端，仅适用于双平台正式发布。Mac 单独发布须逐项核对 Mac 实际产物。工作流读取 `docs/RELEASE-版本号.md`，因此发布说明原路径保留；不要因整理文档触发发布或覆盖公开附件。

发布前先按[许可检查清单](licenses/RELEASE-CHECKLIST.md)核对对应源码、第三方声明与素材授权。Mac 单平台先运行 `python3 scripts/check-licenses.py`，使用 `zsh scripts/package-app --zip --ffmpeg-root <独立构建目录>` 随包提供 FFmpeg 的对应源码和构建记录。双平台合发另运行 `python3 scripts/check-licenses.py --release` 检查两端的待办。两端打包生成 `Licenses/dependencies.json`，Windows 专用 pystray hook 外置源码仍需实际替换加载验收。

发布检查包括代码／数据保护、相关行为测试、独立构建、签名、包内后端启动与源文件一致性；发布后核对标签提交、CI、下载附件版本与 SHA256SUMS。应用尚未 Apple 公证。0.6.3 的一次发布授权不表示未来每次修改均可正式发布。

## 文档同步与审查

- 需求变化先确认，改 PRODUCT 和决定依据；实现／验证／缺口只改 STATUS 的对应 SD/LF/A 行；技术细节放所属 design 文档，实验放 research。
- 保留失败证据、来源和验收限制。旧文档迁移先做映射并归档，不能只靠重新总结后删除原文。
- 每次检查 `python3 scripts/check-docs.py`、`git diff --check` 与暂存范围；脚本核对历史正文完整性、旧文档去向、需求编号、本地链接／锚点、打包依赖及版本。
- 纯文档改动无需重新运行课堂模型；修改可执行检查脚本要实际运行。涉及状态机、存储、恢复或应用行为时执行对应测试，不能以文档检查替代。
- 推送前审查没有私人材料、模型、密钥或个人完整路径。推送后核对远端提交与 CI；不得以新文档措辞掩盖未完成项。

## 实测边界

文件回放可用 scripts/smoke-live.py，但不会打开麦克风，不能替代权限、设备、休眠或硬件故障验收。模型 WER 需要人工／可靠参考；RTF 不等于首个有意义译文延迟，RSS 与 MLX 峰值不能简单相加。研究资料见 [research](research/SPEECH_RESEARCH-2026-11-05.md)，旧实验协议与所有未关闭门槛见 STATUS。

## 口音本地测试包

`zsh scripts/package-app --zip --test-build` 在独立的 `dist/accent-test-提交/` 目录生成包，不覆盖原应用或0.6.3包。使用独立bundle ID，包内build-info.json记录来源提交及非正式发行标识；仍不含模型和私人资料。不创建Release或tag。

复习标记测试使用 `zsh scripts/package-app --zip --test-build --test-kind review`，输出至 `dist/review-test-提交/`，应用名称和bundle ID独立于口音测试及正式包。首次试用前结束并退出旧应用，避免多个应用同时打开同一资料库。


## 并排本地界面版（2026-11-28）

以下步骤生成使用独立资料库的本地测试应用。用户下载的 Mac 0.6.6 采用正式图标与常规应用标识；发布提交、校验值和正式包验证见[当前状态](STATUS.md)。

用户批准保留原Logo画面、仅将Dock图标外角改为透明圆角，并安装独立新版查看。
原 `docs/assets/logo.png` 不改；`make-app-icon.swift` 在打包时对各尺寸的缩放图只添加连续圆角alpha，
保留RGB、图案及配色；不借用设计候选，不假定系统替旧ICNS裁角。

`package_app.py --test-build --test-kind interface` 生成 **LectureFlow 新版.app**，独立bundle ID
`local.lectureflow.interface-test`，并写入 `LectureFlowIsolatedStorage=true`。
该标志令原生RuntimePaths默认使用应用支持目录的 **LectureFlow Interface** 子目录，
不自动发现/接入原开发库或模型缓存；旧应用仍使用LectureFlow目录，既有用户默认不变。
新版偏好由独立bundle隔离；显式环境/用户资料库选择仍遵守原约定，不自动迁移资料。

这是正常发布配置编译的产品代码，**不是合成预览**；不会替换麦克风、ASR或翻译。
本地候选 `official_release=false`，不构成发布/上传授权；安装与打开由主会话处理。

可在隔离checkout直接调用已存在的依赖解释器运行打包工具（不复制开发虚拟环境）：

```sh
PYTHONPATH=src /path/to/existing/.venv/bin/python scripts/package_app.py \
  --test-build --test-kind interface --with-models \
  --runtime-source /path/to/existing/LectureFlow.app --model-source /path/to/local/models
```

runtime-source只读复用签名有效、应用与后台轮子均与当前项目同版本且满足当前依赖声明的运行组件；
工具重新构建并强制安装当前源码wheel，记录依赖版本，去除本地安装来源和无用的开发路径入口脚本。
界面版with-models打包三种既有catalog模型，以满足默认高精度选择；公共模型源只读复制，清单改为包内相对路径。
包内后台在全新临时库执行模型状态与SQLite初始化检查，最终进行深度严格签名检查。
不启动正式GUI、不录制用户音频、不接入现有资料，真实麦克风/课堂转写另行实测。
