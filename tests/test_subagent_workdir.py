"""子代理工具 role / goal / working_directory 接线：role、goal 进右栏状态，工作目录进子代理 ToolContext。

role 是右栏展示标签并校验为短单行；goal 供展开时展示；working_directory 缺省与主代理一致，
相对路径按主代理工作目录解析。
"""
import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import core.agent_step as step_mod
import core.sub_agent as sa
import core.tools
import core.tools.extra_tools.subagent as subagent_tool_mod
from core.config import WORKDIR
from core.runtime_context import ToolContext
from core.runtime_state import RuntimeState
from core.tools.extra_tools.subagent import SpawnSubagent


def test_schema_declares_role_and_goal_required_and_working_directory_optional():
    params = SpawnSubagent.to_openai_tool()["function"]["parameters"]
    assert "role" in params["properties"]
    assert "goal" in params["properties"]
    assert "working_directory" in params["properties"]
    assert "role" in params["required"]
    assert "goal" in params["required"]
    assert "working_directory" not in params["required"]
    role_schema = params["properties"]["role"]
    assert role_schema["minLength"] == 1
    assert role_schema["maxLength"] == 32
    assert "pattern" in role_schema


@pytest.mark.parametrize("bad_role", ["", "   ", "line\nbreak", "x" * 33])
def test_schema_rejects_invalid_role(bad_role):
    with pytest.raises(ValidationError):
        SpawnSubagent(description="do it", goal="demo", role=bad_role)


def _capture_tool_spawn(monkeypatch):
    calls = []

    async def fake_spawn(description, ctx=None, goal="", working_directory=None, role="subagent"):
        calls.append({"description": description, "goal": goal, "working_directory": working_directory,
                      "role": role})
        return "ok"

    monkeypatch.setattr(subagent_tool_mod, "spawn_subagent", fake_spawn)
    return calls


def test_tool_defaults_working_directory_to_parent_cwd(monkeypatch, tmp_path):
    calls = _capture_tool_spawn(monkeypatch)
    out = asyncio.run(SpawnSubagent(description="do it", goal="demo", role=" reviewer ")
                      .arun(ToolContext(cwd=tmp_path)))
    assert out == "ok"
    assert calls[0]["goal"] == "demo"
    assert calls[0]["role"] == "reviewer"
    assert calls[0]["working_directory"] == tmp_path


def test_tool_resolves_relative_working_directory_against_parent_cwd(monkeypatch, tmp_path):
    calls = _capture_tool_spawn(monkeypatch)
    (tmp_path / "pkg").mkdir()
    asyncio.run(SpawnSubagent(description="do it", goal="demo", role="reviewer", working_directory="pkg")
                .arun(ToolContext(cwd=tmp_path)))
    assert calls[0]["working_directory"] == (tmp_path / "pkg").resolve()


def test_tool_rejects_nonexistent_working_directory(monkeypatch, tmp_path):
    _capture_tool_spawn(monkeypatch)
    with pytest.raises(ValueError, match="does not exist or is not a directory"):
        asyncio.run(SpawnSubagent(description="do it", goal="demo", role="reviewer", working_directory="missing")
                    .arun(ToolContext(cwd=tmp_path)))


def _patch_spawn(monkeypatch, state, captured):
    monkeypatch.setattr(sa, "RUNTIME_STATE", state)
    monkeypatch.setattr(sa, "shared_sub_model_client", lambda: object())

    def fake_prompt(agent_type, tools=None, work_directory=None):
        captured["prompt"] = {"agent_type": agent_type, "work_directory": work_directory}
        return "sys"

    monkeypatch.setattr(sa, "build_system_prompt", fake_prompt)

    async def prepare(messages, ctx=None, sub_model=False):
        return messages

    monkeypatch.setattr(sa, "prepare_messages", prepare)

    class _Pool:
        def schemas(self):
            return []

    monkeypatch.setattr(core.tools, "assemble_tool_pool", lambda *a, **k: _Pool())

    async def fake_step(history, system_prompt, **kwargs):
        captured["tctx"] = kwargs["tctx"]
        captured["entry"] = state.snapshot().subagents[0]
        return SimpleNamespace(aborted=False, assistant_message={"content": "done"},
                               followup_messages=[], tool_calls=[])

    monkeypatch.setattr(step_mod, "run_agent_step", fake_step)


def test_spawn_subagent_records_goal_and_runs_in_working_directory(monkeypatch, tmp_path):
    state = RuntimeState()
    captured = {}
    _patch_spawn(monkeypatch, state, captured)
    assert asyncio.run(sa.spawn_subagent("explore", goal="find the bug", role="reviewer",
                                         working_directory=tmp_path)) == "done"
    assert captured["entry"].description == "explore"
    assert captured["entry"].goal == "find the bug"
    assert captured["entry"].role == "reviewer"
    assert captured["tctx"].cwd == tmp_path
    assert captured["tctx"].agent_name == "sub-agent"
    assert captured["prompt"] == {"agent_type": "sub-agent", "work_directory": tmp_path}
    assert state.snapshot().subagents == []


def test_spawn_subagent_defaults_to_workdir(monkeypatch):
    state = RuntimeState()
    captured = {}
    _patch_spawn(monkeypatch, state, captured)
    asyncio.run(sa.spawn_subagent("explore", goal="g"))
    assert captured["entry"].role == "subagent"
    assert captured["tctx"].cwd == WORKDIR
    assert captured["prompt"]["work_directory"] == WORKDIR
