# 权限策略与统一决定接口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把写死在 `core/hook/hook.py` 的权限判断改成「内置权限扩展 + 用户规则文件 + 代码扩展决定语义」的统一决定链。

**Architecture:** `core/extension/` 派发器新增阶段（`user → observer → gate`）与决定票（`decide`）；内置权限门（`core/extension/builtin/permission.py`）在地板 → 用户表态 → 默认层三层上裁决，冲突弹卡；用户规则来自 `.harness/.permissions.json`；旧 `core/hook/` 删除。

**Tech Stack:** Python 3.14、pytest、uv、现有 `core/extension/` 派发器与 `core/interaction` UI 端口。

**Spec:** `docs/superpowers/specs/2026-10-13-permission-policy-design.md`

## Global Constraints

- 覆盖工具只有 `terminal`、`read_file`、`write_file`、`edit_file`；`decide`/`block` 对全部工具可用，规则只解析这四类。
- 地板（现有 `DENY_LIST`）命中即拒绝：不询问、不可被规则/扩展覆盖；不得引入任何关闭权限门或绕过地板的开关。
- 用户表态不唯一（规则 vs 扩展、扩展 vs 扩展）必须弹确认卡；headless（`ask_permission` 默认 `"no"`）按拒绝收口。
- 内置权限门 fail-closed（异常 → 拒绝并记 ERROR）；外部扩展与直接 `dispatcher.add` 的回调维持 fail-open。
- 权限门固定最后执行；外部扩展没有任何公开注册点能排到它之后。
- 规则文件 `.harness/.permissions.json`：JSON、启动时读取、重启生效、不设写保护（用户负责）。
- 结果文案（模型可见，逐字）：地板 `Permission denied: '<pattern>' is on the deny list.`；用户拒绝 `Permission denied by user`；规则拒绝 `Permission denied by rule: <pattern>`；扩展拒绝 `[Extension blocked] <reason>`；门内异常 `Permission check failed: <ExceptionName>`。
- 非目标：MCP 工具、规则热加载、全局/项目规则继承、会话内记住允许、`.hooks.json` 与 s08 教学示例改造。

## Review Focus

| # | 风险输入/条件 | 期望行为 | 覆盖测试 |
|---|---|---|---|
| 1 | `.permissions.json` 非法 JSON / 非法正则 / 非法 action / 文件缺失 / 空对象 | 启动不崩、退默认层、ERROR 日志、其余条目照常 | Task 3 |
| 2 | 回调先投票、后续回调改写参数（TOCTOU） | 过期票作废不入裁决；权限门永远按最终参数判定 | Task 1、Task 6 |
| 3 | 权限门回调自身抛异常；外部回调抛异常 | 门 fail-closed 拒绝；外部 fail-open 弃权且不中断 | Task 1 |
| 4 | 冲突/默认询问在 headless 或用户答 no | 不执行工具，结果 `Permission denied by user` | Task 4 |
| 5 | `use_extensions=False`（外部全关） | 地板与规则仍然生效，内置观察者照常 | Task 6 |

---

### Task 1: 决定语义与派发器阶段

**Files:**
- Modify: `core/extension/context.py`
- Modify: `core/extension/dispatcher.py`
- Test: `tests/test_extension_dispatcher.py`

**Interfaces:**
- Produces:
  - `Decision(action: str, reason: str = "", source: str = "", args_snapshot: dict | None = None)`（`core/extension/context.py`）
  - `BeforeToolContext.decide(action: str, reason: str = "") -> None`：登记一票；action 不在 `{"allow","ask","deny"}` 抛 `ValueError`。`block()` 本任务保持旧行为（设置 `blocked`），Task 6 再迁移。
  - `core/extension/dispatcher.py`：`STAGES = ("user", "observer", "gate")`；`add(event, callback, module_name, *, stage="user", fail_closed=False, builtin=False)`；`dispatch(event, ctx, *, include_external=True)`；`_HANDLERS[event]` 元素为 `_Handler(module_name, callback, stage, fail_closed, builtin)`。
- Consumes: 现有 `dispatch` / `add` 调用方（loader、测试）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_extension_dispatcher.py`）

```python
def test_decide_collects_votes_without_blocking():
    dispatcher.add("before_tool", lambda ctx: ctx.decide("allow", "trusted"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: ctx.decide("deny", "no"), "ext_b")
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    _run("before_tool", ctx)
    assert [(d.action, d.source) for d in ctx.decisions] == [("allow", "ext_a"), ("deny", "ext_b")]
    assert ctx.blocked is False


def test_same_callback_keeps_only_last_vote():
    def hook(ctx):
        ctx.decide("ask", "first")
        ctx.decide("allow", "final")
    dispatcher.add("before_tool", hook, "ext_a")
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert [d.action for d in ctx.decisions] == ["allow"]


def test_vote_dropped_when_later_callback_rewrites_args():
    dispatcher.add("before_tool", lambda ctx: ctx.decide("allow", "looks safe"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: ctx.args.update({"command": "git push --force"}), "ext_b")
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git status"})
    _run("before_tool", ctx)
    assert ctx.decisions == []


def test_vote_kept_when_callback_rewrites_before_deciding():
    def hook(ctx):
        ctx.args["command"] = "git status"
        ctx.decide("allow", "normalized")
    dispatcher.add("before_tool", hook, "ext_a")
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git status "})
    _run("before_tool", ctx)
    assert [d.action for d in ctx.decisions] == ["allow"]


def test_stale_votes_dropped_before_gate_stage_runs():
    dispatcher.add("before_tool", lambda ctx: ctx.decide("allow"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: ctx.args.update({"command": "rm x"}), "ext_b")
    seen = []
    dispatcher.add("before_tool", lambda ctx: seen.append(list(ctx.decisions)),
                   "builtin:permission", stage="gate", fail_closed=True, builtin=True)
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    _run("before_tool", ctx)
    assert seen == [[]]


def test_stage_order_is_user_then_observer_then_gate():
    seen = []
    dispatcher.add("before_tool", lambda ctx: seen.append("gate"),
                   "builtin:permission", stage="gate", builtin=True)
    dispatcher.add("before_tool", lambda ctx: seen.append("user"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: seen.append("observer"),
                   "builtin:observers", stage="observer", builtin=True)
    _run("before_tool", BeforeToolContext(tool_name="terminal", args={}))
    assert seen == ["user", "observer", "gate"]


def test_include_external_false_skips_external_but_runs_builtin():
    seen = []
    dispatcher.add("before_tool", lambda ctx: seen.append("external"), "ext_a")
    dispatcher.add("before_tool", lambda ctx: seen.append("builtin"),
                   "builtin:observers", stage="observer", builtin=True)
    _run_external_off("before_tool", BeforeToolContext(tool_name="terminal", args={}))
    assert seen == ["builtin"]


def test_fail_closed_blocks_on_exception():
    def broken(ctx):
        raise RuntimeError("boom")
    dispatcher.add("before_tool", broken, "builtin:permission",
                   stage="gate", fail_closed=True, builtin=True)
    ctx = BeforeToolContext(tool_name="terminal", args={})
    _run("before_tool", ctx)
    assert ctx.blocked is True
    assert ctx.block_reason == "Permission check failed: RuntimeError"
```

配套：`_run` 之外新增 `def _run_external_off(event, ctx): asyncio.run(dispatcher.dispatch(event, ctx, include_external=False))`；新增 `test_decide_rejects_unknown_action`（`pytest.raises(ValueError)`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_extension_dispatcher.py -v`
Expected: 新增用例 FAIL（`decide` / `include_external` / `stage` 不存在）。

- [ ] **Step 3: 实现**

`context.py`：新增 `Decision` dataclass；`BeforeToolContext` 加 `decisions: list[Decision] = field(default_factory=list)` 与 `decide()`（做 action 校验，`reason` 用 `str()`）。

`dispatcher.py`：
- 用 `_Handler` dataclass 替换 `_HANDLERS` 里的裸元组；`add()` 校验 `stage in STAGES`。
- `dispatch(event, ctx, *, include_external=True)`：过滤 `builtin=False and not include_external`；按 `_STAGE_ORDER = {"user": 0, "observer": 1, "gate": 2}` 稳定排序后依次调用。
- 每个 callback 正常返回后：取本次新增的 `ctx.decisions[before:]`，**只保留最后一条**，写入 `source=module_name` 与 `args_snapshot=copy.deepcopy(ctx.args)`（仅当 ctx 有 `args` 属性）。
- 进入第一个 `stage=="gate"` 的 handler 之前执行 `_drop_stale_decisions(ctx)`：移除 `args_snapshot is not None and args_snapshot != ctx.args` 的票并记 info 日志。
- `except Exception`：维持 fail-open；若 `handler.fail_closed` 且 ctx 有 `blocked` 属性，则 `ctx.blocked = True; ctx.block_reason = f"Permission check failed: {type(e).__name__}"`，随后按既有短路条件 break。
- `_normalize` 增加 `decisions` 的非 list 防护（置空 + ERROR，日志文案与 `inject_messages` 一致）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_extension_dispatcher.py tests/test_extension_api.py -v`
Expected: PASS（本任务不改变 `block()` 与 `ExtensionAPI`，旧用例保持绿）。

- [ ] **Step 5: Commit**

```bash
git add core/extension/context.py core/extension/dispatcher.py tests/test_extension_dispatcher.py
git commit -m "扩展派发器：决定票、阶段排序与 fail-closed 元数据"
```

---

### Task 2: ExtensionAPI 内置支持与 loader 适配

**Files:**
- Modify: `core/extension/api.py`
- Modify: `core/extension/loader.py`
- Test: `tests/test_extension_api.py`、`tests/test_extension_loader.py`

**Interfaces:**
- Consumes: Task 1 的 `dispatcher.STAGES`、`dispatcher.add(...)`。
- Produces:
  - `ExtensionAPI(module_name: str, *, builtin: bool = False)`
  - `on(event, callback, *, stage="user", fail_closed=False)`；非内置实例传 `stage != "user"` 或 `fail_closed=True` 抛 `ValueError`；未知 stage 抛 `ValueError`。
  - `commit() -> list[tuple[str, Callable, str, bool]]`（event, callback, stage, fail_closed）。
  - loader 提交：`dispatcher.add(event, callback, module_name, stage=stage, fail_closed=fail_closed)`。

- [ ] **Step 1: 写失败测试**

`tests/test_extension_api.py` 更新 `test_on_buffers_and_commit_returns_registrations`：

```python
assert api.commit() == [("before_llm", fn, "user", False)]
```

新增：

```python
def test_external_api_rejects_builtin_stage_and_fail_closed():
    api = ExtensionAPI("nano_extension_demo")
    with pytest.raises(ValueError):
        api.on("before_tool", lambda ctx: None, stage="gate")
    with pytest.raises(ValueError):
        api.on("before_tool", lambda ctx: None, fail_closed=True)


def test_builtin_api_accepts_gate_stage():
    api = ExtensionAPI("builtin:permission", builtin=True)
    fn = lambda ctx: None
    api.on("before_tool", fn, stage="gate", fail_closed=True)
    assert api.commit() == [("before_tool", fn, "gate", True)]
```

`tests/test_extension_loader.py` 新增一条断言（复用现有加载 helper）：外部扩展注册后的 `_HANDLERS` 元素满足 `stage == "user" and fail_closed is False and builtin is False`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_extension_api.py tests/test_extension_loader.py -v`
Expected: FAIL（`builtin` 参数 / 4 元组 commit 不存在）。

- [ ] **Step 3: 实现**

`api.py`：构造函数加 `builtin=False`；`on` 用 `STAGES` 校验 stage；非内置实例的 stage/fail_closed 校验；`commit()` 返回 4 元组。`loader.py` 解包 4 元组并传给 `dispatcher.add`（外部 `builtin` 默认 False）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_extension_api.py tests/test_extension_loader.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add core/extension/api.py core/extension/loader.py tests/test_extension_api.py tests/test_extension_loader.py
git commit -m "扩展 API：内置扩展可声明 gate 阶段与 fail-closed"
```

---

### Task 3: 规则文件解析

**Files:**
- Create: `core/extension/builtin/__init__.py`（本任务只放包 docstring）
- Create: `core/extension/builtin/permission.py`（本任务只放规则部分）
- Test: `tests/test_permission_rules.py`

**Interfaces:**
- Produces:
  - `ACTIONS = ("allow", "ask", "deny")`
  - `@dataclass Rules(terminal: list[tuple[re.Pattern, str]] = field(default_factory=list), external_directory: dict[str, str] = field(default_factory=dict))`
  - `load_rules(path: Path) -> Rules`
  - `match_terminal(rules: Rules, command: str) -> tuple[str, str] | None`：按文件顺序取**最后一个**命中的 `(action, pattern.pattern)`。
  - `external_directory_action(rules: Rules, tool_name: str) -> str | None`
- Consumes: 文件名由 Task 7 的 `core.config.PERMISSION_FILE` 提供，本任务只接收 path。

- [ ] **Step 1: 写失败测试**（`tests/test_permission_rules.py`）

```python
def _write(tmp_path, payload):
    path = tmp_path / ".permissions.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_parses_rules_in_file_order_last_match_wins(tmp_path):
    rules = load_rules(_write(tmp_path, {"terminal": {"command": {"^git": "allow", "force": "deny"}}}))
    assert match_terminal(rules, "git push --force") == ("deny", "force")


def test_missing_file_returns_empty_rules(tmp_path):
    rules = load_rules(tmp_path / "none.json")
    assert rules.terminal == [] and rules.external_directory == {}
    assert match_terminal(rules, "ls") is None


def test_invalid_json_returns_empty_rules_and_logs_error(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    path = tmp_path / ".permissions.json"; path.write_text("{oops", encoding="utf-8")
    assert load_rules(path).terminal == []
    assert logger.error.called


def test_bad_regex_and_bad_action_drop_only_that_entry(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    rules = load_rules(_write(tmp_path, {"terminal": {"command": {"(": "deny", "^ls": "maybe", "^pwd": "allow"}}}))
    assert [pattern.pattern for pattern, _ in rules.terminal] == ["^pwd"]
    assert logger.error.call_count == 2


def test_matching_is_case_insensitive(tmp_path):
    rules = load_rules(_write(tmp_path, {"terminal": {"command": {"SUDO": "deny"}}}))
    assert match_terminal(rules, "sudo ls") == ("deny", "SUDO")


def test_external_directory_per_tool(tmp_path):
    rules = load_rules(_write(tmp_path, {
        "read_file": {"external_directory": "allow"},
        "write_file": {"external_directory": "deny"},
        "edit_file": {"external_directory": "ask"},
    }))
    assert external_directory_action(rules, "read_file") == "allow"
    assert external_directory_action(rules, "write_file") == "deny"
    assert external_directory_action(rules, "edit_file") == "ask"
    assert external_directory_action(rules, "grep") is None


def test_unknown_tool_and_unknown_field_ignored_with_warn(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    rules = load_rules(_write(tmp_path, {"grep": {"external_directory": "allow"},
                                         "terminal": {"unknown_field": 1, "command": {"^ls": "allow"}}}))
    assert external_directory_action(rules, "grep") is None
    assert [pattern.pattern for pattern, _ in rules.terminal] == ["^ls"]
    assert logger.warning.called
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_permission_rules.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

按 Spec §4.2：`json.loads` → 顶层必须 dict，否则 ERROR + `Rules()`；逐工具解析；action 非法 / `re.error` → ERROR 丢单条；未知工具与未知字段 WARN 忽略；非法顶层类型（terminal 值不是 dict 等）→ ERROR 忽略该工具段；编译用 `re.compile(pattern, re.IGNORECASE)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_permission_rules.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add core/extension/builtin/__init__.py core/extension/builtin/permission.py tests/test_permission_rules.py
git commit -m "权限规则文件：解析、校验与匹配语义"
```

---

### Task 4: 权限门

**Files:**
- Modify: `core/extension/builtin/permission.py`（迁移 `core/hook/hook.py` 的地板/默认层实现）
- Create: `tests/test_permission_gate.py`（迁移 `tests/test_hook_permission.py`）
- Delete: 无（旧文件 Task 7 删除）

**Interfaces:**
- Consumes: Task 1 的 `BeforeToolContext.decisions`、Task 3 的 `Rules` / `match_terminal` / `external_directory_action`、`core.interaction.ask_permission`、`core.config.WORKDIR`。
- Produces: `build_gate(rules: Rules) -> Callable[[BeforeToolContext], None]`；`register(api, rules: Rules) -> None`（内部用 `api.on("before_tool", build_gate(rules), stage="gate", fail_closed=True)`）；内部常量 `FLOOR_PATTERNS`、`DESTRUCTIVE_PATTERNS` 等保持 `core/hook/hook.py` 同名与行为。

- [ ] **Step 1: 迁移并新增测试**（`tests/test_permission_gate.py`）

迁移规则（逐条把 `tests/test_hook_permission.py` 的全部用例搬过来）：

```python
# 旧：result = hook_permission.permission_hook(make_tool_call(name, args)); assert result == "..."
# 新：
ctx = BeforeToolContext(tool_name=name, args=args)
build_gate(Rules())(ctx)
assert ctx.blocked and ctx.block_reason == "..."
# 旧 assert ... is None  →  新 assert not ctx.blocked
# patch_input(monkeypatch, fn)  →  monkeypatch.setattr(permission, "ask_permission", fn)
```

新增用例（构造投票用本地 helper：`_vote(action, reason="", source="nano_extension_x")` 直接构造 `Decision` 并设 source）：

```python
def test_rule_allow_overrides_destructive_default(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: pytest.fail("must not ask"))
    gate = build_gate(Rules(terminal=[(re.compile("^rm "), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm old.txt"})
    gate(ctx)
    assert not ctx.blocked


def test_rule_deny_blocks_with_rule_message():
    gate = build_gate(Rules(terminal=[(re.compile("^git push"), "deny")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git push origin main"})
    gate(ctx)
    assert ctx.block_reason == "Permission denied by rule: ^git push"


def test_external_directory_rule_allow_skips_prompt():
    gate = build_gate(Rules(external_directory={"write_file": "allow"}))
    ctx = BeforeToolContext(tool_name="write_file", args={"path": str(Path.cwd().parent / "x.txt")})
    gate(ctx)
    assert not ctx.blocked


def test_extension_vote_allow_overrides_default_ask():
    gate = build_gate(Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm old.txt"})
    ctx.decisions.append(_vote("allow", "trusted"))
    gate(ctx)
    assert not ctx.blocked


def test_conflict_rule_vs_extension_asks_and_yes_allows(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q: cards.append(q) or "y")
    gate = build_gate(Rules(terminal=[(re.compile("^git"), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git push --force"})
    ctx.decisions.append(_vote("deny", "no force push", source="nano_extension_guard"))
    gate(ctx)
    assert not ctx.blocked
    assert "Decision conflict" in cards[0]
    assert "nano_extension_guard: deny (no force push)" in cards[0]


def test_conflict_rejected_denies(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: "n")
    ...  # 同上，断言 ctx.blocked and ctx.block_reason == "Permission denied by user"


def test_floor_beats_rule_and_extension(monkeypatch):
    monkeypatch.setattr(permission, "ask_permission", lambda q: pytest.fail("floor must not ask"))
    gate = build_gate(Rules(terminal=[(re.compile("^sudo"), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "sudo ls"})
    ctx.decisions.append(_vote("allow", "trusted"))
    gate(ctx)
    assert ctx.block_reason == "Permission denied: 'sudo' is on the deny list."


def test_same_action_uses_first_vote_message():
    gate = build_gate(Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    ctx.decisions.append(_vote("deny", "first", source="ext_a"))
    ctx.decisions.append(_vote("deny", "second", source="ext_b"))
    gate(ctx)
    assert ctx.block_reason == "[Extension blocked] first"


def test_headless_conflict_defaults_to_deny():
    gate = build_gate(Rules())          # 不 patch ask_permission：headless 默认 "no"
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "ls"})
    ctx.decisions.append(_vote("allow", source="ext_a"))
    ctx.decisions.append(_vote("deny", reason="no", source="ext_b"))
    gate(ctx)
    assert ctx.block_reason == "Permission denied by user"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_permission_gate.py -v`
Expected: FAIL（`build_gate` 不存在）。

- [ ] **Step 3: 实现 `build_gate`**

把 `core/hook/hook.py` 的 `DENY_LIST`、`DESTRUCTIVE`、`_compile_pattern`、`_clean_token`、`_command_path_candidates`、`_msys_drive_to_windows`、`_is_outside_workdir`、`_outside_paths`、`_confirm` 原样迁移（行为与文案不变）。裁决顺序：

1. terminal 且命中 `FLOOR_PATTERNS` → `blocked=True`、`block_reason=f"Permission denied: '{pattern}' is on the deny list."`，返回。
2. 规则票：terminal 用 `match_terminal`（`reason=pattern`、`source="rule"`）；文件工具仅在路径越界且 `external_directory_action` 非 None 时投票（`reason=f"{tool}.external_directory"`）。
3. `votes = ([rule_vote] if rule_vote else []) + list(ctx.decisions)`；非空时：动作唯一 → allow 直接返回 / deny 写文案 / ask 弹卡（`ask_permission(card)` 非 `y|yes` 视为拒绝）；动作不唯一 → 冲突卡，拒绝则 `Permission denied by user`。
4. 无票 → 默认层：terminal 的 `DESTRUCTIVE` 与 `_outside_paths` 合并成一张卡（沿用旧文案与 `_confirm`）；文件工具越界卡沿用 `Access outside working directory:\n\n{tool}: {path}`；其余放行。

文案构造：`_call_desc(tool, args)`（terminal → command；三类文件工具 → `f"{tool}: {path}"`）；扩展拒绝 `f"[Extension blocked] {votes[0].reason}"`；规则拒绝 `f"Permission denied by rule: {votes[0].reason}"`；ask 单票卡 `f"{reason}\n\n{desc}"`；冲突卡 `f"Decision conflict:\n" + "\n".join(f"- {d.source}: {d.action} ({d.reason})" ...) + f"\n\n{desc}"`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_permission_gate.py tests/test_permission_rules.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add core/extension/builtin/permission.py tests/test_permission_gate.py
git commit -m "内置权限门：地板、规则与扩展表态的合并裁决"
```

---

### Task 5: 内置观察者与注册入口

**Files:**
- Create: `core/extension/builtin/observers.py`
- Modify: `core/extension/builtin/__init__.py`
- Test: `tests/test_extension_builtin.py`

**Interfaces:**
- Consumes: Task 2 的 `ExtensionAPI(builtin=True)`、Task 4 的 `build_gate` / `load_rules`。
- Produces:
  - `observers.register(api)`：`api.on("before_tool", tool_call_log_hook, stage="observer")`、`api.on("after_tool", large_tool_output_hook, stage="observer")`。
  - `register_builtin_extensions(*, rules: Rules | None = None) -> None`：`rules is None` 时 `load_rules(PERMISSION_FILE)`；依次注册 `builtin:permission`（gate 回调、`stage="gate"`、`fail_closed=True`）与 `builtin:observers`，用 `ExtensionAPI(builtin=True)` + `dispatcher.add(..., builtin=True)`。
  - 日志文案沿用：`[HOOK] call tool {name} with arguments: {json.dumps(args, ensure_ascii=False)}`；`[HOOK] large output from {name}: {size} chars`（阈值 100_000）。

- [ ] **Step 1: 写失败测试**（`tests/test_extension_builtin.py`）

```python
@pytest.fixture(autouse=True)
def _clean():
    dispatcher.reset(); yield; dispatcher.reset()


def test_builtins_register_observers_before_gate():
    register_builtin_extensions(rules=Rules())
    stages = [(h.module_name, h.stage) for h in dispatcher._HANDLERS["before_tool"]]
    assert stages == [("builtin:observers", "observer"), ("builtin:permission", "gate")]
    gate = dispatcher._HANDLERS["before_tool"][-1]
    assert gate.fail_closed is True and gate.builtin is True


def test_tool_call_is_logged_even_when_floor_blocks(monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(observers, "_LOGGER", logger)
    register_builtin_extensions(rules=Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "sudo ls"})
    asyncio.run(dispatcher.dispatch("before_tool", ctx))
    assert ctx.blocked
    assert logger.info.called


def test_large_output_warns(monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(observers, "_LOGGER", logger)
    register_builtin_extensions(rules=Rules())
    ctx = AfterToolContext(tool_name="terminal", args={}, result="x" * 100_001)
    asyncio.run(dispatcher.dispatch("after_tool", ctx))
    assert logger.warning.called


def test_use_extensions_false_still_enforces_floor():
    register_builtin_extensions(rules=Rules())
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "sudo ls"})
    asyncio.run(dispatcher.dispatch("before_tool", ctx, include_external=False))
    assert ctx.block_reason == "Permission denied: 'sudo' is on the deny list."
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_extension_builtin.py -v`
Expected: FAIL（模块/入口不存在）。

- [ ] **Step 3: 实现**

`observers.py`：两个回调 + `register(api)`（stage="observer"）。`builtin/__init__.py`：包 docstring + `register_builtin_extensions`（内部 `_register(module_name, register)` helper；`permission.register(api, rules)`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_extension_builtin.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add core/extension/builtin/__init__.py core/extension/builtin/observers.py tests/test_extension_builtin.py
git commit -m "内置扩展注册入口：观察者与权限门接线"
```

---

### Task 6: 主循环接线与 `block()` 迁移

**Files:**
- Modify: `core/extension/context.py`（`block()` 迁移为 `decide("deny", reason)`）
- Modify: `core/agent_step.py`、`core/agent_loop.py`、`core/sub_agent.py`
- Modify: `tests/test_agent_step.py`、`tests/test_extension_loop_wiring.py`、`tests/test_extension_tool_execute_wiring.py`、`tests/test_extension_subagent_wiring.py`、`tests/test_subagent_status.py`、`tests/test_experimental_tools_wiring.py`、`tests/test_extension_api.py`
- Consumes: Task 1–5 的全部产出。
- Produces:
  - `StepPolicy` 删除 `permission_hooks`；全部 dispatch 调用改为 `await dispatch(event, ctx, include_external=policy.use_extensions)`。
  - 被拦结果：`_tool_message(tool_call_id, tool_ctx.block_reason)`（逐字写入，不再拼前缀）。

- [ ] **Step 1: 写/改失败测试**

各 integration fixture 在 `reset_extensions()` 后补 `register_builtin_extensions(rules=Rules())`（保证权限门在场）；删除所有 `monkeypatch.setattr(step_mod, "trigger_hooks", ...)`（`tests/test_extension_loop_wiring.py:101,180`、`tests/test_extension_tool_execute_wiring.py:166`、`tests/test_extension_subagent_wiring.py:77`、`tests/test_subagent_status.py:77`、`tests/test_experimental_tools_wiring.py:62`）。

新增/改写用例（断言逐字）：

```python
# tests/test_agent_step.py
def test_floor_command_blocked_even_with_use_extensions_false(monkeypatch):
    outcome = _run(monkeypatch, [("", "", [_tool_call_cmd("sudo ls")], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=False))
    assert outcome.followup_messages[0]["content"] == "Permission denied: 'sudo' is on the deny list."

def test_gate_sees_args_after_extension_rewrite(monkeypatch):
    add_hook("before_tool", lambda ctx: ctx.args.update({"command": "sudo ls"}), "test")
    outcome = _run(monkeypatch, [("", "", [_tool_call_cmd("ls")], "tool_calls", None)],
                   policy=StepPolicy(use_extensions=True))
    assert outcome.followup_messages[0]["content"].startswith("Permission denied: 'sudo'")
```

`_tool_call_cmd(cmd)` 为本地 helper（替换 TOOL_CALL 的 command）。`tests/test_extension_api.py::test_abort_and_block_helpers_capture_state` 改为断言 `tool.decisions[-1].action == "deny"` 且 `tool.blocked is False`。`test_extension_tool_execute_wiring.py::test_permission_gate_runs_before_tool_execute` 改为用 floor 命令（`sudo ls`）并断言 `calls == []`、工具结果为地板文案；`test_use_extensions_false_does_not_dispatch` 改为断言外部回调不执行、外部被关时仍被地板拦截。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_agent_step.py tests/test_extension_loop_wiring.py tests/test_extension_tool_execute_wiring.py -v`
Expected: FAIL（`permission_hooks` 仍在 / 门未参与裁决）。

- [ ] **Step 3: 实现**

- `context.py`：`block(reason)` 改为仅 `self.decide("deny", reason)`。
- `agent_step.py`：删除 `from core.hook.hook import trigger_hooks` 与 `if policy.permission_hooks:` 分支；五个 dispatch 调用统一传 `include_external=policy.use_extensions`；blocked 分支写 `tool_ctx.block_reason` 原文（不再加 `[Extension blocked]`）。
- `agent_loop.py`：`MAIN_STEP_POLICY` 删除 `permission_hooks=True`。
- `sub_agent.py`：`StepPolicy(use_extensions=True)`。
- 测试按 Step 1 更新。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_agent_step.py tests/test_extension_loop_wiring.py tests/test_extension_tool_execute_wiring.py tests/test_extension_subagent_wiring.py tests/test_subagent_status.py tests/test_experimental_tools_wiring.py tests/test_extension_api.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add core/extension/context.py core/agent_step.py core/agent_loop.py core/sub_agent.py tests/
git commit -m "主循环接入统一决定链：权限门常驻、block 迁移为表决"
```

---

### Task 7: 启动接线与旧系统删除

**Files:**
- Modify: `core/config.py`、`core/bootstrap.py`、`tests/conftest.py`、`tests/test_bootstrap.py`
- Delete: `core/hook/hook.py`、`core/hook/__init__.py`、`tests/test_hook_permission.py`
- Test: `tests/test_bootstrap.py`

**Interfaces:**
- Produces: `core.config.PERMISSION_FILE = HARNESS_CONFIG_DIR / ".permissions.json"`；`bootstrap()` 调用 `register_builtin_extensions()` 取代 `install_default_hooks()`。

- [ ] **Step 1: 写/改失败测试**

`tests/test_bootstrap.py`：新增 `assert config.PERMISSION_FILE in bootstrap_mod._JSON_FILES`；并发初始化测试把 `hook_mod.install_default_hooks` 换成 `builtin_mod.register_builtin_extensions`，期望 `calls` 集合不变（`"hooks"` 改名 `"builtins"`）。`tests/conftest.py` 改为 `register_builtin_extensions(rules=Rules())`（空规则保证测试与本地 `.permissions.json` 无关）。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_bootstrap.py -v`
Expected: FAIL（`PERMISSION_FILE` / `register_builtin_extensions` 未接线）。

- [ ] **Step 3: 实现**

`config.py` 加常量；`bootstrap.py` 的 `_JSON_FILES` 加 `PERMISSION_FILE: {}`，函数内导入并调用 `register_builtin_extensions()`（位置与旧 `install_default_hooks()` 相同，先于 `load_extensions()`），docstring 中「出厂 hook」改为「内置扩展」；删除 `core/hook/` 与 `tests/test_hook_permission.py`。

- [ ] **Step 4: 验证无残留并跑测试**

```bash
grep -rn "core\.hook\|core/hook\|trigger_hooks\|install_default_hooks\|permission_hooks" core/ tests/ || echo "clean"
uv run pytest tests/test_bootstrap.py -v
```
Expected: `clean`；PASS。

- [ ] **Step 5: Commit**

```bash
git add -A core/config.py core/bootstrap.py core/hook tests/conftest.py tests/test_bootstrap.py tests/test_hook_permission.py
git commit -m "启动接线：内置权限扩展取代出厂 hook，删除 core/hook"
```

---

### Task 8: 文档更新与全量回归

**Files:**
- Modify: `docs/permissions.md`（重写）、`docs/extensions.md`、`README.md`、`docs/superpowers/specs/2026-10-08-extension-hooks-design.md`（「与旧 hook 的关系」一节标注被取代）
- Add: `examples/permission_config_example.json`（已存在于工作区，纳入版本控制）

- [ ] **Step 1: 改文档**

- `docs/permissions.md`：三层判定模型、`.harness/.permissions.json` 格式与语义（含 `examples/permission_config_example.json` 示例）、冲突询问、失败语义、四条结果文案；写明已知风险（规则/扩展文件在工作目录内、agent 可写、重启生效、应由用户人工审阅，本期不设写保护）。
- `docs/extensions.md`：`decide`/`block` 语义、`user → observer → gate` 顺序、内置 fail-closed vs 外部 fail-open、`use_extensions` 只控外部；删除「旧 hook 与新扩展相互独立」段落。
- `README.md`：权限一节改为指向新机制。
- 2026-10-08 spec：加一行「本设计的『与旧 hook 的关系』由 2026-10-13 权限策略设计取代」。

- [ ] **Step 2: 检查引用与全量回归**

```bash
grep -rn "core/hook\|trigger_hooks\|permission_hooks" docs/ README.md | grep -v "2026-10-08\|2026-10-13" || echo "clean"
uv run pytest tests/ -q
```
Expected: `clean`；全绿。

- [ ] **Step 3: 手动冒烟（可选，需交互环境）**

复制 `examples/permission_config_example.json` 到 `.harness/.permissions.json`，重启 TUI：`terminal` 执行匹配 allow 的规则不弹卡、匹配 deny 的规则被拒、`sudo` 仍被地板拒绝。

- [ ] **Step 4: Commit**

```bash
git add docs/ README.md examples/permission_config_example.json
git commit -m "文档：权限策略与统一决定接口"
```
