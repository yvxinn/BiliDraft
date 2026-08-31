"""Explicit QR login. Secrets are stored only in ignored local files."""

import os
import time
import requests
from .config import UA
from .storage import atomic_text, write_json


def _get(session, url, params=None):
    try:
        response = session.get(url, params=params, timeout=15, allow_redirects=False)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"B站登录服务 HTTP {response.status_code}，请稍后重试")
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("expected object")
        return result
    except (requests.RequestException, ValueError) as e:
        # Request exception strings can contain the private QR polling key.
        raise RuntimeError("B站登录请求失败，请检查网络或稍后重试") from e


def login(cfg, timeout=180, notify=print):
    import qrcode

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    finger = _get(session, "https://api.bilibili.com/x/frontend/finger/spi")
    buvid = finger.get("data", {}).get("b_3", "")
    if buvid:
        session.cookies.set("buvid3", buvid, domain=".bilibili.com")
    result = _get(session, "https://passport.bilibili.com/x/passport-login/web/qrcode/generate")
    if result.get("code") != 0:
        raise RuntimeError("二维码生成失败，请稍后重试")
    info = result["data"]
    credential = cfg.path(cfg.bilibili.credential_file)
    credential.parent.mkdir(parents=True, exist_ok=True)
    qr = credential.parent / "login_qr.png"
    qrcode.make(info["url"]).save(qr)
    notify(f"二维码：{qr}\n请用 B站 App 扫码并确认，二维码将在约 3 分钟后过期。")
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            time.sleep(2)
            j = _get(
                session,
                "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                params={"qrcode_key": info["qrcode_key"]},
            )
            if j.get("code") != 0:
                raise RuntimeError("二维码登录接口返回异常，请重试")
            status = j["data"].get("code")
            if status == 0:
                cookies = requests.utils.dict_from_cookiejar(session.cookies)
                if not cookies.get("SESSDATA"):
                    raise RuntimeError("登录未获得有效凭据")
                write_json(
                    credential,
                    {
                        "sessdata": cookies["SESSDATA"],
                        "bili_jct": cookies.get("bili_jct", ""),
                        "dedeuserid": cookies.get("DedeUserID", ""),
                        "buvid3": cookies.get("buvid3", ""),
                        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
                lines = ["# Netscape HTTP Cookie File"]
                for c in session.cookies:
                    lines.append(
                        "\t".join(
                            [
                                c.domain,
                                "TRUE" if c.domain.startswith(".") else "FALSE",
                                c.path or "/",
                                "TRUE" if c.secure else "FALSE",
                                str(c.expires or 0),
                                c.name,
                                c.value,
                            ]
                        )
                    )
                cookie_file = cfg.path(cfg.bilibili.cookies_file)
                atomic_text(cookie_file, "\n".join(lines) + "\n")
                if os.name != "nt":
                    os.chmod(credential, 0o600)
                    os.chmod(cookie_file, 0o600)
                notify("登录成功。凭据已保存在本地；请勿分享 config 目录。")
                return
            if status == 86038:
                raise RuntimeError("二维码已过期，请重新运行 login")
        raise RuntimeError("扫码登录超时，请重新运行 login")
    finally:
        qr.unlink(missing_ok=True)
