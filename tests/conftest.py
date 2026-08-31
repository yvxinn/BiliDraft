import pytest
from bilidraft.config import Config


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    """The regression suite must never contact accounts, APIs or model providers."""
    import requests
    from urllib.parse import urlsplit

    original = requests.sessions.Session.request

    def guarded(self, method, url, *args, **kwargs):
        if urlsplit(url).hostname not in ("localhost", "127.0.0.1", "::1"):
            pytest.fail("External HTTP is prohibited in offline tests")
        return original(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.sessions.Session, "request", guarded)


@pytest.fixture
def cfg(tmp_path):
    c = Config(workspace=tmp_path)
    c.llm.model = "test-model"
    c.llm.retries = 0
    c.polish.split_retries = 0
    return c


@pytest.fixture
def transcript():
    return {
        "source": "test",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 3.0,
                "text": "这是一个完整的信息示例，需要保留原文中的每一项内容。",
            },
            {"id": 1, "start": 3.0, "end": 6.0, "text": "答案是42，并且不能丢失这里的否定词。"},
        ],
    }
