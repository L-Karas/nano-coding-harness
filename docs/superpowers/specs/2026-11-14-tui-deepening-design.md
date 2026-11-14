# core/tui 深挖设计：包级 Interface 与五个 Module

- 日期：2026-11-14
- 状态：已完成（2026-11-14）
- 关联代码：`core/tui/`（`ui_textual.py`、`surface.py`、`commands.py`、`interactions.py`、
  `render.py`、`footer.py`、`info_panel.py`、`cards.py`、`widgets.py`、`screens/`）
- 关联文档：`docs/tui.md`、`docs/adr/0005-ui-port.md`、`GLOSSARY.md`

## 1. 意图

`core/tui/` 的外层 Seam（`core.interaction` 端口 → `core/tui/render.py`）方向正确，
但端口 Adapter 被切成两半，App 内部是「一个 Module 用三个 mixin、三个文件写」。
结果是：接口靠散文契约、状态三份表示、pending ask 写两遍、终端成像规则只能起 Textual 才能测。

本次不改 UI 行为、不改用户可见文案、不动 Textual 版本，只重排 Seam 与 Interface：
让每个 Module 的 Interface 变小、把实现沉到 Seam 之后、让测试穿过同一个 Interface。

**判据不是「代码更短」，而是：同一份知识只存在一处（locality），一份实现被 N 个调用点与
M 个测试复用（leverage）。**

## 2. 现状与证据

### 2.1 包的 Interface 没有声明

| 消费者 | 实际依赖 |
| --- | --- |
| `core/cli.py` | `from core.tui.ui_textual import run` |
| `core.interaction` | `core.tui.render` **被 import 时的注册副作用**接通 |
| `tests/`（66 处 `from core.tui…`，25 个文件起 `ChatApp`） | `ChatApp`、`render._APP`、`app._tool_cards`、各私有纯函数 |
| `smoke/`（22 处 import） | `_CappedCardBody`、`_ClarifyList`、`_CommandInput`、`_file_rows`、`_SPINNER_FRAMES` 等，几乎全私有 |

`core/tui/__init__.py` 为空；包名入口是 `ui_textual.py`；端口实现靠 import 副作用注册。
事实 Interface = 测试与 smoke 用到的私有符号集合，因此任何重命名要改 25+ 个文件。

### 2.2 三个 mixin 是三个文件，不是一个 Module

`ChatApp(_RenderSurface, _CommandFlow, _DockInteractions, App)`：

- 三个 mixin 之间有 **149 处跨类私有成员访问**（`commands.py` 62 / `interactions.py` 78 /
  `surface.py` 9），方向是双向的：
  `_CommandFlow._set_idle` → `_DockInteractions._reset_interactions` →
  读 `_CommandFlow._busy`；
- 宿主契约是 docstring 里的一段散文（「宿主契约（ChatApp 提供）：…」），没有类型、没有测试；
- `_ChatScreen` 还要读 `app._perm_pending` / `_clarify_pending` / `_clarify_other` 做焦点改道，
  是同一批私有状态的第三个消费者。

### 2.3 一个状态三种表示

右栏折叠态同时存在为 `ChatApp._manual_expand`、`ChatApp._panel_pct`、`#right.-collapsed`；
`_sections_non_empty` 是第四份。改一条折叠规则要同时编辑 `ui_textual.py` 三个方法、
`info_panel.py` 的回调与 `app.css` 两条规则。

### 2.4 pending ask 写了两份

权限与 clarify 各有一套 `pending / future / show / hide / highlight / focus / status`；
`_answer_permission` 与 `_answer_clarify` 的收尾逐行同构；`_reset_interactions` 是第三份。
两个 Adapter 是真实的（两处变体），但没有被收进一个 Module。

### 2.5 终端成像规则粘在 Textual 层

- 键帽跨 chunk 的半截前缀不变式分散在 `surface._stream_update`、`surface._stop_stream`、
  `render._dim_body`、`render._add_markdown_card` 四处；
- 参数格式化 / diff 行号沟槽 / `Tool Result:` 头行 / 折叠上限全在 `render.py`，
  必须启动 App 才能测：110 个测试文件中 25 个起 Textual。

### 2.6 两处小接缝错误

- `footer.py` 用 `getattr(self.app, "_manager", None)` 反向摸宿主的私有成员；
- `utils.py` 有 6 个同构的 `try: from core.client … except: return ""` 适配器，
  页脚每 2s 全部执行一次。

## 3. 目标 / 非目标 / 反目标

**目标**

1. 包级 Interface 收敛为 2 个符号，且 `import core.tui` 无副作用。
2. 端口 Adapter 收成一个 Module（线程桥 + 注册 + 生命周期），渲染值构造收成纯 Module。
3. 三个 mixin 溶解为 4 个可独立构造、可独立测试的 Module；`ChatApp` 只剩事件路由与装配。
4. pending ask 收成一个纯状态机 + 一个幂等 reconcile 的部件 Adapter。
5. 每步可独立合并且 `pytest -q` 全绿；用户可见行为与文案零变化。

**非目标**

- 不改 UI 布局、配色、快捷键、用户可见文案。
- 不替换 Textual，不引入新的 UI 框架或新依赖。
- 不改 `core.interaction` 端口的 9 个函数签名（域层不受影响）。
- 不重写 17 个弹窗；表单窗（settings / mcp / login）留到独立设计。
- 不追求行数下降；允许 `blocks.py` 这类「拆出来更清楚」的净增行数。

**反目标**

- 新增一个模块只是为了把 3 行函数挪个位置（pass-through）。
- 为「将来可能有别的 UI」定义没有实现的 Protocol / 抽象基类。
- 用 `ChatApp` 私有成员名的字符串约定继续连接两个模块。
- 迁移中让同一份状态同时存在于旧位置和新位置超过一个提交。

## 4. 目标 Interface

### 4.1 包级

```python
# core/tui/__init__.py
__all__ = ["run", "ChatApp"]

run(handle_query=None, session_manager=None, banner=..., on_interrupt=None, runtime=None) -> None
ChatApp   # 测试 / smoke 直驱 run_test 用
```

不变式（写进 docstring，属于 Interface）：

- `run()` 阻塞直到退出；期间 `core.interaction` 的 9 个端口已注册；退出后句柄清空。
- 端口渲染函数从任意线程可调；`ask_*` 只允许非 App 线程调用，App 线程调用抛 `RuntimeError`。
- 未 attach 时渲染函数抛 `RuntimeError`（与今天 `_require_app` 语义一致），不静默。
- `import core.tui` 不注册、不写全局；生命周期只经 `port.attach(app)` 一处进入：

```python
# core/tui/render.py
@contextmanager
def attach(app: ChatView) -> Iterator[None]:   # 注册端口 + 设置句柄；不可嵌套
```

`run()`、`tests/support.py` 的 `render_bridge`、`smoke/__init__.py` 共用这一个入口。

### 4.2 内部模块与 Seam 图

```
core.interaction
      │ 9 个端口函数
      ▼
    render.py ─────────────► blocks.py（纯）
      │  attach / _exec          ▲
      ▼                          │
    board.py ────────────────────┘
      │
   parts.py ◄── ui_textual.py（ChatApp：事件路由 + 装配）
      ▲              │
 ask_panel.py ──► ask.py（纯）
      ▲              │
   turn.py ──────────┘
      │
 commands.py ──► screens/
```

单向依赖，无双向 import；`ask_panel.py` 只管部件，不知道回合是否存在——作答后的收尾由
`interactions.py`（App 事件路由）调用 `Turn.finish`，不 import `turn.py`。

### 4.3 `blocks.py`（Step 1）

纯展示值构造：无 Textual import、无线程、无 App、无全局。

```python
DEFAULT_MAX_LINES = 10
TOOL_ARGS_MAX_LINES = 4

@dataclass(frozen=True)
class Block:
    kind: str                      # 卡片 CSS 类：user/assistant/tool/result/error/perm/notice/sessions
    body: Text | str               # assistant 用 str（Markdown 部件），其余用 Text
    cap: int | None = None         # None = 不折叠
    head: int = 0                  # 固定头行数，不参与折叠
    tail: Text | None = None       # 固定尾段（diff 场景的结果正文）
    call_id: str = ""              # 非空 = 结果段并回该调用卡

def strip_keycaps(text: str) -> str
def split_trailing_keycap(text: str) -> tuple[str, str]   # (可发片段, 留给下一片的半截前缀)
def dim_body(text: str) -> Text

def user_input(text: str) -> Block
def assistant_markdown(content: str) -> Block
def tool_call(call_id: str, name: str, args: Any) -> Block
def tool_result(call_id: str, output: Any, *, is_error: bool | None = None,
                diff: list[tuple[str, int, str]] | None = None) -> Block
def background_notice(message: str) -> Block
def permission(message: str, details: list[str]) -> Block
def sessions_empty() -> Block
```

隐藏的复杂度：单个参数值的单行化（bool / null / 嵌套 JSON / 换行转义）、diff 行号定宽沟槽、
`Tool Result:` 头行与固定头行计数、键帽降级与跨 chunk 半截序列、`-merged` 与折叠上限。

**删除测试**：删掉 `blocks.py`，这些规则分别落回 `render.py`、`interactions.py`、smoke 与
4 个测试文件——复杂度扩散，因此它值得存在。

### 4.4 `board.py`（Step 3）

```python
class Board:
    def __init__(self, scroll: Callable[[], VerticalScroll], *, on_welcome: Callable[[bool], None]) -> None
    def add(self, block: Block, *, spin: bool = True) -> None
    def merge(self, block: Block) -> None          # 用 block.tool_call_id 查登记；未登记则独立成卡
    def stream(self, chunk: str) -> None
    def end_stream(self) -> None
    def clear(self) -> None
```

不变式：只在 App 线程调用；`end_stream` 幂等且补发半截键帽；`stream` 首片自建卡、空片 no-op；
`merge` 对同一 `tool_call_id` 只并一次；`clear` 先停流再删卡再复位欢迎。
`_tool_cards` 与 `_keycap_carry` 成为实现细节（不再出现在 `ChatApp` 上）。
需要宿主提供的东西从「整个 `self`」收敛为两个构造参数：滚动容器、首次内容回调。

### 4.5 `ask.py` + `ask_panel.py`（Step 4）

```python
class AskKind(StrEnum): PERMISSION; CLARIFY

@dataclass(frozen=True)
class AskRequest:                  # 一次询问请求
    kind: AskKind
    options: tuple[str, ...] = ()
    multi: bool = False
@dataclass(frozen=True)
class AskView:                     # 部件渲染所需的当前视图
    request: AskRequest
    chosen: frozenset[int]
    other_mode: bool

class AskState:                    # 同一时刻至多一个挂起请求
    def open(self, request: AskRequest, future: Future[str]) -> bool
    def view(self) -> AskView | None
    def toggle(self, index: int) -> bool
    def set_other_mode(self, active: bool = True) -> bool
    def answer(self, value: str = "") -> bool     # 幂等；写 Future 并关闭
    def cancel(self) -> bool                      # 权限→"no"，clarify→"[User cancelled]"

class AskPanel:                    # 视图 → 部件
    def sync(self) -> None         # 幂等：部件状态 = asks.view() 的派生物
    def answer(self, value: str) -> bool
    def select(self, index: int) -> bool
    def toggle(self) -> None
    def confirm(self) -> bool
    def enter_other(self) -> None
    def submit_other(self, text: str) -> bool
    def cancel(self) -> bool
    def focus_target(self, widget: Widget | None) -> Widget | None
```

`AskPanel.sync()` 是唯一渲染动作：open / answer / cancel / toggle / Other 都变成
「改状态 → sync」。状态行操作提示由 `AskPanel` 生成（纯状态机不含 UI 文案）。
`_answer_permission` 与 `_answer_clarify` 合并为 `_answer`；`_reset_interactions` 消失。

### 4.6 `turn.py`（Step 5）

```python
class Turn:
    def start(self, work: Callable[[], None], *, status_text: str,
              thread_name: str, error_title: str) -> Thread | None   # None = 忙，已拒绝
    def finish(self) -> None                      # 幂等：cancel_asks → 进度 → 状态行 → 输入条
    @property
    def busy(self) -> bool
```

隐藏：忙门禁（回合中拒绝 `/new` `/compact` `/fork` 与普通消息）、worker 线程与异常上屏、
收尾顺序（先清挂起再算进度）、App 已退出时静默。`busy` 在生产代码中只由本 Module 写。

### 4.7 `parts.py` 与右栏（Step 2 / 6）

```python
class Parts:                       # 49 处 query_one("#…") 的唯一收口
    board: _ChatBoard; prompt: _CommandInput; status: _StatusLine
    left: Vertical; right: Vertical; info_tab: Static
    perm: OptionList; clarify: _ClarifyList; info: _InfoPanel; footer: _FooterBar
```

右栏三态合一为 `RightPanel`：`collapsed` / 宽度百分比 / 手动展开意图一份真值，
`sync()` 幂等落到 CSS 类与 `styles.width`。页脚构造时注入 `FooterEnv` 快照与 token 来源，
不再 `getattr(self.app, "_manager")`；环境读取合并为 `utils.read_footer_env()` 一次。

## 5. 方案与取舍

| 方案 | 做法 | 结论 |
| --- | --- | --- |
| A. 保留 mixin，只把契约写成 Protocol | 给 `ChatApp` 加 `ChatView` Protocol，私有成员照旧 | 否：不是真 Seam（只有一个实现），契约仍无强制力，149 处私有访问不变 |
| B. 拆成独立 Module，构造时注入协作者 | `Board(scroll, on_welcome)`、`AskPanel(parts, asks)`、`Turn(...)` | **采用**：依赖显式、可独立构造、测试穿过各自 Interface |
| C. 单一 God Object + 纯函数下沉 | 只抽 `blocks.py`，App 保持 mixin | 部分采用：Step 1 先做，风险最低，但不解决 2.2 / 2.4 |
| D. 事件总线解耦 mixin | 用消息在 mixin 间通信 | 否：把双向调用换成隐式订阅，locality 更差 |
| E. 抽 `Activity` 模块管 `_progress_sync` | 独立状态机 | 否：`_progress_sync` 已是一条函数一条优先级规则，抽出即 pass-through；删除测试失败 |

排序理由：A/D 是「看起来解耦、实际没变」的重构；B 的代价是每个 Module 显式列出协作者
（构造参数变多），换来的是删除宿主契约散文、单写者状态与可独立测试。C 先行是因为它零行为
变化、可先落地验证测试口径。

## 6. 迁移计划（每步可独立合并）

| 步 | 动作 | 行为变化 | 主要触及 |
| --- | --- | --- | --- |
| 1 | 抽 `blocks.py`；`render.py` 只留桥接；`interactions.py` / `surface.py` / `info_panel.py` 改导入 | 无 | `render.py`、`surface.py`、`interactions.py`、`info_panel.py`、`tests/test_render_tool_call_args.py`、`tests/test_ui_markdown_keycap.py` |
| 2 | `parts.py` 收口 `query_one`；包 `__init__` 声明 `run` / `ChatApp`；`render.attach` 取代 import 副作用注册 | 无 | `ui_textual.py`、`surface.py`、`commands.py`、`interactions.py`、`tests/support.py`、`smoke/__init__.py` |
| 3 | `surface.py` + `cards.py` → `board.py`；`_tool_cards` / `_keycap_carry` 下沉 | 无 | `surface.py`、`board.py`、`ui_textual.py`、2–3 个 UI 测试 |
| 4 | `interactions.py` → `ask.py`（纯）+ `ask_panel.py`（reconcile） | 无 | `interactions.py`、`ask.py`、`ask_panel.py`、权限/clarify 测试 |
| 5 | `commands.py` 拆 `turn.py`；`_busy` 单写者 | 无 | `commands.py`、`turn.py`、回合 wiring 测试 |
| 6 | 右栏三态合一；页脚注入 env 快照 | 无 | `ui_textual.py`、`info_panel.py`、`footer.py`、`utils.py` |

Red-Green 口径：每步先写/改测试到失败（新 Interface 的用户），再改实现；不允许先删旧符号
再补测试。每步结束必须 `uv run pytest tests/ -q` 全绿。

## 7. 测试与验收

**每步的测试口径**

- Step 1：`tests/test_tui_blocks.py`（纯，无 Textual）+ `tests/test_render_tool_call_args.py`
  与 `tests/test_ui_markdown_keycap.py` 改导入；现有 UI 测试不动即绿。
- Step 2：`tests/test_tui_parts.py`（部件取件 + 缺失时抛错语义）；`tests/support.py` 的
  `render_bridge` 改 `port.attach`，现有 UI 测试不动即绿。
- Step 3：`tests/test_tui_board.py` 用真实 `VerticalScroll` 在 `run_test` 内测 6 个方法；
  合并/未登记/重复 merge/清屏四例；`app._tool_cards` 断言改为行为断言（不出现第二张卡）。
- Step 4：`tests/test_tui_ask.py`（纯：至多一个、幂等 answer、两种 cancel 策略、多选累积、
  Other 模式）；`tests/test_ui_permission_card.py` 等改走 `AskState`。
- Step 5：`tests/test_tui_turn.py`（忙门禁、finish 幂等、异常上屏、App 退出静默）。
- Step 6：页脚 / 右栏现有测试改注入方式。

**验收指标**

- 包外零私有 import：`tests/` 与 `smoke/` 只用 `run` / `ChatApp` / 声明为公开的纯函数。
- 需要启动 Textual 的测试：25 → ≤10（纯 Module 的测试不启动 App）。
- `core/tui/` 内零双向 import。
- `ChatApp` ≤ 250 行；`core/tui/__init__.py` 有 `__all__`。
- 右栏折叠态只有一份真值；生产代码中 `Turn.busy` 只有一个写者。
- `uv run python -m core.tui.ui_textual --smoke` 通过。

## 8. 风险与回退

- **Textual 内部行为依赖**：`_CappedCardBody` 覆写 `get_content_height` / `render_line`，
  `_ChatScreen.set_focus` 覆写焦点改道；迁移只搬位置不改逻辑。回归由现有 UI 测试与 smoke 兜。
- **线程时序**：`render.attach` 取代 import 副作用注册后，未 attach 就渲染会抛 `RuntimeError`
  （今天已如此），但「注册了适配器但 App 未启动」的旧路径会变。`tests/test_ui_notify.py`
  经 `core.interaction` 调用，必须走 `port.attach`，否则红。
- **回退**：每步一个提交，互不依赖；任一步回归即 revert 该提交，先前步骤仍有效。

## 9. 未决问题

- `blocks.Block` 的 `kind` 同时承担 CSS 类与内容类型两个语义；若后续卡片类型增多，
  再拆 `kind` / `card_class`。
- 表单窗统一（settings / mcp / login，1241 行）需要先 design-twice 比较字段声明式与
  现状，另立 spec，不在本次范围。

## 10. 实施进度

| 步 | 状态 | 验证 |
| --- | --- | --- |
| 1. `blocks.py` | 完成 | 纯测试 `tests/test_tui_blocks.py` |
| 2. `parts.py` + `port.attach` + 包级 Interface | 完成 | `tests/test_tui_parts.py`（含子进程「import 不拉起 Textual」） |
| 3. `board.py` + `status.py`（`surface.py` 删除） | 完成 | `tests/test_tui_board.py` |
| 4. `ask.py` + `ask_panel.py`（`interactions.py` 仅剩事件路由） | 完成 | `tests/test_tui_ask.py`（纯）+ `tests/test_tui_ask_panel.py` |
| 5. `turn.py`（`_busy` 单写者） | 完成 | `tests/test_tui_turn.py`（纯） |
| 6. `right_panel.py` + 页脚注入 env 快照 | 完成 | 现有 info panel / footer 测试改注入方式 |

全量验证：`uv run pytest tests/ -q` → 1180 passed / 6 skipped；
`uv run python -m core.tui.ui_textual --smoke` → 全部 OK，exit 0。

### 评审后的接口修订（2026-11-14）

三路只读审查后定稿的名字（文档正文已同步）：`Ask` → `AskRequest`、`AskDesk` → `AskState`、
`Dock` → `AskPanel`（文件 `dock.py` → `ask_panel.py`）、`desk` → `asks`、`dock` → `ask_panel`；
`submit` → `answer`、`other()` → `set_other_mode()` / `enter_other()`；`Block.head` → `head_lines`、
`Block.cap` → `max_lines`、`Block.call_id` → `tool_call_id`、`_CappedCardBody.reserved` → `head_lines`；
`Turn.start(name=)` → `thread_name=`、`dispatch` → `call_on_app_thread`；
`EnvSnapshot` → `FooterEnv`、`read_env` → `read_footer_env`。

同时修掉三条审查发现：第二个并发询问不再悬挂（立即按拒绝 / 取消落地 Future）；
多选 clarify 在 Other 行按 Enter 进入输入模式（原先直接提交）；`AskPanel.sync()` 在 Other
模式真正幂等（原先第二次 sync 会禁用输入条）。

### 与计划的偏差

- **文件名未改**：`ui_textual.py` 与 `render.py` 保留原名。包级 Interface 已由
  `core/tui/__init__.py` 的 `run` / `ChatApp` 承担，改名只产生 20+ 处无接口收益的 churn；
  `render.py` 仍是端口 Adapter（渲染 + attach；`ask_*` 留在这里是因为它们是端口的两个入口）。
- **`ChatApp` 行数**：495 行（目标 ≤250）。剩余部分是 compose、Textual 事件路由、右栏动作、
  任务栏进度与入口 `run()`；状态与渲染/询问/回合实现均已移出。行数不是本次判据：
  跨 mixin 私有成员访问 149 → 73（且 73 处都是具名协作者 `board` / `turn` / `dock` / `desk` /
  `parts` 或 App 的 4 个公开回调），`ui_textual.py` 内 `query_one("#…")` 49 → 0。
- **`utils.py` 的逐项读取函数保留**：它们各有独立测试且被 `/model` 弹窗复用；
  页脚改为经 `read_env()` 一次取快照，不再逐项读取、也不再 `getattr(app, "_manager")`。
- **`_FooterBar` 默认参数**捕获模块级 `read_env`：测试改为 patch `core.tui.utils.current_*`
  （数据源），而不是 patch 页脚模块的导入绑定。
