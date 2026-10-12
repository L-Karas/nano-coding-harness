"""ToolPool：schema 与条目对齐、执行契约、run/arun 缺省与上下文透传。"""
import asyncio

from core.runtime_context import ToolContext
from core.tools import ToolResult, ToolPool
from core.tools.tool_base import BaseTool
from core.tools.tool_pool import _McpEntry


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


class ParallelSafe(BaseTool):
    """Parallel safe."""
    parallel_safe: bool = True

    def run(self, tctx=None):
        return "ran"


def test_pool_reports_declared_parallel_safe():
    pool = ToolPool({"safe": ParallelSafe, "serial": Echo})
    assert pool.is_parallel_safe("safe") is True
    assert pool.is_parallel_safe("serial") is False
    assert pool.is_parallel_safe("unknown") is False


def test_mcp_entry_is_not_parallel_safe_by_default():
    entry = _McpEntry(
        {"type": "function", "function": {"name": "mcp__srv__do"}},
        lambda **kwargs: "",
        lambda **kwargs: "",
    )
    pool = ToolPool({"mcp__srv__do": entry})
    assert pool.is_parallel_safe("mcp__srv__do") is False


def test_internal_fields_stay_out_of_openai_schema():
    props = ParallelSafe.to_openai_tool()["function"]["parameters"]["properties"]
    assert "agent_type" not in props
    assert "experimental" not in props
    assert "parallel_safe" not in props
