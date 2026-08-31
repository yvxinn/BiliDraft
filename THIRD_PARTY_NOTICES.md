# 第三方依赖与素材

本仓库不打包模型权重、FFmpeg 二进制、课程视频、字幕、截图或第三方源码。以下组件由包管理器或使用者单独取得：

| 组件 | 用途 | 官方来源 |
| --- | --- | --- |
| yt-dlp | 视频获取与合并 | https://github.com/yt-dlp/yt-dlp |
| FFmpeg | 音频提取、容器处理 | https://ffmpeg.org/legal.html |
| OpenCV / NumPy / Pillow / ImageHash | 画面分析与图像处理 | 各 Python 包的上游仓库及 LICENSE |
| RapidOCR / ONNX Runtime | OCR | https://github.com/RapidAI/RapidOCR |
| FunASR / SenseVoice | 本地 ASR 和 VAD 时间戳 | https://github.com/modelscope/FunASR / https://github.com/FunAudioLLM/SenseVoice |
| PyTorch / torchaudio | 可选 ASR 运行时 | https://pytorch.org/ |
| Requests / PyYAML / Typer / filelock / qrcode | 网络、配置和 CLI | 安装包中对应的 LICENSE 与元数据 |
| pytest / Ruff | 开发与验收 | https://docs.pytest.org/ / https://docs.astral.sh/ruff/ |

许可证应按**实际安装版本**核验；模型权重的条款可能与代码不同。FFmpeg 的许可证取决于构建启用的组件。不要把第三方二进制或模型直接随项目发布，除非已经履行其授权与声明要求。

画面去重流程参考了 [asdhabdua/bilibili-video-notes-skill](https://github.com/asdhabdua/bilibili-video-notes-skill) 的抽帧、OCR 与感知哈希组合（上游 README 声明 MIT）。B 站接口资料参考 [bilibili-API-collect](https://github.com/pskdje/bilibili-API-collect)。感谢这些项目的公开工作；它们不隶属于 BiliDraft，也不为本项目提供担保。

示例 BV 号和测试素材为合成占位内容。实际视频及其派生字幕、讲义的权利归各权利人，本项目许可证不覆盖这些素材。
