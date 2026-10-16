# 权限策略

每次工具调用先由**内置权限扩展**在 `before_tool` 的 `gate` 阶段统一裁决，再决定是否执行。
判定按三层依次进行：

1. **地板（floor）**：仅 `terminal`。硬编码规则分两类（模式表在 `permission_patterns.py`），
   命中即拒绝，不询问、不可被规则或扩展票覆盖；匹配大小写不敏感，匹配前把 tab 与连续空格
   归一为单空格。
   - `DENY_LIST`：灾难子串，命令任意位置命中即拒绝。清根只匹配根目录本身
     （`rm -rf /tmp/foo` 降为确认卡）；此外包括 `--no-preserve-root`、`> /dev/<块设备>`、
     结构匹配的 fork bomb。
   - `DENY_COMMAND_LIST`：危险词，只在命令位命中（行首或 `;` `&&` `||` `|` `(` `{` 换行、
     `$(`、反引号之后，允许前置 `VAR=x` 与命令路径前缀，如 `/usr/bin/sudo`）：提权
     `sudo`/`doas`、关机重启（含 `systemctl reboot|poweroff|halt`）、`mkfs`/`wipefs`、
     `dd of=/dev/<块设备>` 写裸设备、Windows `diskpart`/`format <盘符>:`/`clear-disk`。
     因此 `echo sudo`、`man reboot` 不再被硬拒；wrapper 调用（`env`/`nice`/`xargs sudo`）
     与 `sh -c 'sudo ...'` 内层这类非命令位写法不再单独覆盖（灾难后果仍由清根等子串兜底）。
   子串条目首尾为字母或数字时要求单词边界（`sudo` 不命中 `sudoku`）。
2. **用户表态（votes）**：用户规则文件（规则层内部先按「deny > ask > allow，同 severity 取
   文件内靠后一条」收敛为最多一票）+ 代码扩展的决定票（每个回调最多一票）。动作全部相同 →
   采用该动作；动作不一致（规则 vs 扩展、扩展 vs 扩展）→ **弹确认卡由用户当场决定**，
   不静默选边。
3. **默认层（defaults）**：没有任何用户表态时生效。`terminal` 命中危险命令模式（删除类、
   系统目录重定向（含 `>/etc` 粘连写法）、递归 chmod/chown、世界可写 chmod、强杀进程、
   `git reset --hard`/`git clean -fd`/强推、`crontab -r`、`curl | sh`/`| python3` 等管道
   执行、`dd` 等）或命令中显式引用了工作目录之外的路径 → 询问；文件工具路径越界 → 询问；
   否则允许。越界扫描是尽力而为的文本解析：变量拼接、`sh -c` 内层脚本与粘连 flag
   （`-o/tmp/x`）不覆盖。

越界判定的基准是**调用方工作目录**：即 `BeforeToolContext.cwd`（`ToolContext.cwd`），
主代理为 `WORKDIR`，子代理为其 `working_directory`（缺省与主代理一致）。

默认层与冲突裁决共用同一张确认卡：用户答 `y` / `yes` → 允许，其余（含无 UI 的 headless 场景）
→ 拒绝。冲突卡会列出各方来源（规则 pattern / 扩展模块名及其 reason）与最终命令或路径。
确认文本（原因 / 冲突标题）样式不变；触发数据（冲突来源清单、越界路径清单、
`command: <命令原文>` / `path: <路径>`）按工具卡正文样式（dim）着色。

| 场景 | 结果 |
| --- | --- |
| 地板命中（无论规则/扩展投什么票） | 拒绝 |
| 只有规则命中或只有扩展表态 | 采用该表态 |
| 同一次调用命中多条规则 | 按 deny > ask > allow；同 severity 取文件内靠后一条 |
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
    "external_directory": "allow",
    "command": {
      "^git status": "allow",
      "git push --force": "deny",
      "^rm ": "ask"
    }
  },
  "read_file": {
    "external_directory": "allow",
    "path": {
      "^src/": "allow",
      "\\.env$": "deny",
      "^\\.\\./": "ask"
    }
  },
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
  收集全部命中。
- `terminal.external_directory`：仅在命令（经默认层同一套越界扫描）引用了调用方工作目录之外的
  路径时参与；未配置时行为不变（默认层询问）。
- 文件工具 `path`：键为正则字符串，对**工作区相对路径**做 `re.search`（同样 `IGNORECASE`）：
  参数路径先 `resolve()`，再换算成相对调用方工作目录的 POSIX 路径（`./src/a.py` → `src/a.py`，
  工作区外的 `../secret.txt`；Windows 跨盘符没有相对形式时退化为绝对 POSIX 路径）。
  目录内与目录外的路径都会匹配；`path` 只对 `read_file` / `write_file` / `edit_file` 生效。
- 文件工具 `external_directory`：仅在解析后的路径位于调用方工作目录之外时生效（见上文基准定义）；
  目录内调用不受它影响（但仍受 `path` 规则影响）。
- 规则收敛：同一次调用的全部命中（`terminal.command` / 文件工具 `path`，加上越界时的
  `external_directory`）按 **deny > ask > allow** 取最严；同一 severity 多条命中时取 JSON 中
  靠后的一条；`external_directory` 命中视为追加在直接规则之后，因此规则层只产出至多一票。
  该收敛对旧规则文件只会不变或变严：旧语义取最后一条命中，它必在命中集合内，而新语义取集合中
  最严的一条，不存在「新代码放行了旧代码会拦下的调用」。
- 已知后果：`external_directory: allow` 是一张规则票，会跳过该次调用的整个默认层（含破坏性
  命令询问）；如需保留确认，可用 command `ask` 规则补回（ask 严于 allow，必然胜出）。
  `external_directory: deny` 不会被同次命中的 `path` allow 压过（deny 优先）。
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
| 规则裁定拒绝 | `Permission denied by rule: <pattern>`（command / path 规则为 JSON 中的正则原文；`external_directory` 为 `<tool>.external_directory`，`terminal` 同理） |
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
- 规则扩展设计（terminal 外部目录 / 文件工具 path 正则）：[`superpowers/specs/2026-10-16-permission-path-rules-design.md`](superpowers/specs/2026-10-16-permission-path-rules-design.md)
- 实现：[`core/extension/builtin/permission.py`](../core/extension/builtin/permission.py)、
  [`core/extension/builtin/permission_patterns.py`](../core/extension/builtin/permission_patterns.py)、
  [`core/extension/dispatcher.py`](../core/extension/dispatcher.py)
- 外部扩展事件与决定票接口：[`extensions.md`](extensions.md)
- 示例规则：[`examples/permission_config_example.json`](../examples/permission_config_example.json)
