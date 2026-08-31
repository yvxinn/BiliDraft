# 排障

## 先确认运行位置

`--workspace` 和 `--config` 是全局选项，放在子命令前。相对配置及输出路径基于 workspace；省略时基于当前目录。Skill 自身环境缺失时执行 `python <skill-dir>/scripts/setup.py`，再运行 `scripts/run.py`。不要把凭据移入已发布的源码包。

```sh
python scripts/run.py --workspace ../my-bilidraft-work doctor --offline --json
python scripts/run.py --workspace ../my-bilidraft-work run BV1234567890 --dry-run
```

示例 BV 需替换。离线诊断不检查服务可达性。

## 登录与下载

- 二维码过期或登录失效：显式运行 `login`，打开输出的本地图片，用 B 站 App 扫码并确认。保留旧凭据直到新登录成功，不在公开 Issue 中上传二维码或 Cookie。
- 下载遇到权限限制/412：检查账号是否可访问该视频，停止盲目重试；不绕过访问控制。
- 仅第一 P：提供 BV 号或完整 `bilibili.com/video/BV...` 链接。其他 P 与短链接明确拒绝，避免处理错内容。
- `--skip-download`：必须已经有完整视频。不要用裁剪片段冒充原视频。

## 模型与质量

- 缺少模型：设置 `llm.model` / `BILIDRAFT_MODEL`，或使用 `--text-mode raw`。不会擅自选择付费模型。
- 401/403/404：核对端点、模型名和环境变量；不要粘贴完整报错中的密钥。429/暂时性错误已有有限重试。
- 本地 Ollama 云模型：localhost 不保证数据留在本机；模型需要对应账户权限。
- strict 返回 2：已生成初稿，查看 report 的原文回退和数字告警。不要自动降低阈值。
- 原文回退：可以 `--retry-failed` 只重试失败块；反复失败时保留原文并人工核对。
- 原始字幕模式：不会修复字幕本身的识别错误，也不调用语义检查模型。

## 媒体、ASR 与性能

FFmpeg 需要能运行，而不只是文件存在。可设置配置项 `ffmpeg`；Linux OpenCV 导入失败时检查系统库，不要安装冲突的多个 OpenCV 变体。

无字幕视频需要 `scripts/setup.py --asr`；默认 CPU，首次获取权重需要时间。可设置 `asr.model`、`asr.vad_model` 为已有本地模型路径，或 `asr.engine: disabled` 明确拒绝无字幕内容。torch 与 torchaudio 版本须匹配。

密集配图/OCR 耗时长时使用 representative，关闭 `representative_ocr`，或降低图片预算。可选 OCR 需 `scripts/setup.py --ocr`。这不影响正文保留。若完全不需截图，用 `--images none`。

## 恢复和更新

Ctrl+C 会取消排队任务，但已发出的 HTTP 请求可能等待超时；随后使用 `--resume`。另一个进程占用任务时不要删除锁，等待或改用其他工作目录。

升级实现后缓存可能失效；`--resume --skip-download` 保留已有完整视频并重算必要阶段。`--from-stage layout` 只重建排版及依赖结果，`--from-stage clean` 会重新润色所有块。分享问题时只提供脱敏配置、版本、退出码和最小合成输入。
