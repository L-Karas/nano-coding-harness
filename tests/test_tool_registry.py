"""注册期校验：至少覆写 run / arun 之一；装配按 agent_type 过滤并支持 exclude。"""
import pytest

import core.tools.tool_loader as tool_loader
from core.tools import assemble_tool_pool
from core.tools.tool_base import BaseTool
from core.tools.tool_loader import _validate_tool_class


def test_tool_without_implementation_is_rejected():
    class NoImpl(BaseTool):
        """No impl."""

    with pytest.raises(RuntimeError, match="neither run"):
        _validate_tool_class(NoImpl)


def test_tool_with_one_implementation_passes():
    class OnlyRun(BaseTool):
        """Only run."""

        def run(self, tctx=None):
            return "ok"

    _validate_tool_class(OnlyRun)


def test_assemble_filters_agent_type_and_exclude(monkeypatch):
    monkeypatch.setattr(tool_loader, "get_client_manager", lambda: None)
    pool = assemble_tool_pool("teammate", exclude=frozenset({"check_inbox"}))
    names = [s["function"]["name"] for s in pool.schemas()]
    assert "read_file" in names
    assert "web_search" in names          # 之前缺 sync handler 会让 teammate 装配崩
    assert "check_inbox" not in names
    assert "save_memory" not in names     # main-only
