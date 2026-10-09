"""WebSearch 新增 max_results：schema 默认 10、4 个 provider 负载、run_web_search_async 透传。"""
import asyncio
import sys
import types

import pytest
from pydantic import ValidationError

import core.tools.web_search.web_search as web_search
from core.tools.web_search.web_search import WebSearch


def test_web_search_defaults_max_results_to_10():
    assert WebSearch.model_validate({"query": "q"}).max_results == 10
    schema = WebSearch.to_openai_tool()["function"]["parameters"]["properties"]["max_results"]
    assert schema["default"] == 10


def test_web_search_rejects_max_results_below_one():
    with pytest.raises(ValidationError):
        WebSearch.model_validate({"query": "q", "max_results": 0})


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _patch_httpx(monkeypatch, response):
    captured = {}

    class _FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *, url, headers=None, json=None):
            captured["payload"] = json
            return response

    monkeypatch.setattr(web_search.httpx, "AsyncClient", _FakeAsyncClient)
    return captured


def test_firecrawl_search_sends_max_results_as_limit(monkeypatch):
    captured = _patch_httpx(monkeypatch, _FakeResponse({"success": True, "data": {"web": []}}))

    ok, _ = asyncio.run(web_search.firecrawl_search("q", max_results=3))

    assert ok is True
    assert captured["payload"]["limit"] == 3


def test_tavily_search_sends_max_results(monkeypatch):
    captured = _patch_httpx(monkeypatch, _FakeResponse({"results": []}))

    ok, _ = asyncio.run(web_search.tavily_search("q", max_results=4))

    assert ok is True
    assert captured["payload"]["max_results"] == 4


def test_exa_search_sends_max_results_as_numresults(monkeypatch):
    captured = _patch_httpx(monkeypatch, _FakeResponse({"results": []}))

    ok, _ = asyncio.run(web_search.exa_search("q", max_results=5))

    assert ok is True
    assert captured["payload"]["numResults"] == 5


def test_ddgs_search_sends_max_results(monkeypatch):
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

    ok, _ = asyncio.run(web_search.ddgs_search("q", max_results=6))

    assert ok is True
    assert captured["max_results"] == 6


def test_run_web_search_async_passes_max_results_to_provider(monkeypatch):
    calls = {}

    async def fake_tavily(query, max_results=10):
        calls["tavily"] = (query, max_results)
        return True, "ok"

    monkeypatch.setattr(web_search, "tavily_search", fake_tavily)
    monkeypatch.setattr(web_search.PROVIDER_STATE, "firecrawl_enabled", False)
    monkeypatch.setattr(web_search.PROVIDER_STATE, "tavily_enabled", True)

    result = asyncio.run(web_search.run_web_search_async("q", max_results=7))

    assert result == "ok"
    assert calls["tavily"] == ("q", 7)
