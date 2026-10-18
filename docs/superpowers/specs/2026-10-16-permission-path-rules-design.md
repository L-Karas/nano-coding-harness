# 权限规则扩展：terminal 外部目录开关与文件工具 path 正则（nano-harness）

- 日期：2026-10-16
- 状态：待评审
- 前置设计：[`2026-10-13-permission-policy-design.md`](2026-10-13-permission-policy-design.md)（三层判定模型与统一决定接口；本设计只扩展其规则层）
- 关联代码：`core/extension/builtin/permission.py`、`tests/test_permission_rules.py`、`tests/test_permission_gate.py`
- 关联文档：`docs/permissions.md`、`docs/configuration.md`、`docs/extensions.md`、`examples/permission_config_example.json`

> 后续变更（2026-10-19）：`external_directory` 的票原因与拒绝文案已统一为「标题 + 触发数据」，
> 越界清单最多展示 3 条；默认层新增 `approval_mode`（`Manual` / `Yolo`）。本文件 §4.3、§4.4、§5、
> §7、§8、§10、§11 中与这些点冲突的描述，以 `docs/permissions.md` 与 `docs/configuration.md` 为准；
> 其余设计仍有效。

## 1. 意图

当前规则文件（`.harness/.permissions.json`）的能力有两个缺口：

1. **terminal 的外部文件访问不可配置**：命令引用调用方工作目录之外的路径时，只能走默认层弹确认卡，
   没有 `allow` / `ask` / `deny` 三态开关（文件工具已有，见 2026-10-13 设计 #3）。
2. **文件工具没有按路径的细粒度规则**：`read_file` / `write_file` / `edit_file` 只有
   `external_directory` 一个开关，无法表达「拒绝读 `.env`」「允许写工作区外的 `../shared/`」
   这类针对特定路径 / 文件的特殊处理。

同时对「多条规则命中」的收敛语义做一次明确：从 terminal 命令规则的「最后命中生效」改为
**deny > ask > allow 取最严**，让规则文件可以安全地叠加（后加的 allow 不会意外压掉早先的 deny）。

本设计不改变地板、扩展决定票、冲突弹卡与默认层的基本结构。

## 2. 已确认的设计决策

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 「外部」的基准 | 调用方工作目录（`ctx.cwd`，缺省 `WORKDIR`），与现有越界判定一致 |
| 2 | terminal 新增字段 | `external_directory`，action 三态，作用在命令引用了工作目录外路径时 |
| 3 | 文件工具新增字段 | `path`：正则 → action 的映射表，三个文件工具各一份 |
| 4 | path 匹配目标 | **相对工作区的路径**（解析参数路径后换算，见 §4.2） |
| 5 | 多条规则命中 | **deny > ask > allow 取最严**；替换 terminal 命令规则现有的「最后命中生效」，文件工具同规则 |
| 6 | 显式 allow 与 deny 的关系 | 同时命中时 deny 胜出：一条 allow 的 path 规则**不能**压过 `external_directory: deny`（用户已确认） |
| 7 | 规则层形态 | 规则层内部收敛为**至多一票**参与现有投票链；与扩展票的冲突弹卡语义不变 |
| 8 | 规则文件 | 沿用 `.harness/.permissions.json`，启动加载、重启生效；格式参考 `examples/permission_config_example.json`（已按本设计更新） |
| 9 | `external_directory: allow` 的作用范围 | 作为规则票**整层跳过默认层**（含 destructive 询问；用户已确认 A 方案，见 §5） |

## 3. 规则文件格式

```json
{
  "terminal": {
    "external_directory": "allow",
    "command": {
      "<正则>": "allow | ask | deny"
    }
  },
  "read_file": {
    "external_directory": "ask",
    "path": {
      "<相对路径正则>": "allow | ask | deny"
    }
  },
  "write_file": {
    "external_directory": "allow",
    "path": { "...": "..." }
  },
  "edit_file": {
    "external_directory": "deny",
    "path": { "...": "..." }
  }
}
```

- `terminal` 段现在接受 `command` 与 `external_directory` 两个键；文件工具段接受
  `external_directory` 与 `path` 两个键。段内未知键 → 忽略并记 WARN（现有行为）。
- `external_directory` 与 `path` 表里的 action 取值均为 `allow` / `ask` / `deny`；
  非法 action 丢单条并记 ERROR。
- `path` 必须是对象；键为正则字符串，编译带 `IGNORECASE`，对**相对路径**做 `re.search`
  （与命令规则一致：命中即算，不要求整串匹配；用 `^` / `$` 自行锚定）。
- `path` 规则同时覆盖工作目录内与目录外的路径：工作目录外的相对形式带 `../` 前缀
  （Windows 跨盘符见 §4.2 的退化规则）。
- 解析失败的粒度沿用现状：单条规则非法只丢该条（ERROR）；整个文件 JSON 非法或顶层不是对象
  → 全部规则不生效（ERROR）。
- 一个已知段（terminal / 三个文件工具）如果没有产出任何有效规则（缺键、空对象、条目全非法）
  → 记 WARN。

## 4. 匹配与收敛

### 4.1 规则层数据模型

```python
@dataclass
class Rules:
    terminal: list[tuple[re.Pattern, str]]              # terminal.command，文件顺序
    paths: dict[str, list[tuple[re.Pattern, str]]]      # 文件工具 -> path 规则，文件顺序
    external_directory: dict[str, str]                  # 四个工具共用，键含 "terminal"
```

查询函数（模块内公开，测试直接使用）：

- `match_command(rules, command) -> list[tuple[str, str]]`：命令规则的全部命中，
  `(action, 正则原文)`，按文件顺序。**替换**现有 `match_terminal`（不再在匹配函数里收敛）。
- `match_path(rules, tool_name, relative) -> list[tuple[str, str]]`：path 规则的全部命中，同上。
- `resolve_matches(matches) -> tuple[str, str] | None`：按 `deny > ask > allow` 取最严；
  同 severity 多条命中时取**列表中的最后一条**（顺序即文件顺序，external_directory 追加在最后）；
  无命中返回 None。
- `external_directory_action(rules, tool_name)`：不变，现在也能查 `"terminal"`。

### 4.2 path 的匹配目标

对文件工具的一次调用，匹配串按下述步骤得到：

1. `base = (ctx.cwd or WORKDIR).resolve()`；`resolved = (base / args["path"]).resolve()`
   （与文件工具自身的路径解析口径一致，符号链接会被还原）。
2. `relative = os.path.relpath(resolved, base)`，再用 POSIX 形式归一（Windows 上
   `..\foo` → `../foo`）：`Path(relative).as_posix()`。
3. 匹配串 = `relative`；正则用 `re.search` + `IGNORECASE`。

边界情况：

- 参数是工作目录内的绝对路径 / 带 `./` 前缀 → 换算回规范相对形式（`src/a.py`）。
- 参数带 `..` → 先 resolve 再换算（`../secret.txt` → `../secret.txt`）。
- 参数解析结果就是工作目录本身 → 匹配串为 `.`。
- **Windows 跨盘符**：`os.path.relpath` 抛 `ValueError`（没有相对形式），退化为匹配
  `resolved.as_posix()` 的绝对路径（如 `d:/secrets/x`）。相对形式的规则不会命中这类路径，
  绝对形式的规则可以；`external_directory` 仍按越界生效。
- 大小写不敏感：与命令规则一致，Windows 路径天然不敏感，POSIX 上行为一致（用户规则自己负责）。

### 4.3 单次调用的规则收敛

`_rule_vote(rules, ctx)` 只产出一票：

- **terminal**：先取 `match_command` 的全部命中；若命令引用了工作目录外路径
  （复用现有 `_outside_paths` 扫描）且配了 `terminal.external_directory`，
  追加一条 `(action, "terminal.external_directory")`；最后 `resolve_matches`。
- **文件工具**：先算匹配串（§4.2），取 `match_path` 的全部命中；若 resolved 路径在工作目录外
  且配了该工具的 `external_directory`，追加 `(action, "<tool>.external_directory")`；
  最后 `resolve_matches`。
- 其余工具：None（不变）。

投票链不变：规则票（至多一票）+ 扩展票；动作一致采用，不一致弹确认卡（现有语义，
本次**不**把 severity 扩展到扩展票）。两者都没有 → 默认层。

### 4.4 原因文案

- `command` / `path` 规则的票原因 = 命中的正则原文（与现状一致）。
- `external_directory` 的票原因 = `<tool>.external_directory`（与现状一致）。
- 同 severity 多条命中时取最后一条展示；因此「命令 deny 规则 + terminal.external_directory deny」
  同时命中时展示 `terminal.external_directory`。

## 5. 默认层与交互后果

默认层（destructive 命令 / 越界询问）代码不变，仍只在**没有任何用户表态**时生效。
由此产生一个需要写进文档的后果：

- `terminal.external_directory: "allow"` 是一张**规则票**：该次调用命中它后默认层整体不参与，
  包括 destructive 询问。这与现有「显式规则压过默认层」的语义一致（`test_rule_allow_overrides_destructive_default`）。
  用户若仍想对破坏性命令确认，可加命令规则（如 `"^rm ": "ask"`），severity 保证 ask 压过 allow。
- `external_directory: deny` 与扩展 allow 票冲突时仍走确认卡（不升级为地板）。

## 6. 行为变化清单

1. terminal 命令规则的收敛从「最后命中生效」变为「deny > ask > allow 取最严」。
   例：`{"force": "allow", "^git": "deny"}` 命中 `git push --force` 现在是 deny（之前 allow）。
2. `terminal.external_directory` 新配置：三态可选。未配置时行为不变（默认层询问）。
3. 文件工具 `path` 规则新配置：可拦工作目录内路径（如 `\.env$": "deny"`），
   可不依赖 `external_directory` 单独表达目录外路径的 allow/ask/deny。
4. `external_directory: allow` + `path` ask/deny 同时命中时，后者胜出（severity）。
5. 规则层内部不再可能产出互相冲突的条目；规则 vs 扩展、扩展 vs 扩展的弹卡语义不变。
6. `permission.py` 里「多条规则命中，是否应只保留最后命中的规则」todo 由本设计消除。

## 7. 非目标与反目标

**非目标**（本期不做）：

- 扩展决定票之间的 severity 收敛（继续弹冲突卡；冲突即交还用户）；
- terminal 命令的按路径规则（terminal 只有 command 与 external_directory 两类规则）；
- `glob` / `grep` 等其他工具的 path 规则；
- glob 语法、规则热加载、TUI 内管理规则、会话内「记住本次允许」；
- 地板改动、`.harness/` 写保护（沿用 2026-10-13 设计的已知风险）；
- 规则文件格式的迁移/版本化（新字段都是可选的，旧文件照常解析）。

**反目标**（出现即设计失败）：

- 任何 allow 能压过同次命中的 deny；
- 规则解析错误被静默吞掉（必须记 ERROR）；
- 本次改动改变地板、扩展票冲突弹卡或默认层文案。

## 8. 测试策略

在 `tests/test_permission_rules.py` / `tests/test_permission_gate.py` 上扩展：

- **解析**：terminal 只配 external_directory 不报「未产出」；文件工具只配 path 不报；
  `path` 非对象 / 条目 action 非法 / 正则非法 → 丢单条 + ERROR；未知段内键 → WARN；
  段产出为零 → WARN。
- **匹配与收敛**：`match_command` / `match_path` 返回全部命中（文件顺序）；
  `resolve_matches` 覆盖 allow+deny→deny、allow+ask→ask、ask+deny→deny、三条同现、
  同 severity 取最后一条、无命中→None。
- **path 换算**：`./src/a.py`、绝对路径、`../x`、`..` 越界、工作目录本身、符号链接
  （沿用 `_link_dir` 模式）、Windows 跨盘符退化（`skipif`）；大小写不敏感。
- **权限门**：path deny 拦目录内路径；path allow 放行目录外路径（不弹卡）；
  `external_directory: deny` + path allow → deny；`external_directory: allow` + path deny → deny；
  terminal external allow 跳过越界询问；terminal external deny 拒绝（文案
  `Permission denied by rule: terminal.external_directory`）；terminal external ask 弹卡；
  command ask + external allow → ask；未配置 external 时越界默认询问（回归）。
- **回归**：地板、扩展票冲突弹卡、`use_extensions=False`、自定义 cwd（子代理）等既有测试不改语义。
- **验收**：`uv run pytest tests/` 全绿。

## 9. 文档更新

- `docs/permissions.md`：规则文件格式（新字段）、path 匹配目标与归一化、severity 收敛、
  场景表与「已知后果」（external allow 跳过默认层）；把「后者覆盖前者」的表述全部替换。
- `docs/extensions.md` 4.4 节：裁决顺序描述里补一句「规则层内部按 deny > ask > allow 收敛为至多一票」。
- `docs/configuration.md` 第 28 行：规则文件说明补上 path 正则与 terminal 外部目录开关。
- `examples/permission_config_example.json`：保持已更新的形状（补文件末尾换行）。
- 本设计文档作为 `docs/permissions.md` 的参考链接。

## 10. 我的决定（审阅时重点看）

1. severity 只在规则层内生效；扩展票保持冲突弹卡，不参与 severity。
2. 同 severity 多条命中取最后一条展示；external_directory 追加在直接规则之后。
3. path 匹配串 = resolve 后的工作目录相对 POSIX 路径；跨盘符退化为绝对 POSIX 路径。
4. path 正则 `re.search` + `IGNORECASE`，与命令规则一致。
5. `external_directory: allow` 作为规则票会跳过该次调用的整个默认层（含 destructive），
   与既有「显式规则压过默认层」一致；需要时用命令 `ask` 规则补回确认。**（用户已确认取此方案 A，
   不采用「只跳过越界询问」方案 B；`build_gate` / `_apply_defaults` 边界维持现状。）**
6. path 规则同时覆盖工作目录内与目录外路径（这是「对特定路径特殊处理」的本体）。
7. path 规则的票原因用正则原文；external_directory 用 `<tool>.external_directory`。
8. `external_directory` 字典改为四个工具共用（含 `"terminal"`），不再单独开字段。
9. 内部函数 `match_terminal` 拆分为 `match_command` + `resolve_matches`（测试随改）。
10. 段内产出为零记 WARN 的口径统一到「该段所有可能来源合计为零」。

## 11. 验收标准

- `uv run pytest tests/` 全绿；§8 列出的新场景均有测试覆盖。
- 三态配置可工作：无规则时默认层行为与今天完全一致；配 allow / ask / deny 后按配置收口。
- 任何一次规则层内部冲突都按 deny > ask > allow 收口，不存在 allow 压 deny 的路径。
- 地板、扩展冲突弹卡、默认层文案与触发条件零变化。
