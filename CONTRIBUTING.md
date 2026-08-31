# 贡献指南

欢迎通过 Issue 或 PR 提交可复现的问题和范围明确的改进。不要上传账号凭据、个人日志或未经授权的课程内容。

## 开发环境

需要 Python 3.10+ 和 FFmpeg：

```sh
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -e ".[dev]"
python -m ruff check bilidraft tests scripts
python -m ruff format --check bilidraft tests scripts
python -m pytest
python -m pip check
python scripts/run.py --workspace tmp/dev doctor --offline
```

基础测试不需要账号、云模型、OCR/ASR 权重。媒体集成测试用 FFmpeg 生成合成视频；缺少 FFmpeg 时会跳过，完整验收应安装它并确认无意外跳过。可选集成另行安装 `.[ocr]` 或 `.[asr]`。

## 变更约定

- 修复可复现的问题，为数据完整性、失败恢复或隐私边界增加有意义的回归测试。
- 不删减原文来提高质量分数，不隐藏 raw fallback 或数字告警。
- 配置有类型与范围校验。修改默认值时同步 `config/config.example.yaml` 和 `bilidraft/defaults.py`；测试检查二者一致。
- 参数影响语义结果时纳入缓存签名，恢复执行应能正确重建下游。
- API 报错不要泄露原始响应、签名 URL、Cookie、Authorization 或字幕。
- 保持 `scripts/run.py` 可从任意目录执行，任务数据与 Skill 安装目录分离。
- 依赖升级应验证 Python 3.10/3.12/3.13 与 Windows/Linux。重型 ASR/OCR 保持可选。

## 发布检查

```sh
python scripts/audit_release.py
python -m pip install build
python -m build
python scripts/audit_release.py --artifacts dist
```

审计检查当前 Git 文件清单、危险路径、常见密钥格式、绝对用户路径及包内文件；它不能代替人工审查，也不替代历史密钥扫描。公开提交之前检查 staged diff、提交元数据和历史，不要推送个人实验历史。wheel 与源码包须在源码目录外验证导入和 CLI。

PR 描述应说明实际问题、行为变化、验证范围及尚未验证的部分。测试只使用合成 BV、模拟凭据和自制素材。
