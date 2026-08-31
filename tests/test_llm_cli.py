import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
import pytest
from typer.testing import CliRunner
from bilidraft import llm, polish, structure, pipeline
from bilidraft.cli import app


@pytest.mark.parametrize("value", ["[]", "null", "not json", "```", '{"incomplete":'])
def test_bad_json_never_becomes_valid(value):
    with pytest.raises(ValueError):
        llm.parse_json(value)


def test_fenced_json():
    assert llm.parse_json('```json\n{"ok":true}\n```') == {"ok": True}


def test_redirect_is_not_followed(cfg, monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=307)

    monkeypatch.setattr(llm.requests, "post", post)
    with pytest.raises(llm.LlmError, match="重定向"):
        llm.chat(cfg.llm, "system", "private input")
    assert len(calls) == 1 and calls[0]["allow_redirects"] is False


def test_auth_failure_is_not_retried(cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(
        llm.requests, "post", lambda *a, **k: calls.append(1) or SimpleNamespace(status_code=401)
    )
    cfg.llm.retries = 3
    with pytest.raises(llm.LlmError, match="401"):
        llm.chat(cfg.llm, "system", "user")
    assert len(calls) == 1


def test_json_mode_fallback_is_supported(cfg, monkeypatch):
    bodies = []

    def post(*args, **kwargs):
        bodies.append(dict(kwargs["json"]))
        if len(bodies) == 1:
            return SimpleNamespace(status_code=400)
        return SimpleNamespace(
            status_code=200, json=lambda: {"choices": [{"message": {"content": '{"ok":true}'}}]}
        )

    monkeypatch.setattr(llm.requests, "post", post)
    assert llm.parse_json(llm.chat(cfg.llm, "system", "user"))["ok"]
    assert "response_format" in bodies[0] and "response_format" not in bodies[1]


def test_reasoning_control_is_optional_and_explicit(cfg, monkeypatch):
    bodies = []

    def post(*args, **kwargs):
        bodies.append(dict(kwargs["json"]))
        return SimpleNamespace(
            status_code=200, json=lambda: {"choices": [{"message": {"content": '{"ok":true}'}}]}
        )

    monkeypatch.setattr(llm.requests, "post", post)
    llm.chat(cfg.llm, "system", "user")
    cfg.llm.reasoning_effort = "none"
    llm.chat(cfg.llm, "system", "user")
    assert "reasoning_effort" not in bodies[0]
    assert bodies[1]["reasoning_effort"] == "none"


def test_actual_http_client_and_model_protocol_are_local(cfg, transcript, tmp_path, monkeypatch):
    # Exercises real HTTP framing, JSON schemas, self-check and heading generation.
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            user = json.loads(body["messages"][-1]["content"])
            seen.append(user)
            if isinstance(user, list):
                response = {"headings": [{"after_id": user[0]["id"], "level": 2, "title": "合成测试标题"}]}
            elif "raw" in user:
                response = {"dropped": []}
            else:
                response = {"lines": [{"ids": [s["id"]], "text": s["text"]} for s in user["lines"]]}
            payload = json.dumps(
                {"choices": [{"message": {"content": json.dumps(response, ensure_ascii=False)}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg.llm.base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    try:
        clean = polish.run_polish(transcript, {}, cfg, tmp_path, progress=lambda x: None)
        headings = structure.run_structure(clean, {}, cfg)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert clean["report"]["suspect_ratio"] == 0
    assert headings["headings"][0]["title"] == "合成测试标题"
    assert len(seen) == 3


def test_cli_strict_returns_two_when_quality_fails(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        pipeline, "run", lambda *a, **k: {"quality": {"passed": False, "warnings": ["未通过"]}}
    )
    result = CliRunner().invoke(app, ["run", "BV1234567890", "--strict"])
    assert result.exit_code == 2 and "未通过" in result.output


def test_cli_init_does_not_overwrite(tmp_path):
    target = tmp_path / "config.yaml"
    runner = CliRunner()
    first = runner.invoke(app, ["init-config", str(target)])
    content = target.read_text(encoding="utf-8")
    second = runner.invoke(app, ["init-config", str(target)])
    assert first.exit_code == 0 and second.exit_code != 0
    assert target.read_text(encoding="utf-8") == content


def test_main_reports_invalid_bvid_without_traceback(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "bilidraft", "run", "../invalid"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert proc.returncode == 1 and "Traceback" not in proc.stderr
