# 维护脚本

仅保留正式运行、安装、发布审计与性能基准，业务实现统一在 `bilidraft/`。

| 脚本 | 用途 |
| --- | --- |
| setup.py | 创建 Skill 自身 .venv，安装基础依赖；--asr/--ocr 可选，--check 只读 |
| run.py | 从任意目录定位 Skill 运行环境，透传 CLI 参数，不改变工作目录 |
| audit_release.py | 检查 Git 文件清单与构建包，不联网，不展示疑似密钥值 |
| benchmark_slides.py | 只读缓存视频，在新目录测量配图，不下载、不调用 LLM |

```sh
python scripts/setup.py --check
python scripts/run.py --workspace ../my-bilidraft-work run BV1234567890 --dry-run
python scripts/audit_release.py --artifacts dist
python scripts/benchmark_slides.py BV1234567890 --workspace ../my-bilidraft-work --workers 4
```

BV 为虚构占位符。基准可用 `--workers 1` 对照、`--ocr` 或 `--mode detailed`；`--text-dir` 只复用已有讲义的文本缓存进行配图预览，不做新的模型校验。输出位于工作目录下独立的 `notes/_benchmarks/` 子目录，包含私有路径与内容标识，不应直接提交。
