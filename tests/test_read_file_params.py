"""read_file 分页参数约束：limit 1..MAX_OUTPUT_LINES，offset 从 1 起。"""
import pytest
from pydantic import ValidationError

from core.template import TOOL_ERROR_PREFIX
from core.tools import ToolPool
from core.tools.base_tools.read import MAX_OUTPUT_LINES, ReadFile


@pytest.mark.parametrize("field,value", [
    ("limit", 0),
    ("limit", -1),
    ("limit", MAX_OUTPUT_LINES + 1),
    ("offset", 0),
    ("offset", -5),
])
def test_rejects_out_of_range_values(field, value):
    with pytest.raises(ValidationError):
        ReadFile.model_validate({"path": "x", field: value})


def test_accepts_boundary_values():
    low = ReadFile.model_validate({"path": "x", "limit": 1, "offset": 1})
    high = ReadFile.model_validate({"path": "x", "limit": MAX_OUTPUT_LINES, "offset": 10_000})
    assert (low.limit, low.offset) == (1, 1)
    assert (high.limit, high.offset) == (MAX_OUTPUT_LINES, 10_000)


def test_schema_advertises_bounds():
    props = ReadFile.to_openai_tool()["function"]["parameters"]["properties"]
    assert props["limit"]["minimum"] == 1
    assert props["limit"]["maximum"] == MAX_OUTPUT_LINES
    assert props["offset"]["minimum"] == 1


def test_pool_rejects_invalid_paging_without_running(monkeypatch):
    calls = []
    monkeypatch.setattr(ReadFile, "run", lambda self, tctx=None: calls.append(self.path) or "ok")

    result = ToolPool({"read_file": ReadFile}).execute_sync("read_file", {"path": "x", "limit": 0})

    assert calls == [], "参数校验失败时不得执行工具"
    assert result.is_error is True
    assert result.content.startswith(TOOL_ERROR_PREFIX)
