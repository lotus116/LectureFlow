# 模型权利来源与固定证据

模型权重不受 LectureFlow 的 GPL 授权。以下是 2026-11-07 对实际配置的核查，
不是从模型家族名称推断。原始文件保留在 `model-cards/`，仅将 Markdown 文件的
本地扩展名改为 `.txt`，内容字节不变。`manifest.json` 记录下载 URL、SHA-256、
原文件名与取得日期；检查脚本验证原文未改。

| 使用位置 | 固定模型与 revision | 已证实许可及范围 |
|---|---|---|
| Mac 标准识别 | [moona3k/mlx-qwen3-asr-0.6b-8bit](https://huggingface.co/moona3k/mlx-qwen3-asr-0.6b-8bit/tree/83ce2a8ef9a382d2ba2209aab383bc51fff366f5) | 固定模型卡明确 `apache-2.0`，说明基于 Qwen/Qwen3-ASR-0.6B 的 MLX 8-bit 转换 |
| Mac 高精度识别 | [moona3k/mlx-qwen3-asr-1.7b-8bit](https://huggingface.co/moona3k/mlx-qwen3-asr-1.7b-8bit/tree/22c8abe6a6772122dda5905967d7496d1d3e8dd2) | 固定模型卡明确 `apache-2.0`，说明基于 Qwen/Qwen3-ASR-1.7B 的 MLX 8-bit 转换 |
| Mac 中文翻译 | [mlx-community/Hy-MT2-1.8B-4bit](https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit/tree/e5c6fe56c7b3bc77fae5ae92db31f2178f1e6912) | 固定 LICENSE.txt 为 Apache-2.0，保留腾讯版权；MLX 4-bit 转换 |
| Windows 识别 | [csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25](https://huggingface.co/csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25/tree/68818b2313fe77bd06f6a7c5068ff3ef59d02b8a) | 镜像固定版本没有许可标签／LICENSE；README 指向下面的 ModelScope 转换源，不能声称镜像提供了完整授权 |
| Windows 中文翻译 | [tencent/Hy-MT2-1.8B-GGUF](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/tree/a0c709d9fac510f2c807aa3af52872340dc37a4a) | 固定 LICENSE.txt 为 Apache-2.0，保留腾讯版权；使用 Q4_K_M GGUF |

## 基础模型与转换链

- 官方 [Qwen 0.6B](https://huggingface.co/Qwen/Qwen3-ASR-0.6B/tree/5eb144179a02acc5e5ba31e748d22b0cf3e303b0)
  与 [Qwen 1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B/tree/7278e1e70fe206f11671096ffdd38061171dd6e5)
  这两个取证版本的模型卡明确 Apache-2.0；仓库没有单独 LICENSE。
  这是基础模型许可证据，不代表转换仓库固定了对应基础模型 commit。
- 官方 [Hy-MT2-1.8B](https://huggingface.co/tencent/Hy-MT2-1.8B/tree/9a341cd1b679d3efd23b46e847b01745a71ed792)
  LICENSE 与 MLX 转换的文本相同。本次只对具体模型、固定版本作判断，不能套用
  其他 Hunyuan 模型的限制性许可或反向把全部 Hunyuan 都称为 Apache。
- Windows Qwen README 指向
  [ModelScope 转换源](https://www.modelscope.cn/models/zengshuishui/Qwen3-ASR-onnx/files)
  和 [Wasser1462 转换项目](https://github.com/Wasser1462/Qwen3-ASR-onnx)。
  [ModelScope API](https://www.modelscope.cn/api/v1/models/zengshuishui/Qwen3-ASR-onnx)
  的本次快照声明 `License=apache-2.0`、基础模型 Qwen/Qwen3-ASR-0.6B、关系
  `quantized`。API 是动态来源，不是不可变版本证据。镜像 README 没有给出
  对应 ModelScope revision，尚需补这条版本／权重关联。

## 包内保留方式

Mac 模型下载逻辑已允许 README、LICENSE、NOTICE，仍需对最终缓存／离线包实查。
Windows setup 现在将通用 Apache 文本明确命名为 `UPSTREAM-APACHE-2.0.txt`，
并复制镜像 README 与本页来源说明，不再将通用文本冒充该镜像作者的 LICENSE。
已有用户模型目录中的历史文件不自动删除或改权利声明；新的官方构建应从干净
目录准备。完整证据随 `Licenses/model-cards/` 进入包，腾讯许可证仍按原摘要下载。

用户自行下载、接入或替换其他模型时，须核查该具体版本自己的条款。模型许可
不改变用户录音／课件的权利，也不授权上传这些材料。
