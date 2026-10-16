# 权限规则扩展：terminal 外部目录与 path 正则 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `.harness/.permissions.json` 支持 `terminal.external_directory` 与三个文件工具的 `path` 正则规则，并把「多条规则命中」从最后命中生效改为 deny > ask > allow 取最严。

**Architecture:** 只改一个实现文件 `core/extension/builtin/permission.py`：规则数据模型加一个 `paths` 字段；解析层支持新字段；匹配层拆成「收集全部命中（`match_command` / `match_path`）+ 按 severity 收敛（`resolve_matches`）」；权限门 `_rule_vote` 把直接规则与 `external_directory` 命中合并后收敛为至多一票，投进现有投票链。地板、扩展票冲突弹卡、默认层结构零改动。

**Tech Stack:** Python 3（dataclass、`re`、`pathlib`、`os.path`）、pytest、uv。

**Spec:** `docs/superpowers/specs/2026-10-16-permission-path-rules-design.md`

## Global Constraints

- action 只接受 `allow` / `ask` / `deny`（`core.extension.protocol.ACTIONS`）。
- 所有规则正则一律 `re.compile(pattern, re.IGNORECASE)` + `pattern.search(text)`；不要求整串匹配。
- 规则层内部恰好收敛出**至多一票**：deny > ask > allow，同 severity 取匹配列表中的**最后一条**；`external_directory` 命中追加在直接规则之后。扩展票的冲突弹卡语义不变。
- `external_directory: allow` 是规则票，整层跳过默认层（含 destructive 询问）——用户已确认 A 方案；**不改** `build_gate` / `_apply_defaults` 的结构与默认层文案。
- 文件工具 path 匹配串 = `(workdir / args["path"]).resolve()` 的工作区相对 POSIX 路径；`os.path.relpath` 抛 `ValueError`（Windows 跨盘符）时退化为 `resolved.as_posix()` 绝对路径。
- 地板（floor）行为、默认层触发条件与全部现有拒绝文案零变化。
- 测试命令：`uv run pytest tests/test_permission_rules.py tests/test_permission_gate.py -v`；每任务收尾跑 `uv run pytest tests/`。
- 提交信息用中文，主题前缀 `权限规则：`。

## Review Focus

最可能咬到使用者的五类输入 / 失败模式（每条都已在对应任务里挂了测试）：

1. **Windows 跨盘符 path**（`d:/...` 与工作区不同盘）：没有相对形式，退化为绝对 POSIX 路径后规则仍应可 deny——Task 2 的 `_relative_path` 单测。
2. **符号链接越界**：`link/secret.txt` 链接到工作区外，规则必须看到 resolve 后的路径（`../outside/secret.txt`）而不是链接入口——Task 2 的 gate 测试。
3. **path 表整体非法 / `path` 非对象**：丢表或丢条目，同段 `external_directory` 仍生效，不崩溃——Task 1 的两个测试。
4. **同 severity 多条命中取最后一条**：external_directory 追加在直接规则之后，deny 原因应显示 `read_file.external_directory`——Task 2 的 `test_external_deny_beats_path_allow` 与 `resolve_matches` 单测。
5. **terminal.external_directory 只对越界命令投票**：工作区内的 `cat ./notes.txt` 配 `deny` 也不拦——Task 2 的回归测试。

## File Structure

- `core/extension/builtin/permission.py`（Modify）：唯一实现文件。任务 1 动解析层（`Rules`、`_parse_*`），任务 2 动匹配与权限门（`match_command` / `match_path` / `resolve_matches` / `_relative_path` / `_rule_vote`）。不新建模块。
- `tests/test_permission_rules.py`（Modify）：解析与匹配语义测试。
- `tests/test_permission_gate.py`（Modify）：权限门端到端行为测试。
- `docs/permissions.md`、`docs/extensions.md`、`docs/configuration.md`（Modify）：文档同步（任务 3）。
- `docs/superpowers/specs/2026-10-16-permission-path-rules-design.md`、`examples/permission_config_example.json`（已存在于工作区，随任务 1 提交）。

---

### Task 1: 规则解析：terminal.external_directory 与文件工具 path 表

**Files:**
- Modify: `core/extension/builtin/permission.py`（`Rules` 约 27-36 行；`_parse_sections` 约 76 行起；`_parse_terminal_section` 约 95 行；`_parse_external_directory_section` 约 120 行）
- Modify: `tests/test_permission_rules.py`
- 一并提交：`docs/superpowers/specs/2026-10-16-permission-path-rules-design.md`、`examples/permission_config_example.json`

**Interfaces:**
- Consumes: `ACTIONS`、`_LOGGER`（已 import）。
- Produces:
  - `Rules.paths: dict[str, list[tuple[re.Pattern, str]]]`（新字段，默认空 dict）；`Rules.external_directory` 现在也可含 `"terminal"` 键。
  - `_parse_pattern_table(label: str, table: object, target: list[tuple[re.Pattern, str]]) -> int`：解析 `{正则: action}` 表，返回成功条目数；表非 dict → `_LOGGER.error(f"[Permission] {label} 必须是对象，忽略该段")` 并返回 0。
  - `_parse_file_tool_section(tool_name: str, section: object, rules: Rules) -> None`（替换 `_parse_external_directory_section`）。

- [ ] **Step 1: Write the failing tests**

在 `tests/test_permission_rules.py` 末尾追加：

```python
def test_terminal_external_directory_parsed(tmp_path):
    rules = load_rules(_write(tmp_path, {"terminal": {"external_directory": "allow"}}))
    assert external_directory_action(rules, "terminal") == "allow"


def test_terminal_with_only_external_directory_does_not_warn(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    load_rules(_write(tmp_path, {"terminal": {"external_directory": "allow"}}))
    assert not logger.warning.called


def test_file_tool_path_rules_parsed_in_order(tmp_path):
    rules = load_rules(_write(tmp_path, {"read_file": {"path": {"^\\.env$": "deny", "secret": "allow"}}}))
    assert [(pattern.pattern, action) for pattern, action in rules.paths["read_file"]] == [
        ("^\\.env$", "deny"), ("secret", "allow")]


def test_bad_path_entry_drops_only_that_entry(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    rules = load_rules(_write(tmp_path, {"read_file": {"path": {"(": "deny", "^\\.env$": "maybe", "secret": "allow"}}}))
    assert [(pattern.pattern, action) for pattern, action in rules.paths["read_file"]] == [("secret", "allow")]
    assert logger.error.call_count == 2


def test_path_non_dict_drops_table_keeps_external_directory(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    rules = load_rules(_write(tmp_path, {"read_file": {"external_directory": "ask", "path": "nope"}}))
    assert external_directory_action(rules, "read_file") == "ask"
    assert not rules.paths.get("read_file")
    assert logger.error.called


def test_file_tool_unknown_field_warns_rule_still_applies(tmp_path, monkeypatch):
    logger = MagicMock(); monkeypatch.setattr(permission, "_LOGGER", logger)
    rules = load_rules(_write(tmp_path, {"read_file": {"unknown": 1, "path": {"^a": "allow"}}}))
    assert rules.paths["read_file"][0][0].pattern == "^a"
    assert logger.warning.called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_permission_rules.py -v`
Expected: 新增 6 个测试 FAIL（`Rules` 无 `paths`、terminal 段拒绝 `external_directory`、文件工具段忽略 `path`）；既有测试仍 PASS。

- [ ] **Step 3: Implement the parser changes**

在 `permission.py` 内：

1. `Rules` 增加第三个字段（保留原有两个字段的默认工厂）：

```python
paths: dict[str, list[tuple[re.Pattern, str]]] = field(default_factory=dict)
```

2. 新增 `_parse_pattern_table`：表非 dict → ERROR 上面那条文案并返回 0；逐条校验 action ∈ `ACTIONS` 与 `re.compile(..., re.IGNORECASE)`，非法条目记 ERROR 并丢弃（沿用现有"非法 action，忽略规则 …" / "非法正则，忽略规则: …"文案）；成功条目 append 到 `target`，返回计数。

3. 重写 `_parse_terminal_section`：
   - 非 dict 段 → 现有 ERROR。
   - 未知键（不在 `{"command", "external_directory"}`）→ 现有 WARN 文案。
   - `external_directory` 键存在：action 合法则 `rules.external_directory["terminal"] = action`，否则 ERROR `f"[Permission] 非法 action，忽略 terminal.external_directory: {action!r}"`。
   - `command` 键存在：`_parse_pattern_table("terminal.command", section["command"], rules.terminal)`。
   - 两个来源合计产出为 0 → WARN `"[Permission] terminal 段未产出任何有效规则"`；不再有独立的「缺少 command」WARN。

4. 把 `_parse_external_directory_section` 改名为 `_parse_file_tool_section`（`_parse_sections` 同步调用点）：
   - 非 dict 段 → 现有 ERROR；未知键（不在 `{"external_directory", "path"}`）→ 现有 WARN 文案。
   - `external_directory` 键存在：同现有校验，写入 `rules.external_directory[tool_name]`。
   - `path` 键存在：`_parse_pattern_table(f"{tool_name}.path", section["path"], rules.paths.setdefault(tool_name, []))`。
   - 合计产出为 0 → WARN `f"[Permission] {tool_name} 段未产出任何有效规则"`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_permission_rules.py -v`
Expected: 全部 PASS（含既有测试；`match_terminal` 在本任务不动，其行为仍为最后命中生效，任务 2 才替换）。

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest tests/`
Expected: 全绿（任务 1 不改变任何裁决行为）。

- [ ] **Step 6: Commit**

```bash
git add core/extension/builtin/permission.py tests/test_permission_rules.py \
  docs/superpowers/specs/2026-10-16-permission-path-rules-design.md \
  docs/superpowers/plans/2026-10-16-permission-path-rules.md \
  examples/permission_config_example.json
git commit -m "权限规则：解析 terminal.external_directory 与文件工具 path 规则"
```

---

### Task 2: 匹配收敛（deny > ask > allow）与权限门接线

**Files:**
- Modify: `core/extension/builtin/permission.py`（替换 `match_terminal` 约 60-67 行；新增 `match_path` / `resolve_matches` / `_relative_path`；重写 `_rule_vote` 约 225-245 行；更新 `Rules` docstring）
- Modify: `tests/test_permission_rules.py`、`tests/test_permission_gate.py`

**Interfaces:**
- Consumes: Task 1 的 `Rules.paths` / `Rules.external_directory["terminal"]`；现有 `_outside_paths`、`_workdir`、`_FILE_TOOLS`、`Decision`。
- Produces:
  - `match_command(rules: Rules, command: str) -> list[tuple[str, str]]`：`rules.terminal` 的全部命中 `(action, pattern.pattern)`，按文件顺序；**删除** `match_terminal`。
  - `match_path(rules: Rules, tool_name: str, relative: str) -> list[tuple[str, str]]`：`rules.paths.get(tool_name, [])` 的全部命中，同上。
  - `resolve_matches(matches: list[tuple[str, str]]) -> tuple[str, str] | None`：severity `deny(0) > ask(1) > allow(2)`；同 severity 取列表中最后一条；空列表返回 None。
  - `_relative_path(resolved: Path, workdir: Path) -> str`：`Path(os.path.relpath(resolved, workdir)).as_posix()`；`ValueError` → `resolved.as_posix()`。
  - `_rule_vote` 内部新助手 `_rule_decision(matches) -> Decision | None`（`resolve_matches` 结果为 None 时 None，否则 `Decision(action, reason, source="rule")`）。

- [ ] **Step 1: Write the failing rules/matching tests**

`tests/test_permission_rules.py`：更新 import（`match_command`、`match_path`、`resolve_matches`、`_relative_path`，去掉 `match_terminal`），把 `test_parses_rules_in_file_order_last_match_wins` 替换为下面第一条，`test_missing_file_returns_empty_rules` 的断言改为 `match_command(rules, "ls") == []`，`test_matching_is_case_insensitive` 改为断言列表；新增：

```python
def test_match_command_returns_all_hits_in_file_order(tmp_path):
    rules = load_rules(_write(tmp_path, {"terminal": {"command": {"^git": "allow", "force": "deny", "^git push": "ask"}}}))
    assert match_command(rules, "git push --force") == [("allow", "^git"), ("deny", "force"), ("ask", "^git push")]


def test_match_path_returns_all_hits_in_file_order(tmp_path):
    rules = load_rules(_write(tmp_path, {"read_file": {"path": {"\\.env": "deny", "^src/": "allow"}}}))
    assert match_path(rules, "read_file", "src/.env") == [("deny", "\\.env"), ("allow", "^src/")]


def test_match_path_is_case_insensitive(tmp_path):
    rules = load_rules(_write(tmp_path, {"read_file": {"path": {"SECRET": "deny"}}}))
    assert match_path(rules, "read_file", "Secret.txt") == [("deny", "SECRET")]


def test_resolve_matches_severity_and_last_of_same_action():
    assert resolve_matches([]) is None
    assert resolve_matches([("allow", "a"), ("deny", "d")]) == ("deny", "d")
    assert resolve_matches([("deny", "d"), ("allow", "a")]) == ("deny", "d")
    assert resolve_matches([("allow", "a"), ("ask", "k")]) == ("ask", "k")
    assert resolve_matches([("allow", "a"), ("ask", "k"), ("deny", "d")]) == ("deny", "d")
    assert resolve_matches([("ask", "k1"), ("ask", "k2")]) == ("ask", "k2")


def test_relative_path_inside_outside_and_dot(tmp_path):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    assert _relative_path((workdir / "src" / "a.py").resolve(), workdir) == "src/a.py"
    assert _relative_path((tmp_path / "x.txt").resolve(), workdir) == "../x.txt"
    assert _relative_path(workdir.resolve(), workdir) == "."


def test_relative_path_cross_drive_falls_back_to_absolute(tmp_path, monkeypatch):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    resolved = (tmp_path / "other" / "x.txt").resolve()

    def boom(path, start):
        raise ValueError("path is on mount 'D:', start on mount 'E:'")

    monkeypatch.setattr(permission.os.path, "relpath", boom)
    assert _relative_path(resolved, workdir) == resolved.as_posix()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_permission_rules.py -v`
Expected: 新测试 FAIL（`match_command` / `match_path` / `resolve_matches` / `_relative_path` 未定义或 import 失败）。

- [ ] **Step 3: Write the failing gate tests**

`tests/test_permission_gate.py` 在「规则与扩展表态的合并裁决」区后追加：

```python
def test_command_rule_severity_deny_beats_later_allow():
    gate = build_gate(Rules(terminal=[(re.compile("^git"), "deny"), (re.compile("force"), "allow")]))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "git push --force"})
    gate(ctx)
    assert ctx.block_reason == "Permission denied by rule: ^git"


def test_path_rule_deny_blocks_inside_workdir():
    gate = build_gate(Rules(paths={"read_file": [(re.compile(r"\.env$"), "deny")]}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": "config/.env"})
    gate(ctx)
    assert ctx.block_reason == r"Permission denied by rule: \.env$"


def test_path_rule_allow_outside_skips_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    outside = str(Path.cwd().parent / "shared" / "x.txt")
    gate = build_gate(Rules(paths={"read_file": [(re.compile(r"^\.\./shared/"), "allow")]}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": outside})
    gate(ctx)
    assert not ctx.blocked


def test_path_rule_ask_prompts_with_pattern(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append(q) or "n")
    gate = build_gate(Rules(paths={"write_file": [(re.compile(r"^dist/"), "ask")]}))
    ctx = BeforeToolContext(tool_name="write_file", args={"path": "dist/bundle.js"})
    gate(ctx)
    assert cards == ["^dist/"]
    assert ctx.block_reason == "Permission denied by user"


def test_external_deny_beats_path_allow():
    outside = str(Path.cwd().parent / "shared" / "x.txt")
    gate = build_gate(Rules(
        paths={"read_file": [(re.compile(r"^\.\./shared/"), "allow")]},
        external_directory={"read_file": "deny"}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": outside})
    gate(ctx)
    assert ctx.block_reason == "Permission denied by rule: read_file.external_directory"


def test_external_allow_loses_to_path_deny():
    outside = str(Path.cwd().parent / "shared" / "x.txt")
    gate = build_gate(Rules(
        paths={"read_file": [(re.compile(r"^\.\./"), "deny")]},
        external_directory={"read_file": "allow"}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": outside})
    gate(ctx)
    assert ctx.block_reason == r"Permission denied by rule: ^\.\./"


def test_terminal_external_directory_allow_skips_outside_prompt(monkeypatch):
    _no_prompt(monkeypatch)
    gate = build_gate(Rules(external_directory={"terminal": "allow"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ../secret.txt"})
    gate(ctx)
    assert not ctx.blocked


def test_terminal_external_directory_allow_skips_destructive_prompt(monkeypatch):
    # 决策 A：external allow 是规则票，整层跳过默认层（含 destructive 询问）
    _no_prompt(monkeypatch)
    gate = build_gate(Rules(external_directory={"terminal": "allow"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm ../old.txt"})
    gate(ctx)
    assert not ctx.blocked


def test_terminal_external_directory_deny_blocks():
    gate = build_gate(Rules(external_directory={"terminal": "deny"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ../secret.txt"})
    gate(ctx)
    assert ctx.block_reason == "Permission denied by rule: terminal.external_directory"


def test_terminal_external_directory_ask_prompts(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append((q, details)) or "n")
    gate = build_gate(Rules(external_directory={"terminal": "ask"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ../secret.txt"})
    gate(ctx)
    assert cards == [("terminal.external_directory", ["command: cat ../secret.txt"])]


def test_terminal_external_directory_ignores_inside_command(monkeypatch):
    _no_prompt(monkeypatch)
    gate = build_gate(Rules(external_directory={"terminal": "deny"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "cat ./notes.txt"})
    gate(ctx)
    assert not ctx.blocked


def test_command_ask_beats_external_allow(monkeypatch):
    cards = []
    monkeypatch.setattr(permission, "ask_permission", lambda q, details: cards.append(q) or "y")
    gate = build_gate(Rules(terminal=[(re.compile("^rm "), "ask")], external_directory={"terminal": "allow"}))
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm ../old.txt"})
    gate(ctx)
    assert cards == ["^rm "]
    assert not ctx.blocked


def test_path_rule_matches_resolved_symlink_target(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    workdir = tmp_path / "proj"
    workdir.mkdir()
    _link_dir(workdir / "link", outside)
    gate = build_gate(Rules(paths={"read_file": [(re.compile(r"^\.\./outside/"), "deny")]}))
    ctx = BeforeToolContext(tool_name="read_file", args={"path": "link/secret.txt"}, cwd=workdir)
    gate(ctx)
    assert ctx.block_reason == r"Permission denied by rule: ^\.\./outside/"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_permission_gate.py -v`
Expected: 新测试 FAIL（`Rules` 无 `paths` 参数、`_rule_vote` 仍用最后命中生效）。

- [ ] **Step 5: Implement the matching + gate changes**

在 `permission.py` 内：

1. 删除 `match_terminal`，新增：

```python
def match_command(rules: Rules, command: str) -> list[tuple[str, str]]:
    """按文件顺序返回命令规则的全部命中 (action, 正则原文)。"""


def match_path(rules: Rules, tool_name: str, relative: str) -> list[tuple[str, str]]:
    """按文件顺序返回该工具 path 规则的全部命中 (action, 正则原文)。"""


_SEVERITY = {"deny": 0, "ask": 1, "allow": 2}


def resolve_matches(matches: list[tuple[str, str]]) -> tuple[str, str] | None:
    """deny > ask > allow；同 severity 取列表中最后一条；空列表返回 None。"""
```

2. 在 `_is_outside_workdir` 旁新增 `_relative_path`（relpath + `as_posix`；`ValueError` 退化绝对 POSIX）。

3. 重写 `_rule_vote`（保留原 docstring 的「每层最多一票」语义，补充 severity 说明）：

```python
def _rule_decision(matches: list[tuple[str, str]]) -> Decision | None:
    decided = resolve_matches(matches)
    if decided is None:
        return None
    return Decision(action=decided[0], reason=decided[1], source="rule")


def _rule_vote(rules: Rules, ctx: BeforeToolContext) -> Decision | None:
    tool_name, args = ctx.tool_name, ctx.args
    if tool_name == "terminal":
        command = args.get("command", "")
        matches = match_command(rules, command)
        action = external_directory_action(rules, "terminal")
        if action is not None and _outside_paths(command, _workdir(ctx)):
            matches.append((action, "terminal.external_directory"))
        return _rule_decision(matches)
    if tool_name in _FILE_TOOLS:
        workdir = _workdir(ctx)
        resolved = (workdir / args.get("path", "")).resolve()
        matches = match_path(rules, tool_name, _relative_path(resolved, workdir))
        action = external_directory_action(rules, tool_name)
        if action is not None and not resolved.is_relative_to(workdir):
            matches.append((action, f"{tool_name}.external_directory"))
        return _rule_decision(matches)
    return None
```

4. 更新 `Rules` docstring，删掉「命中的后者覆盖前者」；删除 `match_terminal` 里的 `# todo: 多条规则命中…`。

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_permission_rules.py tests/test_permission_gate.py -v`
Expected: 全部 PASS；既有冲突弹卡 / 默认层 / 地板 / 自定义 cwd 测试不变。

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest tests/`
Expected: 全绿。

```bash
git add core/extension/builtin/permission.py tests/test_permission_rules.py tests/test_permission_gate.py
git commit -m "权限规则：命中多条按 deny > ask > allow 收敛并接入权限门"
```

---

### Task 3: 文档同步

**Files:**
- Modify: `docs/permissions.md`（三层判定第 2 层描述、场景表、规则文件格式与语义、结果文案表）
- Modify: `docs/extensions.md`（4.4 节「权限门裁决顺序」一句）
- Modify: `docs/configuration.md`（第 28 行规则文件说明）

**Interfaces:**
- Consumes: Task 1/2 的最终格式与语义（本任务只改文字）。

- [ ] **Step 1: Update `docs/permissions.md`**

- 判定第 2 层改成：规则文件内部先按 「deny > ask > allow，同 severity 取文件内靠后一条」收敛为至多一票，再与扩展票合并；扩展票冲突仍弹卡。
- 场景表补一行：「同一次调用命中多条规则 → 按 deny > ask > allow；同 severity 取靠后一条」。
- 「格式」示例改为新形状（`terminal.external_directory` + 三个文件工具的 `path` 表），并指向 `examples/permission_config_example.json`。
- 「语义」：
  - `terminal.command` 条目删除「最后一个命中的键生效」，改为 severity；
  - 新增 `terminal.external_directory` 条目：仅在命令引用工作目录外路径时参与（越界扫描口径与默认层一致）；未配置时行为不变（默认层询问）；
  - 新增文件工具 `path` 条目：匹配串为 resolve 后的工作区相对 POSIX 路径，跨盘符退化为绝对 POSIX 路径，`re.search` + `IGNORECASE`，同时覆盖目录内与目录外；
  - 新增「规则收敛」条目：直接规则命中在前、`external_directory` 命中追加在后，按 severity 收敛成规则层一票；
  - 新增已知后果：`external_directory: allow` 会整层跳过默认层（含 destructive 询问），需要时用 command `ask` 规则补回；`external_directory: deny` 不会被同次命中的 path allow 压过。
- 结果文案表「规则裁定拒绝」一格补注：`<pattern>` 为 command/path 规则的正则原文，文件工具 `external_directory` 为 `<tool>.external_directory`（`terminal` 同理）。
- 「参考」小节的设计契约一行补上本设计文档：`[2026-10-16-permission-path-rules-design.md](superpowers/specs/2026-10-16-permission-path-rules-design.md)`（保留 2026-10-13 一条）。

- [ ] **Step 2: Update `docs/extensions.md` and `docs/configuration.md`**

- `docs/extensions.md` 4.4 节裁决顺序一句，在「用户表态（规则 + 扩展票…）」处补「规则层内部先按 deny > ask > allow 收敛为至多一票」。
- `docs/configuration.md` 第 28 行说明改为覆盖新能力：`权限规则（terminal 命令正则与外部目录策略 / 文件工具 path 正则与越界策略，见 permissions.md）`。

- [ ] **Step 3: Verify no stale wording and links exist**

Run: `grep -n "后者覆盖\|最后命中生效\|最后命中的键生效" docs/permissions.md docs/extensions.md docs/configuration.md`
Expected: 无输出（2026-10-13 历史设计文档允许保留旧表述，不在本任务的修改范围）。

Run: `grep -n "terminal.external_directory" docs/permissions.md && grep -n "2026-10-16-permission-path-rules-design.md" docs/permissions.md`
Expected: 两条新表述都能命中。

- [ ] **Step 4: Run the full suite and commit**

Run: `uv run pytest tests/`
Expected: 全绿。

```bash
git add docs/permissions.md docs/extensions.md docs/configuration.md
git commit -m "权限规则：同步权限与配置文档"
```

---

## 验收标准（来自 Spec）

- `uv run pytest tests/` 全绿；§8 列出的场景均有测试覆盖。
- 无规则时默认层行为与改动前完全一致；配 allow / ask / deny 后按配置收口。
- 规则层内部不存在 allow 压 deny 的路径；`external_directory: deny` 压过 path allow。
- 地板、扩展冲突弹卡、默认层文案与触发条件零变化。
