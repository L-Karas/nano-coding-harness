# 权限策略与统一决定接口设计（nano-harness）

- 日期：2026-10-13
- 状态：待评审
- 关联代码：`core/hook/hook.py`、`core/extension/`、`core/agent_step.py`、`core/bootstrap.py`、`core/config.py`、`core/interaction.py`
- 关联文档：`docs/permissions.md`、`docs/extensions.md`、`docs/superpowers/specs/2026-10-08-extension-hooks-design.md`（本设计取代其「与旧 hook 的关系」一节）

## 1. 意图

权限判断现在写死在 `core/hook/hook.py`：两组硬编码命令列表 + 工作目录边界。用户无法表达「这个项目里这条命令我信任」「这个目录允许写」，只能改 harness 源码。同时 `core/extension/` 已有统一的事件、上下文与短路语义，却和旧 hook 各跑各的，工具节点上存在两条并行的判断路径。

本设计做两件事：

1. **权限策略可配置**：用户在 `.harness/.permissions.json` 写规则（terminal 命令正则 → 允许/询问/拒绝；文件工具的目录边界策略）。
2. **权限可编程**：代码扩展在 `before_tool` 上获得决定语义（allow/ask/deny），与配置规则汇入同一条决定链；旧 hook 的三个钩子重写为内置扩展，`core/hook/` 删除。

动机是让权限策略可以被引入与扩展（产品目标），不是单纯的代码清理。

## 2. 已确认的设计决策

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 本次覆盖工具 | 仅 `terminal`、`read_file`、`write_file`、`edit_file` |
| 2 | terminal 规则 | 对 command 做正则匹配，命中则按规则处理（allow / ask / deny） |
| 3 | 文件工具规则 | 只表达工作目录外是否放行（allow / ask / deny），目录内始终允许 |
| 4 | 规则优先级 | 同工具多条命中时，新引入的规则生效（文件内靠后的覆盖靠前的；用户规则压过默认层） |
| 5 | 地板 | 真正危险的指令始终拒绝：硬编码、不可配置、不可被规则或扩展覆盖、不弹询问 |
| 6 | 统一接口 | `before_tool` 上下文新增决定语义（allow / ask / deny），不再只有 block |
| 7 | 冲突处理 | 用户引入的表态不唯一时，**不猜**：弹确认卡由运行时用户当场决定 |
| 8 | 扩展 vs 默认层 | 用户显式表态直接压过默认层（扩展 allow + 默认 ask → 直接放行），不算冲突 |
| 9 | 规则文件 | 放在 `HARNESS_CONFIG_DIR`，JSON 格式，内容参考 `examples/permission_config_example.json` |
| 10 | 受保护路径 | 本期不做：`.harness/.permissions.json` 与 `.harness/extensions/` 不设写保护，由用户负责人工审阅；风险写入权限文档 |

第 7 条的原理：由用户引入的不确定性问题（规则与扩展、扩展与扩展互相矛盾）难以靠优先级逻辑解决，责任交还给引入者，运行时以询问收口。

## 3. 判定模型

每次工具调用由一个**内置权限扩展**在 `before_tool` 节点做最终裁决。裁决输入分三层：

1. **地板（floor）**：仅 terminal。现有 `DENY_LIST` 原样迁移（含词边界、大小写不敏感匹配）。命中即拒绝，最终结果。
2. **用户表态（votes）**：
   - 配置规则：terminal 命令正则 / 文件工具 external_directory 策略，每层最多一票（规则层内部已按「新引入生效」收敛为单票）；
   - 代码扩展：每个 callback 最多一票（同一 callback 多次 `decide` 以最后一次为准）。
3. **默认层（defaults）**：现有 `DESTRUCTIVE` 列表、terminal 命令内越界路径扫描、文件工具越界确认。仅在没有用户表态时生效。

裁决伪代码：

```
if floor matches (terminal only):
    return deny(floor_message)                    # 最终，不询问

votes = rule_vote(可有可无) + extension_votes     # 用户引入
if votes:
    if actions 全部相同: return votes[0]
    return ask_conflict(votes)                    # 弹卡；yes → allow，no → deny

return default_vote(tool, args)                   # 破坏性/越界 → ask；否则 allow
```

场景对照：

| 场景 | 结果 |
|---|---|
| 地板命中（无论是否存在 allow 规则/扩展） | 拒绝 |
| 只有规则命中 / 只有扩展表态 | 采用该表态 |
| 规则与扩展同动作 | 采用该动作 |
| 规则与扩展动作不同 | 弹卡询问 |
| 扩展与扩展动作不同 | 弹卡询问 |
| 有用户表态且无冲突，默认层持不同动作 | 采用用户表态（默认层不参与） |
| 无任何用户表态 | 默认层：破坏性命令 / 越界 → 询问；否则允许 |

补充语义：

- **表态时刻与一致性**：每个 callback 结束时，为其票保存当时的参数快照；派发结束后，快照与**最终参数**不一致的票作废（记日志）并视为弃权——拒绝「先投票、后被别人改写参数」的静默放行路径。正常「自己改写 + 自己投票」的组合不受影响（快照取自该 callback 结束时）。地板与规则按最终参数判定，权限门固定最后执行（见 §5 顺序）。
- **冲突卡内容**：列出各方（规则 pattern / 扩展模块名及其 reason）与命令/路径，供用户判断。
- **冲突不是错误**：是正常结果，记日志即可，不抛异常。

## 4. 规则文件

### 4.1 位置与生命周期

- 路径：`.harness/.permissions.json`（`HARNESS_CONFIG_DIR` 下，沿用 dot 前缀约定）。
- 启动时由 `bootstrap()` 创建默认 `{}`（加入现有 `_JSON_FILES`），启动时读取一次；**修改后需重启进程**（与外部扩展一致，本期不做热加载）。
- 文件不存在 → 视为空规则（仅地板 + 默认层），不报错。

### 4.2 格式与语义

```json
{
  "terminal": {
    "command": {
      "<正则>": "allow | ask | deny"
    }
  },
  "read_file":    { "external_directory": "allow | ask | deny" },
  "write_file":   { "external_directory": "allow | ask | deny" },
  "edit_file":    { "external_directory": "allow | ask | deny" }
}
```

- `terminal.command`：键为正则字符串；匹配方式 `re.search`，编译带 `IGNORECASE`（与现有权限门一致；可用作用域形式 `(?-i:...)` 局部关闭大小写不敏感，如 `(?-i:FOO)` 只匹配大写的 `FOO`）。按 JSON 文件顺序逐条匹配，**最后一个命中的键生效**（对应「新引入的规则生效」）。
- 文件工具 `external_directory`：仅在解析后的路径位于 `WORKDIR` 之外时生效；目录内调用与规则无关，直接允许。
- 未知顶层工具键：忽略并记 WARN（为未来工具留扩展空间）。
- 非法 `action` 值、非法正则：丢弃该条规则并记 ERROR，其余规则照常生效。
- JSON 整体解析失败：整个文件不生效（退化为默认层）并记 ERROR。

## 5. 统一决定接口（core/extension 改动）

### 5.1 BeforeToolContext 新增决定语义

- 新增 `decide(action, reason="")`：登记一票，`action ∈ {"allow","ask","deny"}`；非法值抛 `ValueError`（由派发器 fail-open 捕获，该 callback 的票丢弃并记 ERROR）。
- 同一 callback 多次 `decide`：以最后一次为准，避免同一来源重复计票。
- `block(reason)` 保留，语义迁移为 `decide("deny", reason)` 的等价写法——**从「硬拦截」变为「deny 表决」**：与 allow 规则冲突时改为询问用户，而不是直接拦死。旧扩展代码零改动。
- 派发器自动为每票打上来源（`nano_extension_<name>` / `builtin:permission`），用于冲突卡与日志。

### 5.2 派发语义变化

- 决定票**不短路**：派发器不再因 `blocked` 提前结束（`aborted` / `handled` 的短路语义不变）。最终裁决由权限门写入 `blocked`，此后剩余回调不再执行。
- 回调顺序：`api.on(event, callback)` 签名不变，**不向扩展暴露 priority 或阶段**。派发器在注册项上维护内部阶段（`user` < `observer` < `gate`），同阶段内按注册顺序稳定排序：
  - `user`：普通回调（外部扩展与测试直接注册的回调），在 `before_tool` 上负责观察、改写参数与投票；
  - `observer`：内置观察者（调用日志、大输出告警）；`before_tool` 上它看到外部改写后的最终参数，且先于权限门执行——被拒绝的调用同样记日志；
  - `gate`：内置权限门，固定最后执行，保证看到最终参数并收齐所有票。外部扩展没有任何公开注册点可以排到 gate 之后。
- 失败语义按注册项元数据区分，不靠名字猜测：
  - `dispatcher.add(event, callback, module_name, *, stage="user", fail_closed=False)` 为内部接口；loader 提交外部扩展用默认值，内置注册使用 `stage="observer" / "gate"`，权限门 `fail_closed=True`。`ExtensionAPI` 对外签名不变。
  - 外部扩展（含测试直接调用 `dispatcher.add`）：fail-open，异常 = 弃权，记日志并继续；
  - 内置权限门：fail-closed，异常 → 拒绝并记 ERROR，避免策略代码崩溃导致放行。

### 5.3 询问入口

- 只有权限门调用 `ask_permission`（`core/interaction.py` 端口）。扩展通过 `decide("ask")` 表达「该问」，不直接调 UI。
- 默认层与冲突裁决共用同一张确认卡；用户答 yes → allow，其余 → deny。

## 6. 内置权限扩展与旧代码迁移

### 6.1 内置扩展形态

- 新目录 `core/extension/builtin/`：
  - `permission.py`：地板列表（迁移自 hook.py）、规则文件加载与校验、判定算法、权限门回调；
  - `observers.py`：`tool_call_log_hook` → `before_tool` 观察者；`large_tool_output_hook` → `after_tool` 观察者（该钩子当前从未被触发，迁移后真正生效）；
  - `__init__.py`：`register_builtin_extensions()`，用与外部扩展相同的 `ExtensionAPI` + `register(api)` 形态注册。
- 由 `bootstrap()` 显式调用注册，不走 `.harness/extensions/` 扫描：该目录被 gitignore、受 `enabled` 控制，权限门必须无条件在场，不能是用户可关的开关。
- 删除 `core/hook/` 整个包（`HOOKS`、`register_hook`、`trigger_hooks`、`DEFAULT_HOOKS`、`install_default_hooks`），以及两个从未触发的旧事件 `pre_llm_call`、`post_tool_call`。
- `examples/s08_hook_system.py` 与根目录 `.hooks.json` 是独立的教学示例，不 import `core.hook`，保持原状。

### 6.2 bootstrap 与主流程接线

- `bootstrap()`：`register_builtin_extensions()` 取代 `install_default_hooks()`，其余顺序不变（内置注册 → 外部加载 → 技能 / 模型 / cron）。
- `core/agent_step.py`：删除 `trigger_hooks` import 与 `policy.permission_hooks` 分支；`before_tool` 的 dispatch 始终执行（权限门在内置扩展里）。
- `StepPolicy.permission_hooks` 字段删除；`use_extensions` 语义收窄为「外部扩展是否参与」：`False` 时派发器跳过所有非内置注册项，**所有事件一律跳过**（`before_llm` / `after_llm` / `tool_execute` 当次不派发）；内置观察者与权限门照常执行——权限门、地板、规则仍然生效。

### 6.3 结果文案（模型可见）

| 来源 | 工具结果 |
|---|---|
| 地板 | `Permission denied: '<pattern>' is on the deny list.`（不变） |
| 用户拒绝（含冲突询问被拒） | `Permission denied by user`（不变） |
| 规则裁定拒绝 | `Permission denied by rule: <pattern>` |
| 扩展裁定拒绝 | `[Extension blocked] <reason>`（兼容旧 block 文案） |

## 7. 行为变化清单

1. 被权限拒绝的工具调用现在也会进入调用日志（日志观察者先于权限门执行），审计更完整。
2. `large_tool_output_hook` 从死代码变为真正生效（`after_tool`）。
3. `block()` 从硬拦截变为 deny 表决：与 allow 规则同时命中时改为询问。
4. `use_extensions=False` 不再关闭权限门。
5. 规则裁定拒绝有了专属文案；扩展拒绝文案保持兼容。
6. dispatcher 对内置与外部回调采用不同的失败语义。

## 8. 非目标与反目标

**非目标**（本期不做）：

- MCP 工具（`mcp_*`）权限策略，保留原 TODO；
- 规则热加载、TUI 内管理规则、全局/项目规则继承与作用域；
- 把 allow/deny 冲突用优先级逻辑解决；
- 主/子代理权限策略分化（共用同一门与同一规则）；
- 会话内「记住本次允许」；
- `.hooks.json` / s08 教学示例的改造；
- 受保护路径 / 配置防篡改：`.harness/.permissions.json` 与 `.harness/extensions/` 在工作目录内，agent 可以写入；本期不设写保护，风险与责任写入权限文档（用户已确认）。

**反目标**（出现即设计失败）：

- 任何可以关掉权限门、或绕过地板的配置/开关/扩展点；
- 冲突时静默选边；
- 规则解析错误被静默吞掉（必须记 ERROR）。

## 9. 测试策略

沿用 `tests/` 现有 stub 模型与工具模式，新增 / 改写：

- **规则加载**：合法文件生效；JSON 非法 → 整体忽略 + ERROR；单条 action / 正则非法 → 只丢该条；未知工具键忽略 + WARN；文件缺失 → 空规则。
- **规则语义**：terminal 后写的命中键覆盖先写的；工作目录内的文件工具调用不受规则影响；文件工具越界 allow/ask/deny 三态。
- **地板**：deny_list 命中时，即使配置了 allow 规则、扩展投了 allow，仍然拒绝。
- **扩展 vs 默认层**：扩展 allow 覆盖默认 ask（不弹卡）；扩展 deny 覆盖默认 allow。
- **冲突**：规则 allow + 扩展 deny → 弹卡；扩展 A allow + 扩展 B deny → 弹卡；用户 yes → 执行，no → `Permission denied by user`；无冲突时不弹卡。
- **失败语义**：权限门抛异常 → 拒绝（fail-closed）；外部回调抛异常 → 弃权且不影响其余回调（回归）。
- **顺序**：权限门看到外部 `before_tool` 改写后的参数（改写后命中规则/地板）；调用日志观察者看到最终参数且先于裁决；外部扩展无法注册到 gate 阶段（结构性断言）；旧测试 `test_extension_loop_wiring` 的顺序断言据此更新。
- **票一致性**：先投票、后被其他回调改写参数的票作废；同一 callback 自行改写后投票仍然有效。
- **接线**：`use_extensions=False` 时地板与规则仍生效、外部回调在所有事件上被跳过、内置观察者照常；`StepPolicy.permission_hooks` 移除后 `test_agent_step` / `test_bootstrap` / `conftest` 相应更新。
- **观察者**：`after_tool` 大输出告警会触发（原死代码）。
- **回归**：`uv run pytest tests/` 全绿；`test_hook_permission.py` 迁移为权限门测试或删除。

## 10. 文档更新

- `docs/permissions.md` 重写：三层判定模型、规则文件格式与语义、冲突询问、失败语义；并写明已知风险——`.harness/.permissions.json` 与 `.harness/extensions/` 位于工作目录内、agent 可写，修改重启后生效，应由用户人工审阅（本期不设写保护）。
- `docs/extensions.md` 更新：决定语义（allow/ask/deny）、priority、内置/外部失败语义差异；删除「旧 hook 与新扩展相互独立」的表述。
- `README.md` 权限/扩展一节同步。
- `docs/superpowers/specs/2026-10-08-extension-hooks-design.md` 标注「与旧 hook 的关系」一节由本设计取代。
- `examples/permission_config_example.json` 保留为文档示例。

## 11. 我的决定（审阅时重点看）

1. 文件名 `.harness/.permissions.json`，bootstrap 创建默认 `{}`。
2. 正则 `re.search` + `IGNORECASE`（与现有门一致）；规则按 JSON 键顺序，最后一个命中生效。
3. 字段级错误只丢单条；JSON 整体错误丢整个文件；两者都记 ERROR。
4. 启动加载、重启生效，与外部扩展一致。
5. `block()` 迁移为 deny 表决（语义变化，见 §7.3）。
6. 权限门固定最后执行：不暴露 priority，用内部阶段 `user → observer → gate` 结构性保证；内置 fail-closed，外部维持 fail-open。
7. `use_extensions=False` 只跳过外部回调（所有事件），内置观察者与权限门照常。
8. 新文案 `Permission denied by rule: <pattern>`；扩展拒绝保持 `[Extension blocked] <reason>`。
9. 冲突卡展示各方原因与最终参数（命令/路径）。
10. 规则文件未知工具键忽略 + WARN。
11. 地板沿用现有 `DENY_LIST` 与词边界/大小写匹配实现；默认层沿用 `DESTRUCTIVE`、terminal 越界扫描与文件工具越界确认。
12. 票与参数一致性：callback 结束时的参数快照与最终参数不一致的票作废（弃权 + 日志）；冲突卡展示最终参数。
13. headless（无 UI）时冲突询问按 ask → no 收口，文案沿用 `Permission denied by user`。
14. `decide` / `block` 对全部工具生效（不限于 §2#1 的四类）；规则配置只处理这四类工具。

## 12. 验收标准

- `uv run pytest tests/` 全绿；§9 列出的新场景均有测试覆盖。
- 地板不可被任何规则/扩展覆盖；权限门在 `use_extensions=False` 与门内异常两种情况下都保持拒绝。
- 配置层：allow 规则放行默认层会询问的命令；deny 规则拦截默认层会放行的命令；文件工具越界三态可配。
- 冲突（规则 vs 扩展、扩展 vs 扩展）始终走确认卡，无静默选边路径。
- 外部扩展没有任何注册点可以排到权限门之后；参数改写后被作废的票不参与裁决。
