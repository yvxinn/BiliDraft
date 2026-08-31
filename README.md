# BiliDraft

[![CI](https://github.com/yvxinn/BiliDraft/actions/workflows/ci.yml/badge.svg)](https://github.com/yvxinn/BiliDraft/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

把 B 站视频变成完整、带参考配图的 Markdown 初稿。可作为 Agent Skill 使用，也可独立运行 CLI。

保留讲解内容，轻度整理口语；默认只选少量代表画面，不逐页归档 PPT。适合课程讲义、长视频整理和图文博客初稿。**0.3.0 为早期公开版本**，数字、术语和存疑段仍需人工核对。

- **完整正文**：B 站字幕优先，无字幕时可选本地 CPU ASR；润色不通过校验就保留原文并提示。
- **灵活配图**：代表配图 / 密集配图 / 不配图；CPU 并行，图片保留完整原始尺寸。
- **可恢复**：阶段缓存、逐块保存、失败块重试、任务文件锁和原子写入。
- **可控数据流**：明确选择模型；原始字幕模式不调用 LLM；二维码登录，凭据仅保存在本地。
- **便于 Agent 调用**：独立工作目录、只读执行计划、JSON 结果和明确退出码。

目前仅支持单个视频的**第一 P**；不支持短链接、播放列表、直播、云 ASR、VLM 选图及 DOCX/PDF 导出。

## 安装为 Skill

需要 Python 3.10+、Git 和 FFmpeg。将本仓库克隆到 Agent 的技能目录下，目录名为 `bilidraft`；根目录的 [SKILL.md](SKILL.md) 是技能入口，运行代码和参考文档都包含在仓库内。

例如 Codex 的用户技能目录：

```sh
git clone https://github.com/yvxinn/BiliDraft.git ~/.codex/skills/bilidraft
python ~/.codex/skills/bilidraft/scripts/setup.py
```

Windows PowerShell 可使用 `$HOME/.codex/skills/bilidraft`。使用自定义 `CODEX_HOME` 时应改为对应的 `skills/bilidraft` 目录。重新加载技能后，例如：

> 用 $bilidraft 把这个 B 站链接整理成完整图文讲义，工作目录放在我的资料目录。先展示执行计划，不使用云模型。

Skill 会先检查依赖和配置，再按需使用二维码登录。**课程工作目录应放在技能安装目录之外**，更新技能不影响凭据和缓存。其他支持 SKILL.md 的 Agent 也可读取本技能；按其技能目录规则安装即可。

安装可选组件：

```sh
python scripts/setup.py --check
python scripts/setup.py --asr    # 无字幕时使用本地 SenseVoice；首次需下载模型
python scripts/setup.py --ocr    # 密集配图，或代表配图的可选 OCR
```

以上命令在仓库内执行；从其他目录使用时请给出脚本绝对路径。`setup.py` 创建本仓库专属的 `.venv`，不安装 FFmpeg，不登录账号，不创建课程数据。

## 直接使用 CLI

无需 Agent 也能运行。克隆仓库、执行 `python scripts/setup.py` 后：

```sh
python scripts/run.py --workspace ../my-bilidraft-work init-config
python scripts/run.py --workspace ../my-bilidraft-work doctor --offline
python scripts/run.py --workspace ../my-bilidraft-work login
python scripts/run.py --workspace ../my-bilidraft-work run BV1234567890 --text-mode raw --dry-run
python scripts/run.py --workspace ../my-bilidraft-work run BV1234567890 --text-mode raw --resume --strict --json
```

**示例 BV 为虚构占位符，请换成实际 BV 号或完整视频 URL。** 全局选项 `--workspace`、`--config` 放在子命令前；不指定工作目录则使用当前目录。实际配置默认读取 `<workspace>/config/config.yaml`，相对路径都以工作目录为基准。`init-config` 不覆盖已有文件。

`login` 会生成本地二维码，输出图片路径。打开图片，用 B 站 App 扫码并确认；成功后保存 `config/credential.json` 和 `config/cookies.txt`，随后清理临时二维码。已有有效登录无需重复扫码。不要分享二维码、Cookie 或配置目录。

如已在自己的虚拟环境中 `python -m pip install .`，可以直接使用 `bilidraft` 或 `python -m bilidraft`，替代 `python scripts/run.py`。

FFmpeg 放在 PATH，或在 YAML 中设置 `ffmpeg` 的完整路径；Windows 也支持发现标准 WinGet Gyan.FFmpeg 安装。Linux 最小环境可能还需 `libgl1`、`libglib2.0-0`。避免同时安装多个 OpenCV 变体，它们共享 `cv2`。

## 选择处理方式

| 目标 | 运行选项 | 数据与计算 |
| --- | --- | --- |
| 完整润色图文 | `--text-mode polish --images representative` | 需明确配置 LLM；默认方式 |
| 保留原始字幕 | `--text-mode raw` | 不调用 LLM，按时间生成标题 |
| 纯文字初稿 | `--images none` | 不解码截图、不运行 OCR |
| 更密集的参考图 | `--images detailed` | 需 OCR 扩展，耗时较多，仍不保证完整逐页覆盖 |
| 控制图片数量 | `--image-budget 24` | 代表配图总上限，不减少正文 |

默认每 10 分钟约 6 张图，总数最多 60 张；重复或空白画面可能更少。代表模式自动最多使用 4 个工作线程，默认不运行 OCR。截图和全文润色并行，适合博客参考图；不会理解或覆盖视频中的每个视觉知识点。

润色模式没有默认模型。请在工作目录的 YAML 中配置：

```yaml
llm:
  base_url: https://your-provider.example/v1
  model: your-model-id
  api_key_env: BILIDRAFT_API_KEY
```

密钥通过环境变量 `BILIDRAFT_API_KEY` 提供；也可用 `BILIDRAFT_BASE_URL`、`BILIDRAFT_MODEL` 覆盖配置。支持 OpenAI 兼容接口及本地服务，HTTP 仅允许回环地址。配置中的本地 Ollama 地址不代表离线：云模型可能继续转发字幕。

**润色和标题生成会把字幕、视频标题及少量背景发送到所选端点。** 请先确认内容授权与供应商政策。`--dry-run` 只读展示模式、目标路径和模型端点，不联网、不写文件、不输出密钥。`doctor --offline` 仅检查本地；在线 doctor 会访问 B 站，并在润色模式下做一次短模型请求。

## 缓存、报告和退出码

```sh
bilidraft --workspace ../my-bilidraft-work run BV1234567890 --resume
bilidraft --workspace ../my-bilidraft-work run BV1234567890 --retry-failed
bilidraft --workspace ../my-bilidraft-work run BV1234567890 --resume --skip-download
bilidraft --workspace ../my-bilidraft-work run BV1234567890 --from-stage layout
bilidraft --workspace ../my-bilidraft-work report BV1234567890
```

`--resume` 复用输入、代码、参数和产物均有效的阶段；`--retry-failed` 只重试失败润色块并重建相应下游。`--skip-download` 需要已经缓存的完整视频；升级后可避免再次下载。`--from-stage` 强制重建指定阶段及依赖它的阶段。`stages` 命令查看阶段记录。

```text
<workspace>/notes/<BV>/
├── note.md       # 图文正文和参考图附录
├── report.json   # 模式、质量告警、配图统计
├── frames/       # 全尺寸图片
└── .task/        # 私有字幕、视频、分块结果和恢复状态
```

分享 Markdown 时保留同目录 `frames/`；不要顺带分享 `.task/`。有权访问视频不等于有权公开分发完整转录稿，请尊重原作者。

使用 `--json` 时，结果写到 stdout，进度和告警写到 stderr。退出码：0 为执行成功；加 `--strict` 后质量告警返回 2（初稿仍保留）；运行错误 1；中断 130。不加 strict 时也要阅读报告。长度、ID、数字检查与模型自检只能帮助发现问题，不能证明语义无损。

## 开发和更多说明

[架构](docs/ARCHITECTURE.md) · [性能](docs/PERFORMANCE.md) · [验证](docs/ACCEPTANCE.md) · [排障](docs/TROUBLESHOOTING.md) · [贡献](CONTRIBUTING.md) · [安全](SECURITY.md) · [变更](CHANGELOG.md)

代码以 [MIT](LICENSE) 发布，第三方依赖与参考见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。仓库不包含账号、模型权重或真实课程素材。
