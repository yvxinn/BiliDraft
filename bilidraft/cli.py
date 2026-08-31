"""Command line interface for BiliDraft."""

import json
from pathlib import Path
from typing import Optional
import typer
from .config import load_config, validate_config
from .storage import read_json, parse_video_input

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    invoke_without_command=True,
    help="B站视频 → 保留完整信息的 Markdown 图文讲义",
)


@app.callback()
def options(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="配置 YAML 路径"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace", "-w", help="配置与产物的工作目录；默认当前目录"
    ),
    version: bool = typer.Option(False, "--version", is_eager=True, help="显示版本"),
):
    if version:
        from . import __version__

        typer.echo(__version__)
        raise typer.Exit()
    ctx.obj = {"config": config, "workspace": workspace}


def _cfg(ctx):
    return load_config(ctx.obj["config"], workspace=ctx.obj["workspace"])


@app.command()
def run(
    ctx: typer.Context,
    bvid: str,
    resume: bool = typer.Option(False, "--resume", help="复用有效阶段与已完成润色块"),
    skip_download: bool = typer.Option(False, "--skip-download", help="使用已有完整视频"),
    retry_failed: bool = typer.Option(False, "--retry-failed", help="复用通过校验的润色块，仅重试失败块"),
    from_stage: Optional[str] = typer.Option(None, "--from-stage", help="强制重跑指定阶段及其下游"),
    strict: bool = typer.Option(False, "--strict", help="质量阈值未通过时退出码为 2；讲义仍保留"),
    text_mode: Optional[str] = typer.Option(
        None, "--text-mode", help="polish 润色，raw 原始字幕且不调用模型"
    ),
    images: Optional[str] = typer.Option(None, "--images", help="representative / detailed / none"),
    image_budget: Optional[int] = typer.Option(None, "--image-budget", help="代表配图总上限"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅输出执行计划，不写文件、不联网"),
    json_output: bool = typer.Option(False, "--json", help="stdout 输出 JSON 结果，进度写到 stderr"),
):
    """处理 BV 号或完整视频链接，仅支持第一 P。"""
    from .pipeline import run as pipeline_run, task_paths

    bvid = parse_video_input(bvid)
    cfg = _cfg(ctx)
    if text_mode is not None:
        cfg.text_mode = text_mode
    if images is not None:
        cfg.slides.mode = images
    if image_budget is not None:
        cfg.slides.max_frames = image_budget
    validate_config(cfg)
    note, task = task_paths(bvid, cfg)
    if dry_run:
        typer.echo(
            json.dumps(
                {
                    "bvid": bvid,
                    "workspace": str(cfg.workspace),
                    "output": str(note),
                    "text_mode": cfg.text_mode,
                    "images": cfg.slides.mode,
                    "image_budget": cfg.slides.max_frames,
                    "model": cfg.llm.model,
                    "model_endpoint": cfg.llm.base_url if cfg.text_mode == "polish" else None,
                    "sends_transcript_to_model": cfg.text_mode == "polish",
                    "cached_source": {k: (task / f"{k}.json").is_file() for k in ("meta", "subtitle")},
                    "note": "Ollama cloud 模型即使使用 localhost 也可能转发到云端；API key 不在计划中输出。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    progress = (lambda message: typer.echo(message, err=True)) if json_output else typer.echo
    report = pipeline_run(bvid, resume, skip_download, cfg, retry_failed, from_stage, progress=progress)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "bvid": bvid,
                    "note": str(note / "note.md"),
                    "report": str(note / "report.json"),
                    "quality": report["quality"],
                    "images": report.get("images", {}),
                    "text_mode": cfg.text_mode,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    if not report["quality"]["passed"]:
        for warning in report["quality"]["warnings"]:
            typer.echo("注意：" + warning, err=json_output)
        if strict:
            raise typer.Exit(2)


@app.command()
def stages(ctx: typer.Context, bvid: str):
    """查看阶段状态，遵循相同的配置输出目录。"""
    from .pipeline import task_paths, _stage_records

    _, task = task_paths(parse_video_input(bvid), _cfg(ctx))
    typer.echo(json.dumps(_stage_records(task), ensure_ascii=False, indent=2))


@app.command()
def report(ctx: typer.Context, bvid: str):
    """查看质量报告。"""
    from .pipeline import task_paths

    note, _ = task_paths(parse_video_input(bvid), _cfg(ctx))
    path = note / "report.json"
    if not path.is_file():
        raise ValueError("尚无质量报告，请先运行任务")
    typer.echo(json.dumps(read_json(path), ensure_ascii=False, indent=2))


@app.command()
def doctor(
    ctx: typer.Context,
    offline: bool = typer.Option(False, "--offline", help="仅检查本地环境，不调用 API"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
):
    """检查依赖、ffmpeg、登录和模型连通性。在线检查会调用一次模型。"""
    from .doctor import diagnose

    result = diagnose(_cfg(ctx), offline)
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result["checks"]:
            icon = "OK" if check["ok"] else "FAIL" if check["required"] else "WARN"
            typer.echo(f"[{icon}] {check['name']}: {check['message']}")
    if not result["ok"]:
        raise typer.Exit(1)


@app.command()
def login(ctx: typer.Context):
    """显式扫码登录，凭据仅保存到本地配置目录。"""
    from .auth import login as qr_login

    qr_login(_cfg(ctx))


@app.command("init-config")
def init_config(ctx: typer.Context, path: Path = typer.Argument(Path("config/config.yaml"))):
    """写入可编辑示例配置；不覆盖已有文件。"""
    path = path if path.is_absolute() else Path(ctx.obj["workspace"] or Path.cwd()) / path
    if path.exists():
        raise ValueError("目标配置已存在，不会覆盖")
    from .defaults import EXAMPLE
    from .storage import atomic_text

    atomic_text(path, EXAMPLE)
    typer.echo(str(path.resolve()))


def main():
    try:
        app()
    except (ValueError, RuntimeError, OSError) as e:
        typer.echo(f"错误：{e}", err=True)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        typer.echo("已中断；完成的阶段和润色块已保存，可使用 --resume 续跑。", err=True)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
