"""ToolPool：schema 与条目对齐、执行契约、run/arun 缺省与上下文透传。"""
import asyncio

from core.runtime_context import ToolContext
from core.tools import ToolResult, ToolPool
from core.tools.tool_base import BaseTool


class Echo(BaseTool):
    """Echo."""
    value: str = ""

    def run(self, tctx=None):
        return f"ran:{self.value}"


class AsyncOnly(BaseTool):
    """Async only."""
    value: str = ""

    async def arun(self, tctx=None):
        return f"async:{self.value}"


class Reject(BaseTool):
    """Reject."""
    def run(self, tctx=None):
        raise ValueError("boom")


class Named(BaseTool):
    """Named."""
    def run(self, tctx=None):
        return tctx.agent_name


def test_pool_schemas_match_entries():
    pool = ToolPool({"echo": Echo, "async_only": AsyncOnly})
    assert [s["function"]["name"] for s in pool.schemas()] == ["echo", "async_only"]
    assert len(pool) == 2


def test_execute_returns_tool_result():
    pool = ToolPool({"echo": Echo})
    assert pool.execute_sync("echo", {"value": "x"}) == ToolResult(content="ran:x")
    assert asyncio.run(pool.execute("echo", {"value": "y"})) == ToolResult(content="ran:y")


def test_async_default_delegates_to_run():
    pool = ToolPool({"echo": Echo})
    assert asyncio.run(pool.execute("echo", {"value": "z"})) == ToolResult(content="ran:z")


def test_sync_default_runs_arun():
    pool = ToolPool({"async_only": AsyncOnly})
    assert pool.execute_sync("async_only", {"value": "z"}) == ToolResult(content="async:z")


def test_unknown_tool_is_result_not_exception():
    pool = ToolPool({})
    assert pool.execute_sync("nope", {}) == ToolResult.unknown("nope")
    assert asyncio.run(pool.execute("nope", {})) == ToolResult.unknown("nope")


def test_tool_exception_becomes_error_result():
    pool = ToolPool({"reject": Reject})
    result = pool.execute_sync("reject", {})
    assert result.is_error
    assert result.content.startswith("[Tool Error]:")


def test_extra_args_are_rejected():
    pool = ToolPool({"echo": Echo})
    assert pool.execute_sync("echo", {"value": "x", "bogus": 1}).is_error


def test_context_reaches_tool():
    pool = ToolPool({"named": Named})
    assert pool.execute_sync("named", {}, ToolContext(agent_name="tm")).content == "tm"
