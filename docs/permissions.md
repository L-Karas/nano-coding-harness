# 权限策略

每次工具调用先由**内置权限扩展**在 `before_tool` 的 `gate` 阶段统一裁决，再决定是否执行。
判定按三层依次进行：

1. **地板（floor）**：仅 `terminal`。硬编码的 `DENY_LIST`（清根删除、提权 `sudo`/`doas`、
   关机重启、`mkfs`/`wipefs`、`dd` 与裸设备覆写、fork bomb、Windows `diskpart`/`format c:` 等）
   命中即拒绝：不询问、不可被规则或扩展票覆盖。匹配大小写不敏感；词条首尾为字母或数字时要求
   单词边界（`sudo` 不命中 `sudoku`）。
2. **用户表态（votes）**：用户规则文件（规则层内部已按「后者覆盖前者」收敛为最多一票）+
   代码扩展的决定票（每个回调最多一票）。动作全部相同 → 采用该动作；动作不一致
   （规则 vs 扩展、扩展 vs 扩展）→ **弹确认卡由用户当场决定**，不静默选边。
3. **默认层（defaults）**：没有任何用户表态时生效。`terminal` 命中危险命令模式（删除类、
   系统目录重定向、递归 chmod/chown、强杀进程、`git reset --hard`/强推、`crontab -r`、
   `curl | sh` 等管道执行）或命令中显式引用了工作目录之外的路径 → 询问；文件工具路径越界 →
   询问；否则允许。越界扫描是尽力而为的文本解析：变量拼接、`sh -c` 内层脚本与粘连 flag
   （`-o/tmp/x`）不覆盖。

默认层与冲突裁决共用同一张确认卡：用户答 `y` / `yes` → 允许，其余（含无 UI 的 headless 场景）
→ 拒绝。冲突卡会列出各方来源（规则 pattern / 扩展模块名及其 reason）与最终命令或路径。

| 场景 | 结果 |
| --- | --- |
| 地板命中（无论规则/扩展投什么票） | 拒绝 |
| 只有规则命中或只有扩展表态 | 采用该表态 |
| 规则与扩展动作相同 | 采用该动作 |
| 规则与扩展动作不同 / 扩展之间动作不同 | 弹确认卡 |
| 有用户表态且动作一致，默认层持不同动作 | 采用用户表态（默认层不参与） |
| 无任何用户表态 | 默认层：破坏性 / 越界 → 询问；否则允许 |

## 规则文件

### 位置与生命周期

- 路径：`.harness/.permissions.json`（`core.config.PERMISSION_FILE`）；`init_harness()` 在文件
  缺失时创建 `{}`。
- 启动时读取一次（`register_builtin_extensions()` → `load_rules()`）；**修改后需重启进程**，
  与外部扩展一致，本期不做热加载。
- 文件不存在 → 空规则（仅地板 + 默认层），不报错。
- 规则只覆盖 `terminal`、`read_file`、`write_file`、`edit_file` 四类工具；其余工具（含 MCP）
  不受规则配置影响（扩展的 `decide` / `block` 决定票对所有工具可用）。

### 格式

```json
{
  "terminal": {
    "command": {
      "^git status": "allow",
      "git push --force": "deny",
      "^rm ": "ask"
    }
  },
  "read_file":  { "external_directory": "allow" },
  "write_file": { "external_directory": "deny" },
  "edit_file":  { "external_directory": "ask" }
}
```

action 取 `allow` / `ask` / `deny`（允许 / 询问 / 拒绝）。字段结构样例见
[`examples/permission_config_example.json`](../examples/permission_config_example.json)
（其中的 `pattern 1`/`pattern 2`/`pattern 3` 是占位文本）。

### 语义

- `terminal.command`：键为正则字符串，对命令做 `re.search`（编译带 `IGNORECASE`；可用作用域
  形式 `(?-i:...)` 局部关闭大小写不敏感，如 `(?-i:FOO)` 只匹配大写的 `FOO`）。按 JSON 键顺序
  逐条匹配，**最后一个命中的键生效**（新引入的规则压过早先的）。
- 文件工具 `external_directory`：仅在解析后的路径位于工作目录（`WORKDIR`）之外时生效；
  目录内调用与规则无关，直接允许。
- 非法 `action`、非法正则 → 丢弃该条规则并记 ERROR，其余规则照常生效。
- JSON 整体解析失败或顶层不是对象 → 整个文件不生效（退化为默认层）并记 ERROR。
- 未知顶层工具键、段内未知字段 → 忽略并记 WARN。

## 扩展决定票

`before_tool` 上下文提供决定语义，规则与扩展票汇入同一条决定链：

```python
def register(api):
    api.on("before_tool", trust_git_status)


def trust_git_status(ctx):
    if ctx.tool_name == "terminal" and ctx.args.get("command") == "git status":
        ctx.decide("allow", "只读命令")
```

- `ctx.decide(action, reason="")`：登记一票，`action ∈ {allow, ask, deny}`；非法值抛
  `ValueError`（该回调按失败处理，票丢弃）。
- 同一回调多次 `decide` 以最后一次为准；票来源由派发器自动标注（外部扩展
  `nano_extension_<名字>`，内置 `builtin:permission`），用于冲突卡与日志。
- `ctx.block(reason)` 保留，等价于 `ctx.decide("deny", reason)`——**是拒绝表决，不是硬拦截**：
  与 allow 规则/扩展票同时出现时改为弹确认卡，而不是直接拦死。
- **票与参数一致性**：回调结束时对参数做快照；若后续回调改写了参数，该票作废（记日志）并视为
  弃权。权限门永远按最终参数判定。
- 决定票不短路事件派发；`blocked` 由权限门在裁决后写入，此后剩余回调不再执行。

## 阶段与失败语义

回调按内部阶段排序执行，同一阶段内保持注册顺序：

```
user（外部扩展 / 测试直接注册的回调）→ observer（内置观察者）→ gate（内置权限门，固定最后）
```

- 外部扩展只能注册 `user` 阶段，也不能声明 `fail_closed`（`api.on()` 抛错使整个扩展作废）；
  `stage` / `fail_closed` 是内置扩展与内部接口的能力。
- `gate` 最后执行，保证权限门看到所有回调改写后的最终参数、收齐所有票；外部扩展没有任何
  公开注册点可以排到权限门之后。
- **外部扩展 fail-open**：单个回调抛异常只记日志（`dispatcher.log`）并视为弃权，后续回调与
  主流程继续。
- **内置权限门 fail-closed**：权限门自身抛异常 → 拒绝，工具结果为
  `Permission check failed: <ExceptionName>` 并记 ERROR，避免策略代码崩溃导致放行。
- 内置观察者（调用日志）在 `observer` 阶段先于权限门执行：被拒绝的调用同样进入
  调用日志。

### `use_extensions=False`

`StepPolicy.use_extensions=False` 只跳过**外部**回调（所有事件一律跳过）；内置观察者与权限门
照常执行，地板与规则仍然生效。不存在任何能关闭权限门或绕过地板的配置/开关/扩展点。

## 模型可见的结果文案

| 来源 | 工具结果 |
| --- | --- |
| 地板 | `Permission denied: '<pattern>' is on the deny list.` |
| 规则裁定拒绝 | `Permission denied by rule: <pattern>`（文件工具为 `<tool>.external_directory`） |
| 扩展裁定拒绝 | `[Extension blocked] <reason>` |
| 用户拒绝（询问或冲突卡被拒） | `Permission denied by user` |
| 权限门自身异常 | `Permission check failed: <ExceptionName>` |

## 已知风险

`.harness/.permissions.json` 与 `.harness/extensions/` 都位于工作目录内，agent 作为进程内代码
**可以写入这两个位置**；修改在重启进程后生效。本期不设写保护（用户已确认），应由用户人工审阅
规则与扩展的变更，只放入可信内容。

> 根目录 `.hooks.json` 与 `examples/s08_hook_system.py` 是独立教学示例，不被运行时读取，
> 与上述机制无关。

## 参考

- 设计契约：[`superpowers/specs/2026-10-13-permission-policy-design.md`](superpowers/specs/2026-10-13-permission-policy-design.md)
- 实现：[`core/extension/builtin/permission.py`](../core/extension/builtin/permission.py)、
  [`core/extension/dispatcher.py`](../core/extension/dispatcher.py)
- 外部扩展事件与决定票接口：[`extensions.md`](extensions.md)
- 示例规则：[`examples/permission_config_example.json`](../examples/permission_config_example.json)
