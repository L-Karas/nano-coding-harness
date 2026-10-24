# /extensions 扩展列表弹窗设计（nano-harness）

- 日期：2026-10-24
- 状态：已批准（2026-10-24）
- 关联代码：`core/extension/registry.py`（新增）、`core/extension/loader.py`、
  `core/extension/builtin/__init__.py`、`core/extension/__init__.py`、
  `core/tui/screens/extensions.py`（新增）、`core/tui/screens/__init__.py`、
  `core/tui/commands.py`、`core/tui/utils.py`
- 关联文档：`README.md`、`docs/extensions.md`、`docs/tui.md`

## 1. 意图

TUI 目前只能通过日志和扩展目录了解扩展状态：哪些装了、哪些启用、各自挂了哪些 hook。
新增 `/extensions` 指令弹出扩展窗口，复用 `/skills` 的列表样式（搜索栏、`● 名称 [tag]`
+ 第二行说明、`Esc` 关闭），并给每条扩展标注来源：`user`（`.harness/extensions/` 下的外部
扩展）或 `builtin`（bootstrap 注册的内置权限门 / 观察者）。

已启用与未启用的扩展都要展示，未启用项要说明原因（`enabled` 非 true、缺清单、清单非法、
加载失败）。

## 2. 目标 / 非目标

**目标**

- `/extensions` 打开只读弹窗，列出当前进程启动时扫描到的全部扩展与两个内置扩展。
- 每条显示名称、来源 tag、以及 hook 事件（已启用）或未启用原因。
- 搜索、键盘浏览、`Esc` 关闭与 `/skills` 一致；`Enter` / 单击不触发动作。
- 列表顺序稳定：`builtin` 在前，`user` 按名称排序。

**非目标**

- 不提供启用 / 禁用 / 重载按钮；扩展仍只在启动时加载一次。
- 不展示扩展的完整 manifest、文件路径或回调函数名。
- 不把扩展列表放进右栏信息面板。
- 不改变扩展加载、注册、派发的任何语义。

**反目标**

- 窗口显示的启用状态与运行期实际注册状态不一致。
- 为展示未启用项而在 TUI 层重新解析 `extension.json`、复制加载器规则。

## 3. 方案与取舍

| 方案 | 做法 | 结论 |
|---|---|---|
| A. 加载器登记表 | 加载器扫描时登记每条扩展（含未启用原因）；内置注册时登记；弹窗只读这张表 | **采用**：状态与运行期一致，未启用项可见，无重复解析 |
| B. 打开窗口时现扫磁盘 | 每次打开重新读 `.harness/extensions` + 从 dispatcher 推断 builtin | 否：未重启时与真实运行期不一致，且重复清单解析逻辑 |
| C. 只从 dispatcher 回调推断 | 由 `_HANDLERS` 的 module_name 反推 | 否：未启用扩展没有任何回调，会直接消失 |

A 的代价是新增一个进程级 registry 和对应的 reset；相比 B/C 的语义缺口可以接受。

## 4. 设计

### 4.1 `core/extension/registry.py`

```python
Origin = Literal["user", "builtin"]
Event = tuple[str, ...]

@dataclass(frozen=True)
class ExtensionInfo:
    name: str              # user = 文件夹名；builtin = 去掉 "builtin:" 前缀的模块名
    origin: Origin
    enabled: bool
    detail: str = ""       # 未启用原因；已启用时为空
    events: tuple[str, ...] = ()  # 已注册的事件名（注册顺序去重）
```

- `record(...)` 以 `(origin, name)` 为键插入或替换；`list_extensions()` 返回排序后的快照
  （key：`origin != "builtin"`，再按 `name.casefold()`）。同名同源的重复登记原地替换，
  不同来源的同名扩展各自保留。
- `reset()` 清空，供测试隔离；由 `core.extension.reset_extensions()` 调用。
- `core/extension/__init__.py` 导出 `ExtensionInfo`、`list_extensions`。

### 4.2 加载器登记（`user`）

`load_extensions()` 扫描到直接子目录（仍跳过 `_` 前缀与非目录）后，无论是否加载都登记：

| 扫描结果 | enabled | detail |
|---|---|---|
| 加载成功 | True | 空；`events` = 本扩展实际提交的事件 |
| `enabled` 非严格 true | False | `disabled` |
| 缺 `extension.json` | False | `missing extension.json` |
| `extension.json` 解析失败 / 非对象 | False | `invalid extension.json` |
| 缺可调用 `register(api)` | False | `missing register(api)` |
| 导入 / 注册抛错 | False | `load failed` |

实现上把 `_load_extension` 改为返回本次提交的 `(event, ...)` 列表、异常上抛；由
`load_extensions` 统一 `try/except` 记日志并登记 `load failed`。原子性不变：
只有 `register(api)` 正常返回后才提交注册并登记 `enabled=True`。

### 4.3 内置登记（`builtin`）

`core/extension/builtin/__init__.py` 的 `_register(module_name, register)` 在提交回调后登记：
名称取 `module_name.removeprefix("builtin:")`（即 `observers`、`permission`），
`origin="builtin"`、`enabled=True`、`events` = 本次提交的事件。
内置注册失败按现有行为直接抛出，不登记。

### 4.4 弹窗（`core/tui/screens/extensions.py`）

```python
class ExtensionsScreen(_ListPickerScreen):
    TITLE = "Extensions"
    HINT = "  ↑/↓ browse    Esc close"
    LIST_ID = "extensions-list"
    SEARCH_PLACEHOLDER = "Search extensions…"
```

- 构造参数 `list[ExtensionInfo]`；`_reload()` 复用 `_entry_row(name, description, origin)`
  与 `_rebuild_options`，按搜索词过滤名称（大小写不敏感，沿用基类 `_match`）。
- 第二行：
  - 已启用且有事件：`hooks: before_tool, after_tool`（注册顺序去重）；
  - 已启用但无事件：`no hooks`；
  - 未启用：`detail`（如上表；缺省 `disabled`）。
- 不继承 `_NamedListScreen` 的「Enter 回传名称」；不定义 OptionSelected 处理，
  `Enter` / 单击保持无动作。

### 4.5 指令接线

- `core/tui/utils.py` 的 `_SLASH_COMMAND_META` 在 `/skills` 后新增：
  `("/extensions", (), "List loaded and disabled extensions")`；候选补全自动生效。
- `core/tui/commands.py`：`_OPENERS["/extensions"] = "_open_extensions"`；
  `_open_extensions()` 取 `list_extensions()`，为空时用
  `render_background_notification("No extensions found", title="⚠️ Extensions")`，
  否则 `push_screen(ExtensionsScreen(rows))`。不做忙时拒绝（只读、无副作用）。
- `core/tui/screens/__init__.py` 导出 `ExtensionsScreen`。

### 4.6 文档与冒烟

- `README.md` 内置指令列表加 `/extensions` 一句说明。
- `docs/extensions.md` 加一小节：TUI `/extensions` 展示已启用 / 未启用扩展与来源。
- `smoke/extensions.py`：注入 / 使用现有 registry 验证 builtin 与 user 行、未启用原因、
  搜索过滤、`Esc` 关闭、`Enter` 不关窗；挂进 `smoke/__init__.py` 的 `_SECTIONS`。

## 5. 测试计划

- `tests/test_extension_registry.py`：排序（builtin 先、user 名称序）、reset、events 去重。
- `tests/test_extension_loader.py` 增补：成功项登记事件；disabled / 缺清单 / 非法清单 /
  缺 register / 注册失败分别登记对应 detail；`_` 前缀不登记；重复调用不重复登记。
- `tests/test_extension_builtin.py` 增补：两个 builtin 条目、origin / enabled / events。
- `tests/test_ui_extensions.py`：行文本与两段样式、搜索过滤、`Esc` 关闭、`Enter` 不关窗。
- `python -m core.tui.ui_textual --smoke` 全量通过。

## 6. 留给实现者的判断

- detail 文案按 §4.2 表执行；若加载失败前已能区分「缺 register」与一般异常，按表使用更精确
  的文案，否则统一 `load failed`。
- 现有测试若直接调用 `loader.reset()`，需确认 registry 也经由 `reset_extensions()` 清理；
  不改变现有测试的隔离方式。
- `hooks:` 行是否按事件名排序：默认按注册顺序（与派发顺序一致），不额外排序。
