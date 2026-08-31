from types import SimpleNamespace

import pytest
import requests

from bilidraft import auth


def test_login_network_errors_do_not_expose_polling_keys():
    def fail(*a, **kw):
        raise requests.ConnectionError("https://example.invalid/poll?qrcode_key=PRIVATE_TEST_TOKEN")

    with pytest.raises(RuntimeError) as error:
        auth._get(SimpleNamespace(get=fail), "https://example.invalid/poll")
    assert "PRIVATE_TEST_TOKEN" not in str(error.value)
    assert "请求失败" in str(error.value)


def test_login_failure_removes_qr_without_saving_credentials(cfg, monkeypatch):
    import qrcode

    def request(session, url, params=None):
        if url.endswith("spi"):
            return {"data": {}}
        if url.endswith("generate"):
            return {"code": 0, "data": {"url": "https://example.invalid/qr", "qrcode_key": "synthetic"}}
        raise RuntimeError("simulated offline failure")

    monkeypatch.setattr(auth, "_get", request)
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    monkeypatch.setattr(qrcode, "make", lambda _: SimpleNamespace(save=lambda p: p.write_bytes(b"test")))
    with pytest.raises(RuntimeError, match="simulated"):
        auth.login(cfg, notify=lambda _: None)
    credential = cfg.path(cfg.bilibili.credential_file)
    assert not credential.exists()
    assert not (credential.parent / "login_qr.png").exists()
    assert not cfg.path(cfg.bilibili.cookies_file).exists()


def test_qr_login_success_preserves_current_credential_flow(cfg, monkeypatch):
    import qrcode
    from bilidraft.storage import read_json

    statuses = iter([86101, 86090, 0])
    messages = []

    def request(session, url, params=None):
        if url.endswith("spi"):
            return {"data": {"b_3": "synthetic-device"}}
        if url.endswith("generate"):
            return {"code": 0, "data": {"url": "https://example.invalid/qr", "qrcode_key": "synthetic-key"}}
        assert params == {"qrcode_key": "synthetic-key"}
        status = next(statuses)
        if status == 0:
            session.cookies.set("SESSDATA", "synthetic-session", domain=".bilibili.com", path="/")
            session.cookies.set("bili_jct", "synthetic-csrf", domain=".bilibili.com", path="/")
        return {"code": 0, "data": {"code": status}}

    monkeypatch.setattr(auth, "_get", request)
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        qrcode, "make", lambda _: SimpleNamespace(save=lambda p: p.write_bytes(b"synthetic QR"))
    )
    auth.login(cfg, notify=messages.append)
    credential = cfg.path(cfg.bilibili.credential_file)
    assert read_json(credential)["sessdata"] == "synthetic-session"
    assert "SESSDATA" in cfg.path(cfg.bilibili.cookies_file).read_text()
    assert not (credential.parent / "login_qr.png").exists()
    assert "synthetic-session" not in "\n".join(messages)


@pytest.mark.parametrize("status,timeout", [(86038, 180), (86101, 0)])
def test_expired_or_timed_out_login_preserves_existing_credentials(cfg, monkeypatch, status, timeout):
    import qrcode

    credential = cfg.path(cfg.bilibili.credential_file)
    credential.parent.mkdir(parents=True)
    credential.write_text('{"sessdata":"existing-synthetic-session"}')
    cookie_file = cfg.path(cfg.bilibili.cookies_file)
    cookie_file.write_text("existing-synthetic-cookies")
    before = (credential.read_bytes(), cookie_file.read_bytes())

    def request(session, url, params=None):
        if url.endswith("spi"):
            return {"data": {}}
        if url.endswith("generate"):
            return {"code": 0, "data": {"url": "https://example.invalid/qr", "qrcode_key": "synthetic"}}
        return {"code": 0, "data": {"code": status}}

    monkeypatch.setattr(auth, "_get", request)
    monkeypatch.setattr(auth.time, "sleep", lambda _: None)
    monkeypatch.setattr(qrcode, "make", lambda _: SimpleNamespace(save=lambda p: p.write_bytes(b"test")))
    with pytest.raises(RuntimeError, match="过期|超时"):
        auth.login(cfg, timeout=timeout, notify=lambda _: None)
    assert before == (credential.read_bytes(), cookie_file.read_bytes())
    assert not (credential.parent / "login_qr.png").exists()
