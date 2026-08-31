"""Thin Bilibili API client with bounded retries and no credential logging."""

import hashlib
import time
import urllib.parse
import requests
from .config import MIXIN_TAB, UA, load_config, load_credential


class BilibiliClient:
    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        cred = load_credential(self.cfg)
        for key, name in (("sessdata", "SESSDATA"), ("buvid3", "buvid3"), ("bili_jct", "bili_jct")):
            if cred.get(key):
                self.sess.cookies.set(name, cred[key], domain=".bilibili.com", secure=True)
        self._wbi = None

    def get(self, url, params=None, retries=3, allow_logged_out=False):
        for attempt in range(retries + 1):
            try:
                r = self.sess.get(url, params=params, timeout=(10, 30))
                if r.status_code in (412, 429, 500, 502, 503, 504):
                    if attempt < retries:
                        time.sleep(2**attempt)
                        continue
                    raise RuntimeError(f"B站服务暂不可用（HTTP {r.status_code}），请稍后重试")
                r.raise_for_status()
                data = r.json()
                code = data.get("code")
                if code == 0 or (code == -101 and allow_logged_out):
                    return data
                if code in (-412, -352):
                    raise RuntimeError("B站请求被风控拒绝，请稍后重试；不会尝试绕过验证")
                if code in (-101, -111):
                    raise RuntimeError("B站登录凭据失效，请运行 bilidraft login")
                raise RuntimeError(f"B站接口错误（code={code}），请检查视频权限或稍后重试")
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt >= retries:
                    raise RuntimeError("B站连接失败，请检查网络后重试") from e
                time.sleep(2**attempt)
            except requests.HTTPError as e:
                raise RuntimeError(f"B站 HTTP {e.response.status_code}") from e
            except (ValueError, AttributeError) as e:
                raise RuntimeError("B站接口返回格式异常") from e
        raise RuntimeError("B站请求失败")

    def _wbi_keys(self):
        if not self._wbi:
            data = self.get("https://api.bilibili.com/x/web-interface/nav", allow_logged_out=True)["data"]
            wbi = data["wbi_img"]
            self._wbi = tuple(wbi[k].rsplit("/", 1)[-1].split(".")[0] for k in ("img_url", "sub_url"))
        return self._wbi

    def _wbi_sign(self, params):
        img, sub = self._wbi_keys()
        mixin = "".join((img + sub)[i] for i in MIXIN_TAB)[:32]
        p = dict(params, wts=int(time.time()))
        p = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in sorted(p.items())}
        p["w_rid"] = hashlib.md5((urllib.parse.urlencode(p) + mixin).encode()).hexdigest()
        return p

    def view(self, bvid):
        return self.get("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})["data"]

    def fetch_meta(self, bvid, task_dir=None):
        d = self.view(bvid)
        page = (d.get("pages") or [{}])[0]
        return {
            "bvid": bvid,
            "cid": page.get("cid", d["cid"]),
            "title": d["title"],
            "owner": d["owner"]["name"],
            "duration": page.get("duration", d["duration"]),
            "tname": d.get("tname", ""),
            "pubdate": d.get("pubdate"),
            "desc": d.get("desc", ""),
            "page": 1,
            "page_count": len(d.get("pages", [])) or 1,
        }

    def fetch_subtitle(self, bvid, cid):
        params = self._wbi_sign({"bvid": bvid, "cid": cid})
        data = self.get("https://api.bilibili.com/x/player/wbi/v2", params)["data"]
        subs = data.get("subtitle", {}).get("subtitles", [])
        if not subs:
            return None
        preferred = [s for s in subs if s.get("lan", "").startswith(("ai-zh", "zh"))]
        sub = (preferred or subs)[0]
        url = sub["subtitle_url"]
        if url.startswith("//"):
            url = "https:" + url
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not any(
            host == d or host.endswith("." + d) for d in ("hdslb.com", "bilibili.com")
        ):
            raise RuntimeError("字幕地址不属于预期的 B站 HTTPS 域名")
        try:
            r = self.sess.get(url, timeout=(10, 30))
            r.raise_for_status()
            body = r.json()["body"]
        except (requests.RequestException, ValueError, KeyError) as e:
            raise RuntimeError("字幕文件获取失败，请稍后重试") from e
        return {
            "source": "bilibili_ai"
            if sub.get("lan", "").startswith("ai-") or "aisubtitle" in host
            else "upowner",
            "lan": sub.get("lan"),
            "segments": [
                {"id": i, "start": b["from"], "end": b["to"], "text": b["content"]}
                for i, b in enumerate(body)
            ],
        }
