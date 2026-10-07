"""ddgs_search 回归：曾硬编码 backend="google,bing,brave,duckduckgo"——bing 在 ddgs>=9
已停用，wikipedia/grokipedia 等可用引擎被排除，回退搜索因此经常返回 No results。"""
import asyncio
import sys
import types

from ddgs.engines import ENGINES


def test_ddgs_search_requests_only_available_backends(monkeypatch):
    import core.tools.web_search.web_search as web_search

    captured = {}

    class _FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, **kwargs):
            captured.update(kwargs)
            return [{"title": "t", "href": "http://example.com", "body": "b"}]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=_FakeDDGS))

    ok, result = asyncio.run(web_search.ddgs_search("q"))

    assert ok is True
    assert "http://example.com" in result
    backend = captured.get("backend", "auto")  # 不传即 ddgs 默认 auto
    keys = {k.strip() for k in backend.split(",")}
    valid = set(ENGINES["text"]) | {"auto", "all"}
    assert keys <= valid, f"unknown ddgs backend(s): {keys - valid}"
