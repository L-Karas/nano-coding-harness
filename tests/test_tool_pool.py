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


_VALIDATE_CALLS = []


class CountingEcho(BaseTool):
    """Counting echo：记录 model_validate 被调用的次数。"""
    value: str = ""

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        _VALIDATE_CALLS.append(obj)
        return super().model_validate(obj, *args, **kwargs)

    def run(self, tctx=None):
        return f"ran:{self.value}"


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


def test_precheck_call_accepts_valid_and_skips_mcp():
    def never_run(**kwargs):
        raise AssertionError("precheck_call 不应执行 MCP 工具")

    mcp_entry = _McpEntry(
        {"type": "function", "function": {"name": "mcp__srv__do"}},
        never_run,
        never_run,
    )
    pool = ToolPool({"echo": Echo, "mcp__srv__do": mcp_entry})
    assert pool.precheck_call("echo", {"value": "x"}) is None
    assert pool.precheck_call("mcp__srv__do", {"anything": 1}) is None


def test_precheck_call_matches_execute_results():
    """预检与执行入口产生完全相同的错误值：参数错误和未知工具都统一格式。"""
    pool = ToolPool({"echo": Echo})

    bad_args = {"value": "x", "bogus": 1}
    assert pool.precheck_call("echo", bad_args) == pool.execute_sync("echo", bad_args)

    assert pool.precheck_call("nope", {}) == pool.execute_sync("nope", {})


def test_execute_validates_args_once():
    """执行入口复用 `_get_tool`，不会在它之外再 model_validate 一次。"""
    pool = ToolPool({"counting_echo": CountingEcho})

    _VALIDATE_CALLS.clear()
    assert pool.execute_sync("counting_echo", {"value": "x"}) == ToolResult(content="ran:x")
    assert len(_VALIDATE_CALLS) == 1

    _VALIDATE_CALLS.clear()
    assert asyncio.run(pool.execute("counting_echo", {"value": "y"})) == ToolResult(content="ran:y")
    assert len(_VALIDATE_CALLS) == 1


class MustNotRun(BaseTool):
    """Must not run：预检不应执行工具。"""
    def run(self, tctx=None):
        raise AssertionError("precheck_call 不应执行工具")


def test_precheck_call_does_not_execute_tool():
    assert ToolPool({"must_not_run": MustNotRun}).precheck_call("must_not_run", {}) is None


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
