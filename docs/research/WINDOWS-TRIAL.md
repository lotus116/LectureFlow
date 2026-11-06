# Windows x64 课堂试用记录 · 2026-11-06

从 main 的 8ea8f52 建立 codex/windows-port。按用户本次授权实施 SD-04/05/06/07 和书签复习的 Windows 入口。此分支供审阅。试用包采用目录分发，复制整个 LectureFlow 文件夹后双击 LectureFlow.exe。模型和运行时均随包提供。

## 上课和复习

1. 选择课程或点“新建课程”。点“开始录音”会新建一节 Lecture，使用系统默认麦克风。默认固定英语，Qwen3-ASR-0.6B INT8 用四个 CPU 线程，Hy-MT2-1.8B Q4_K_M 通过 Vulkan 使用 RTX 3060。仅 CPU 翻译可在开始前勾选。
2. 主窗与置顶字幕窗均可“标记当前”。可暂停、继续，结束或关闭主窗时等待保存。看到“已保存”再关闭电脑。录音存放在本机 LocalAppData 下的 LectureFlow/Library，界面可直接打开本节录音文件夹。
3. 选回课程和 Lecture，点“复习书签”。点上一句或当前句查出处，需要缩小范围时在英文栏拖选连续一至四个词，再点“查课件拼写”。可选择单份课件或全部课程资料。“回听书签区间”播放所示两句对应的原音。课件候选供阅读，不会写回字幕。

## 本机与实现

Windows 11 家庭版 10.0.26200，x64，Ryzen 7 6800H（16 逻辑处理器），15.25 GiB 系统内存。AMD 核显和 RTX 3060 Laptop 共存，nvidia-smi 报告 NVIDIA 专用显存 6144 MiB。开工时系统可用内存约 0.56 GiB，D 盘空余约 238 GiB。使用原生 PowerShell、Git、Python 3.12 和独立虚拟环境构建。

Tk 窗口、PortAudio 采集、ONNX 识别和 GGUF 翻译位于独立 Windows 模块。模型下载固定版本和 SHA-256。翻译子进程仅监听随机的 127.0.0.1 端口，逐次生成访问令牌，客户端绕过环境代理，正常收尾时关闭子进程。课堂数据由本机处理。包内麦克风和文件回放共用 PCM 写盘、推理、字幕和登记路径。推理从已经写盘的帧推进，积压保留在磁盘。复用 CaptionEngine 的临时字幕、前两段上下文回改与收尾，以及原有 SQLite、复习标记、课件解析、拼写查证与暂停区间映射。试用版的英文以增长的短窗口进行多次解码，中文按临时英文刷新，未实现逐字显示翻译 token。

## 最终包验收

公开样本来自 LibriSpeech 的人工参考子集，依原顺序拼接完整语音，以单声道 16 kHz PCM 的真实速度喂入包内文件源。计时从开始采集起，包含模型加载。首条取主窗实际刷新时刻。用小写、去标点并保留英文撇号的词序列计算 WER。[公开音频与参考文本](https://huggingface.co/datasets/hf-internal-testing/librispeech_asr_dummy/tree/main/clean)

| 包内实测 | 音频 | 首条英文 | 首条中文 | 含收尾总时间 | 最终英文 WER |
|---|---:|---:|---:|---:|---:|
| 同段，CPU 识别 + Vulkan 翻译 | 33.06 秒 | 9.26 秒 | 10.78 秒 | 35.23 秒 | 3/83，3.61% |
| 同段，CPU 识别 + CPU 翻译 | 33.06 秒 | 7.55 秒 | 10.27 秒 | 45.42 秒 | 3/83，3.61% |
| 连续文件，CPU 识别 + Vulkan 翻译 | 304.605 秒 | 8.95 秒 | 10.66 秒 | 309.05 秒 | 54/734，7.36% |

短样本实际输出包括 “Mister. Quilter is the apostle of the middle classes, and we are glad to welcome his gospel.”，纯 CPU 最终译文为“奎尔特先生是中产阶级的使者，我们很高兴欢迎他的福音。”两个后端使用同一对基础模型。没有同硬件的 Mac/MLX 对照，无法给出相对 Mac 的质量或速度变化。

- 长回放保存 4,873,679 帧，输入与保存 PCM 的 SHA-256 相同。每十秒采样，最大待识别音频为 4.0 秒，最后一分钟采样为 0 至 2.5 秒，收尾后为零。27 段均锁定，完成 78 次翻译任务，其中 51 次包含后文；回改待办为零。英文有 40 个替换、7 个漏词、7 个插词。此回放未提供课件，从稳定英文到最终英文的专业词纠对 0 处、改坏 0 处；公开样本没有专业词标注，不能据此判断课堂术语召回率。
- 运行中采样，桌面进程峰值工作集约 1.95 GiB，翻译进程约 1.95 GiB（各自峰值）。Vulkan 报告 33/33 层卸载，模型、计算和上下文显存预算约 1443 MiB；某次整卡读数为 3471 MiB，包含其他进程。
- 两份合成 PPTX 与两句本地合成语音使用独立资料库。在打包窗口中等到第二句显示才标记，实际标记时刻 13.50 秒。实际识别将数字写成了 Two Thousand Three；为精确复现指定错字，仅在关闭后的测试库中将该处规范为 2003，再从 EXE 重启。上一句 0–4.75 秒，当前句 6–11 秒，回听计划 0–11 秒。点击回听后采样到应用音频输出会话活动至约 10.53 秒，此后直至第 15 秒均已停止。上一句显示 Cornell 2003 → CoNLL-2003、NLP.pptx 第 3 页及页内原文；当前句首选第 4 页。切换 Geology.pptx 显示无匹配。单份课件与两词选择也通过窗口检查。
- 另一次包内复测，浮窗点标记后主窗同步显示“已标记”，主窗再次点击仅保留一条。录音中直接关闭主窗，进程正常收尾，录音和标记均已入库。
- 真实麦克风使用默认 C-Media 输入，8 秒采集、约 2.06 秒暂停、继续 6 秒，保存 14 秒非静音 PCM，0 次输入溢出、1 个书签、2 段含中文的字幕。归一化 RMS 0.0070、峰值 0.0276。没有人工对读真值，此项验证设备采集和保存，无法评估教室距离的人声准确率。麦克风资料留在独立本地测试库。
- 首轮旧包在中文路径封存时触发音频探测错误。Windows 现直接读 PCM WAV 帧长，并对 ffmpeg 诊断显式解码；最终包的长回放、短回放及真实麦克风均成功登记。还修复了目录同步、Windows 下持有目录内锁时无法移动父目录、UTF-8 和文件名差异。Python 回归 222 项通过，包含第二进程争锁及持锁者退出后的重入；无符号链接权限时目录逃逸用 NTFS junction 验证。

## 复现

在 Windows x64 项目根目录使用 Python 3.12、uv 和 PowerShell。首次获取权重需要联网。

~~~powershell
./scripts/setup-windows.ps1
./scripts/build-windows.ps1
$env:PYTHONUTF8 = "1"
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check src tests
~~~

复测公开样本时，下载上方数据集的 validation parquet，然后运行以下命令，在应用窗口点“开始录音”。每次使用新的独立资料库。

~~~powershell
uv pip install --python .venv/Scripts/python.exe pyarrow soundfile jiwer
.venv/Scripts/python.exe scripts/windows-replay-check.py prepare local/test.parquet local/fixture
dist/WindowsTrial/LectureFlow/LectureFlow.exe --library local/test-library --replay local/fixture/public.wav --metrics local/test-metrics.json
.venv/Scripts/python.exe scripts/windows-replay-check.py score local/fixture local/test-library local/test-metrics.json
~~~

## 使用边界

可以带去做有原音兜底的课堂试用。公开朗读比远场专业课简单，英文专名和中文语义仍可能错，起录到首条中文本机约十秒。纯 CPU 路径短测在音频结束后另用约 12 秒。30 分钟以上持续负载、三小时续航、真实教室噪声、设备切换、磁盘满、异常强杀后的自动恢复及其他 PC 未验收。试用版使用系统默认输入设备；系统麦克风选错时请在 Windows 声音设置切换。自动跟页留待后续。
