"""jev-for-terminal 示例扩展的单元 / 集成测试。

覆盖：注册门槛、请求体、allow/ask/deny 映射、置信度阈值、非 terminal 跳过、
失败兜底（含 HTTP 错误与非法 choice），以及经 dispatcher + 内置权限门的端到端裁决。

运行（仓库根目录）：
    uv run pytest tests/test_jev_for_terminal.py -q
"""
from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.extension.dispatcher as dispatcher  # noqa: E402
from core.config import AgentConfig  # noqa: E402
from core.extension.builtin import permission  # noqa: E402
from core.extension.builtin.permission import Rules, build_gate  # noqa: E402
from core.extension.context import BeforeToolContext  # noqa: E402

EXT_DIR = ROOT / "examples" / "jev-for-terminal"
EXT_FILE = EXT_DIR / "__init__.py"
MODULE_NAME = "jev_terminal_under_test"


@pytest.fixture(scope="module")
def jev():
    """按文件路径加载被测扩展（扩展不在 sys.path 上）。"""
    spec = importlib.util.spec_from_file_location(MODULE_NAME, EXT_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(MODULE_NAME, None)


@pytest.fixture
def no_dotenv(monkeypatch, jev):
    """register() 会 load_dotenv()；测试里禁用，保证环境变量完全由用例控制。"""
    monkeypatch.setattr(jev.dotenv, "load_dotenv", lambda *args, **kwargs: None)


def jev_answer(choice: str, confidence: float = 0.97) -> dict:
    """模拟 Jev Choice 回答（choice + confidence + probabilities）。"""
    return {"answers": {"action": {
        "type": "choice", "choice": choice, "confidence": confidence,
        "probabilities": {"allow": 0.0, "ask": 0.0, "deny": 0.0},
    }}}


def patch_http(jev, monkeypatch, handler) -> None:
    """把扩展模块里的 AsyncClient 换成带 MockTransport 的客户端，保留其真实响应处理逻辑。"""
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(jev, "AsyncClient", factory)


def terminal_ctx(command: str, cwd: Path | None = None) -> BeforeToolContext:
    return BeforeToolContext(tool_name="terminal", args={"command": command}, cwd=cwd)


def decide(jev, command: str, handler=None, monkeypatch=None) -> BeforeToolContext:
    if handler is not None:
        patch_http(jev, monkeypatch, handler)
    ctx = terminal_ctx(command)
    asyncio.run(jev.on_before_terminal(ctx))
    return ctx


# ---------- 注册 ----------

def test_register_requires_api_key(jev, monkeypatch, no_dotenv):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY|Typesafe AI API KEY"):
        jev.register(SimpleNamespace(on=lambda *args, **kwargs: None))


def test_register_wires_before_tool(jev, monkeypatch, no_dotenv):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    calls = []
    api = SimpleNamespace(on=lambda *args, **kwargs: calls.append((args, kwargs)))
    jev.register(api)
    assert calls == [(("before_tool", jev.on_before_terminal), {})]


# ---------- 请求体 ----------

def test_request_body_shape(jev):
    body = jev.build_request_body("rm -rf build", "E:/work")
    assert body["model"] == "jev-latest"
    assert "<command>rm -rf build</command>" in body["state"]
    assert "E:/work" in body["state"]
    action = body["questions"]["action"]
    assert action["type"] == "choice"
    assert set(action["criteria"]) == {"allow", "ask", "deny"}


# ---------- 决策映射 ----------

@pytest.mark.parametrize("choice", ["allow", "ask", "deny"])
def test_choice_becomes_exactly_one_matching_vote(jev, monkeypatch, choice):
    ctx = decide(jev, "echo hi", lambda request: httpx.Response(200, json=jev_answer(choice)),
                 monkeypatch)
    assert [decision.action for decision in ctx.decisions] == [choice]  # 不能多投一票
    assert ctx.blocked is False  # 扩展只投票，拦截由权限门裁决


@pytest.mark.parametrize("confidence,expected", [
    (1.0, "allow"),
    (0.8, "allow"),
    (0.79, "ask"),
    (0.0, "ask"),
])
def test_allow_respects_confidence_threshold(jev, monkeypatch, confidence, expected):
    ctx = decide(jev, "echo hi",
                 lambda request: httpx.Response(200, json=jev_answer("allow", confidence)),
                 monkeypatch)
    assert [decision.action for decision in ctx.decisions] == [expected]


@pytest.mark.parametrize("confidence", [None, "high", "", True])
def test_allow_without_numeric_confidence_votes_ask(jev, monkeypatch, confidence):
    payload = jev_answer("allow")
    if confidence is None:
        del payload["answers"]["action"]["confidence"]
    else:
        payload["answers"]["action"]["confidence"] = confidence
    ctx = decide(jev, "echo hi",
                 lambda request: httpx.Response(200, json=payload), monkeypatch)
    assert [decision.action for decision in ctx.decisions] == ["ask"]
    assert "confidence" in ctx.decisions[0].reason


@pytest.mark.parametrize("choice", ["ask", "deny"])
def test_threshold_does_not_downgrade_ask_or_deny(jev, monkeypatch, choice):
    ctx = decide(jev, "echo hi",
                 lambda request: httpx.Response(200, json=jev_answer(choice, confidence=0.0)),
                 monkeypatch)
    assert [decision.action for decision in ctx.decisions] == [choice]


def test_request_sends_command_and_token(jev, monkeypatch, tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode("utf-8")
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=jev_answer("allow"))

    with monkeypatch.context() as patch:
        patch.setenv("TYPESAFE_API_KEY", "test-key")
        decide(jev, "ls -la", handler, patch)
    assert "ls -la" in seen["body"]
    assert seen["auth"] == "Bearer test-key"


def test_non_terminal_tool_is_ignored(jev, monkeypatch):
    def handler(request):  # pragma: no cover - 不应被调用
        pytest.fail("non-terminal tool must not call Jev")

    patch_http(jev, monkeypatch, handler)
    ctx = BeforeToolContext(tool_name="read_file", args={"path": "a.txt"})
    asyncio.run(jev.on_before_terminal(ctx))
    assert ctx.decisions == []


@pytest.mark.parametrize("args", [{}, {"command": ""}])
def test_terminal_without_command_is_ignored(jev, monkeypatch, args):
    def handler(request):  # pragma: no cover - 不应被调用
        pytest.fail("empty command must not call Jev")

    patch_http(jev, monkeypatch, handler)
    ctx = BeforeToolContext(tool_name="terminal", args=args)
    asyncio.run(jev.on_before_terminal(ctx))
    assert ctx.decisions == []


# ---------- 失败兜底（一律 ask） ----------

def test_network_error_votes_ask(jev, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("network down", request=request)

    ctx = decide(jev, "ls", handler, monkeypatch)
    assert [decision.action for decision in ctx.decisions] == ["ask"]


def test_non_json_body_votes_ask(jev, monkeypatch):
    ctx = decide(jev, "ls", lambda request: httpx.Response(502, text="<html>bad gateway</html>"),
                 monkeypatch)
    assert [decision.action for decision in ctx.decisions] == ["ask"]


@pytest.mark.parametrize(
    "status,payload",
    [(401, {"detail": {"error_type": "authentication_error"}}),
     (429, {"error": "rate limited"}),
     (500, {"error": "internal"}),
     (200, {}),
     (200, {"answers": {}}),
     (200, {"answers": {"action": {}}})],
)
def test_error_or_incomplete_response_votes_ask(jev, monkeypatch, status, payload):
    ctx = decide(jev, "rm -rf build", lambda request: httpx.Response(status, json=payload),
                 monkeypatch)
    assert [decision.action for decision in ctx.decisions] == ["ask"]


@pytest.mark.parametrize("payload", [[], "ok", 42])
def test_non_object_response_votes_ask(jev, monkeypatch, payload):
    ctx = decide(jev, "ls", lambda request: httpx.Response(200, json=payload), monkeypatch)
    assert [decision.action for decision in ctx.decisions] == ["ask"]


@pytest.mark.parametrize("choice", ["probably-fine", "ALLOW", "", None])
def test_unknown_choice_votes_ask(jev, monkeypatch, choice):
    ctx = decide(jev, "rm -rf build",
                 lambda request: httpx.Response(200, json=jev_answer(choice)),
                 monkeypatch)
    assert [decision.action for decision in ctx.decisions] == ["ask"]


def test_reason_matches_action(jev, monkeypatch):
    expected = {
        "allow": "safe",
        "ask": "unsure",
        "deny": "dangerous",
    }
    for choice, word in expected.items():
        ctx = decide(jev, "echo hi",
                     lambda request, choice=choice: httpx.Response(200, json=jev_answer(choice)),
                     monkeypatch)
        reason = ctx.decisions[0].reason.lower()
        assert word in reason
        assert "blocked" not in reason


# ---------- 与权限门的端到端裁决 ----------

@pytest.fixture
def wired(jev, monkeypatch, tmp_path):
    dispatcher.reset()
    monkeypatch.setattr(
        permission, "CONFIGMANAGER",
        SimpleNamespace(config=AgentConfig(approval_mode="Manual")))
    monkeypatch.setattr(permission, "WORKDIR", tmp_path)
    prompts: list[tuple[str, list[str]]] = []
    answers = {"value": "no"}

    def fake_ask(question: str, details: list[str]) -> str:
        prompts.append((question, details))
        return answers["value"]

    monkeypatch.setattr(permission, "ask_permission", fake_ask)
    dispatcher.add("before_tool", jev.on_before_terminal, "nano_extension_jev-for-terminal")
    dispatcher.add("before_tool", build_gate(Rules()), "builtin:permission",
                   stage="gate", fail_closed=True, builtin=True)
    yield SimpleNamespace(prompts=prompts, answers=answers, cwd=tmp_path)
    dispatcher.reset()


def run_gate(command: str, cwd: Path) -> BeforeToolContext:
    ctx = terminal_ctx(command, cwd=cwd)
    asyncio.run(dispatcher.dispatch("before_tool", ctx))
    return ctx


@pytest.mark.parametrize("choice,answer,blocked", [
    ("allow", "no", False),   # allow 票压过默认层的破坏性命令确认
    ("ask", "yes", False),
    ("ask", "no", True),
    ("deny", "yes", True),    # deny 直接拦截，不询问
])
def test_gate_end_to_end(wired, jev, monkeypatch, choice, answer, blocked):
    wired.answers["value"] = answer
    patch_http(jev, monkeypatch,
               lambda request: httpx.Response(200, json=jev_answer(choice)))
    ctx = run_gate("rm -rf build", wired.cwd)

    assert len(ctx.decisions) == 1
    assert ctx.blocked is blocked
    if choice == "allow":
        assert wired.prompts == []
    elif choice == "ask":
        assert len(wired.prompts) == 1
        assert any("command: rm -rf build" in line for line in wired.prompts[0][1])
    else:
        assert wired.prompts == []
        assert ctx.block_reason.startswith("[Extension blocked]")


def test_gate_low_confidence_allow_asks(wired, jev, monkeypatch):
    """低于阈值的 allow 必须转为确认；`echo hi` 本身不会触发默认层。"""
    wired.answers["value"] = "yes"
    patch_http(jev, monkeypatch,
               lambda request: httpx.Response(200, json=jev_answer("allow", confidence=0.5)))
    ctx = run_gate("echo hi", wired.cwd)
    assert len(wired.prompts) == 1
    assert ctx.blocked is False


def test_gate_failure_falls_back_to_ask(wired, jev, monkeypatch):
    wired.answers["value"] = "no"

    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    patch_http(jev, monkeypatch, handler)
    ctx = run_gate("rm -rf build", wired.cwd)
    assert len(wired.prompts) == 1
    assert ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_gate_http_error_asks_user(wired, jev, monkeypatch):
    """回归：API 错误（401/429/5xx JSON）必须询问用户，不能静默 allow。"""
    wired.answers["value"] = "yes"
    patch_http(jev, monkeypatch,
               lambda request: httpx.Response(500, json={"error": "internal"}))
    ctx = run_gate("rm -rf build", wired.cwd)
    assert len(wired.prompts) == 1
    assert ctx.blocked is False


# ---------- 加载门槛 ----------

def test_loader_loads_enabled_extension(tmp_path, monkeypatch, jev):
    """复制扩展并启用：走真实加载器路径，验证包加载 + register 提交。"""
    from core.extension import reset_extensions
    from core.extension.loader import load_extensions

    reset_extensions()
    target = tmp_path / "jev-for-terminal"
    shutil.copytree(EXT_DIR, target, ignore=shutil.ignore_patterns("__pycache__", "test_*"))
    (target / "extension.json").write_text('{"enabled": true}', encoding="utf-8")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(jev.dotenv, "load_dotenv", lambda *args, **kwargs: None)
    try:
        load_extensions(tmp_path)
        handlers = dispatcher._HANDLERS.get("before_tool", [])
        assert len(handlers) == 1
        assert handlers[0].module_name == "nano_extension_jev-for-terminal"
        assert handlers[0].callback.__name__ == "on_before_terminal"
    finally:
        reset_extensions()
