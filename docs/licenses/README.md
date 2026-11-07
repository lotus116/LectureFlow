# 许可与第三方材料

LectureFlow 自有代码采用 **GPL-3.0-only**，全文见根目录 [LICENSE](../../LICENSE)，
覆盖范围和素材例外见 [NOTICE](../../NOTICE.md)。允许商业使用；向他人分发受 GPL
约束的修改版时，需要履行相应源码、许可与声明义务。自用修改不要求公开。

**源码有了许可证，不代表既有安装包、模型和宣传素材已经全部完成合规核查。**
[本轮审计](AUDIT-2026-11-07.md)区分已证实事实、尚缺材料和待确认的使用权；
[发布清单](RELEASE-CHECKLIST.md)规定后续二进制交付门槛。

## 模型

模型权重不由 LectureFlow 重新授权。完整来源链、固定 revision、原始模型卡与
许可证据见 [模型来源](MODELS.md) 和 `model-cards/`。

- Mac 两个 Qwen3-ASR MLX 转换仓库的固定模型卡均声明 Apache-2.0。
- Mac Hy-MT2 MLX 与 Windows 官方 Hy-MT2 GGUF 的固定许可文本为 Apache-2.0，
  带腾讯原始版权声明，不能与其他 Hunyuan 产品的许可混为一谈。
- Windows Qwen ONNX 镜像固定版本没有 LICENSE 或许可元数据。其指向的 ModelScope
  转换源当前标注 Apache-2.0，但未记录镜像对应的准确源 revision。原始 Qwen 模型
  的 Apache 声明与这条来源证据需分别保存，不能把复制一份标准 Apache 文本当成
  镜像作者的授权声明。

## 软件依赖

以每次实际构建的 `Licenses/dependencies.json` 和 `Licenses/dependencies/`
为版本与声明清单。这个清单反映构建环境，不自动证明所有原生库或源码义务已满足。

- imageio-ffmpeg 的 Python 封装为 BSD-2-Clause；实际 Mac FFmpeg 7.1 为
  GPL-2.0-or-later，Windows Gyan FFmpeg 7.1 为 GPL-3.0-or-later。
- Windows pystray 0.19.5 为 LGPL-3.0-or-later；冻结进 EXE 仍需提供许可、对应源码
  和适用的修改／重新构建途径。不能只复制许可证而遗漏这些义务。
- llama.cpp 为 MIT，sherpa-onnx 为 Apache-2.0；随附 DLL、PDFium、TkDND、
  PortAudio 等仍需保留各自完整声明。PyInstaller 有自己的分发例外。
- tqdm 的许可包含 MPL-2.0 与 MIT；regex 的元数据包含 Apache-2.0 与 CNRI-Python。
  不能把所有 Python 包统称为 MIT/Apache，也不能只依赖元数据作兼容性结论。

保留的标准文本：[GPL v2](GPL-2.0.txt)、[LGPL v3](LGPL-3.0.txt)、
[Apache 2.0](Apache-2.0.txt)。GPL v3 见根目录 LICENSE。
