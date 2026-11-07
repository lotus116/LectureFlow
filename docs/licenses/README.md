# 许可与第三方材料

LectureFlow 自有代码采用 **GPL-3.0-only**，全文见根目录 [LICENSE](../../LICENSE)，
覆盖范围和素材例外见 [NOTICE](../../NOTICE.md)。允许商业使用；向他人分发受 GPL
约束的修改版时，需要履行相应源码、许可与声明义务。自用修改不要求公开。

**源码有了许可证，不代表既有安装包、模型和宣传素材已经全部完成合规核查。**
[本轮审计](AUDIT-2026-11-07.md)区分已证实事实、尚缺材料和待确认的使用权；
[发布清单](RELEASE-CHECKLIST.md)规定后续二进制交付门槛。

## 发布修改版意味着什么

选择 GPL-3.0-only 是为了让收到修改版的人继续拥有查看、修改和再分发源码的权利。
以下是便于理解的说明，不替代 [GPL v3 正文](../../LICENSE)，也不附加新的限制。

| 使用方式 | 需要做什么 |
|---|---|
| 自己使用或私下修改，不向他人分发 | 不要求公开修改，也不要求向本项目提交代码。 |
| 向他人分发受 GPL 约束的修改版，包括收费出售 | 受覆盖的修改作品须按 GPL v3 授权；保留版权、许可和免责声明，明确标注修改及相关日期。不能把修改版变成限制接收者复制、修改和再分发的闭源产品。 |
| 提供安装包或可执行文件下载 | 按 GPL 第 6 条提供与该二进制对应的完整源码，包括适用的构建、安装脚本；在线分发通常可在同一下载位置明确提供免费源码下载入口。只链接原项目或一个不断变化的主分支不够。 |
| 收费、商用或提供付费支持 | 允许；收费不免除源码与许可义务，也不能禁止接收者依法再分发。 |
| 仅在服务器运行，用户通过网络使用 | 普通 GPL 不因这种网络交互本身触发 AGPL 式的源码提供义务；若另行分发程序副本，仍须遵守 GPL 的分发要求。 |

这里的“开源”准确说是**向收到程序的人提供相应源码和 GPL 权利**，不强制每个
私人修改都公开到 GitHub，也不强制修改者向本项目提交 PR。接收者有权继续公开
这些源码。独立作品、第三方模型和素材的许可范围另行判断，不能把整个安装包中
所有内容一概重新标为 GPL。

例如：有人修改 LectureFlow 后发布收费安装包，仍需提供该修改版本对应的源码、
保留 GPL 声明并说明修改；仅提供我们原版的仓库地址不能替代自己的修改源码。

官方解释：[私下修改与公开源码](https://www.gnu.org/licenses/gpl-faq.en.html#GPLRequireSourcePostedPublic)
· [GPL v3 第 5、6 条](https://www.gnu.org/licenses/gpl-3.0.en.html)。

## 视频 BGM 与视觉素材

README 视频的音轨来源为休憩うさぎ（[@QK_rabbit_2 原帖](https://x.com/QK_rabbit_2/status/1968963279730233345)），
帖文标题为「おてんば恋娘 ～笛のテストに向けて～」。来源署名已保留在视频下方。
**署名不是授权**；目前保留“再用许可待核实”的状态，不把这段音乐作为 GPL 素材
授权他人复用。音乐及琪露诺／东方／fumo 相关图像的权利边界见
[NOTICE](../../NOTICE.md)，来源核查与未完成事项见[素材审计](AUDIT-2026-11-07.md)。

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
