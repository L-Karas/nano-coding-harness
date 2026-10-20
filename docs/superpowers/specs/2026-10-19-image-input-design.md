# 图像输入支持设计（nano-harness）

- 日期：2026-10-19
- 状态：待评审
- 关联代码：`core/context/message.py`、`core/context/token.py`、`core/context/session/session.py`、
  `core/template/message_template.py`、
  `core/tools/tool_result.py`、`core/tools/tool_pool.py`、`core/tools/base_tools/read.py`、
  `core/agent_step.py`、`core/client/model.py`、`core/tui/render.py`、`core/tui/commands.py`、
  `core/tui/screens/fork.py`
- 关联文档：`docs/tools.md`、`docs/architecture.md`、`docs/context-compaction.md`、`GLOSSARY.md`

## 1. 意图

改 TUI、或对界面某处不满意时，纯文字很难精确指位置。用户需要把截图（或图片 URL）交给
harness，由模型直接看到图后再讨论怎么改。

当前缺口：

- `read_file` 只读文本/文档，图片只有 `read_image()` 空壳；
- `Message.content` 只有 `str`，会话 JSONL 与 `to_llm_messages()` 都只能承载文本；
- 工具结果只有文本，没有「随本次读取带回图片」的通道。

本设计只做一条链路：**用户给出路径 / URL → 模型调用 `read_file` → 工具返回结果并在本轮
工具全部执行完后统一附加一条图片 user 消息 → 会话按请求视图落盘 → 压缩照常处理**。
不做剪贴板粘贴、不做 TUI 图片渲染。

## 2. 已确认的设计决策

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 图片入口 | 用户把路径（或图片 URL）写给模型，由 `read_file` 读取；不做粘贴 / 拖拽 / `@` 附件 |
| 2 | 请求形态 | 图片只放在 user 消息的 content 数组：`{"type":"text"}` + `{"type":"image_url"}`；本地图用 `data:<mime>;base64,...`，URL 图原样透传给接口 |
| 3 | 消息模型 | `Message.content` 扩展为 `str \| list[dict]`；`to_llm_messages()` 对数组原样透传；当前模型 `vision=false` 时整条替换为纯文本占位（见 #11） |
| 4 | 工具返回 | `read_file` 仍返回文本结果（成功 / 失败）；图片作为 `ToolResult.images` 带回，不写进 tool 消息 |
| 5 | 注入时机 | 一轮所有工具执行完后，在 tool 消息之后追加**一条** user 注入消息：文本按调用顺序列出路径，图片 part 顺序与之一致 |
| 6 | 落盘 | 会话 JSONL 直接保存这条请求视图（含 base64）；不复制原图、不建资源目录、不保存文件路径到别处 |
| 7 | TUI | 注入消息统一使用 `INJECTION_MESSAGES_TEMPLATE`；TUI 回放跳过，不渲染图片，不参与会话标题 / fork 列表 |
| 8 | 压缩 | 图片作为普通消息一起参与压缩；token 估算文本正常计，每个 `image_url` part 暂按固定 **1000 token** 计 |
| 9 | 本地图片限制 | 支持 `png` / `jpg` / `jpeg` / `gif` / `webp`，单图上限 5 MB |
| 10 | 新读图片的模型能力 | 当前模型 `vision=false` 时 `read_file` 不编码图片、不返回附件，工具结果说明原因；step 层对自定义工具返回的图片兜底不附加 |
| 11 | 旧会话图片 + 非视觉模型 | 请求时把含图片的整条注入消息替换为纯文本 user 占位消息，会话文件保持原样；切回视觉模型后原图仍可用 |

## 3. 消息与请求视图

### 3.1 `Message.content`

```python
content: str | list[dict] = ""
```

- 纯文本消息保持 `str`，所有既有调用方行为不变。
- 图片注入消息使用 OpenAI 兼容 content 数组，数组中只出现两种 part：
  - `{"type": "text", "text": "<injection_messages>...</injection_messages>"}`
  - `{"type": "image_url", "image_url": {"url": "<data URL 或 https URL>"}}`
- 会话落盘仍用 `asdict(message)` → JSON；数组内容原样序列化 / 反序列化。

新增三个纯函数（放 `core/context/message.py`，并可从 `core.context` 导入）：

- `content_text(content) -> str`：`str` 原样返回；数组按顺序拼接所有 `text` part 的文本
  （`"\n"` 分隔）。用于标题、fork 预览、TUI 回放、注入识别。
- `is_injected_content(content) -> bool`：`content_text()` 去空白后是否以
  `INJECTION_MESSAGES_PREFIX` 开头、以 `INJECTION_MESSAGES_SUFFIX` 结尾。
- `is_synthetic_content(content) -> bool`：`content_text()` 去前导空白后是否以 `<` 或 `[`
  开头。会话标题与 `/fork` 列表共用这一条「合成消息」规则（cron、后台通知、压缩摘要、
  图片注入都算合成消息）。

`to_llm_messages(messages, *, vision=True)` 做字段裁剪，并新增一个视觉门控：

- `vision=True`（默认）：`content` 是数组时原样透传，不再假设 `str`。
- `vision=False`：只要消息 content 是数组且含 `image_url` part，就把**整条消息**替换为
  一条纯文本 user 消息（不保留原数组、不保留 text part）：
  `{"role": "user", "content": IMAGE_OMITTED_MESSAGE}`，其中
  `IMAGE_OMITTED_MESSAGE = "[Image message omitted: the current model does not support vision input.]"`
  放进 `core/template/message_template.py`。会话里保存的消息不被修改。

路径不会丢：紧邻的 tool 结果文本仍然带有 `Image read: <source>`，模型能据此知道读过哪些文件。

两个请求构造点显式传当前 client 的能力：`agent_step`（主 / 子代理请求）与
`context_compact.summarize_history`（压缩请求）。这样 `/model` 切到非视觉模型、或重启后用
非视觉默认模型恢复旧会话时，主请求和压缩都不会因 image part 报错。

### 3.2 Token 估算

`core/context/token.py` 的 `estimate_size()` 对数组 content：文本 part 正常估算，每个
`image_url` part 固定计 **1000 token**（模块常量 `IMAGE_TOKEN_ESTIMATE = 1000`，暂不做按
尺寸 / 分辨率的精确计算）。**dict 分支与 Message 分支都要按同一规则处理**：子代理历史是
dict 列表，不能只改 Message 分支。压缩的切分与触发会把这些固定估算算进去（见 §9 已知限制）。

## 4. `read_file` 行为

### 4.1 输入分类

| 输入 | 行为 |
|---|---|
| 现有文本 / document 格式 | 完全不变（`anydoc` 转换、行数 / 字节截断、`limit` / `offset`） |
| 本地图片路径（后缀命中图片表） | 检查大小 → 读原始字节 → base64 → data URL；返回 `ToolResult`（文本 + `images`） |
| `http(s)://` 图片 URL（路径部分后缀命中图片表） | **不下载、不校验**，直接作为 `image_url.url` 透传；返回 `ToolResult`（文本 + `images`） |
| 其他二进制 / 未知后缀 | 维持现状（按文本读，解码失败即工具错误） |

`ToolContext.vision=false` 时，本地图片与图片 URL 两条分支都不读取 / 编码，只返回文本结果
（含来源、MIME、大小与「当前模型不支持视觉输入，未附加」说明）。

图片表与上限（`core/tools/base_tools/read.py` 模块常量）：

```python
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
```

- URL 识别：`^https?://`（大小写不敏感），去掉 query / fragment 后取后缀判断。
  `http(s)` URL 的后缀不在表中 → 工具错误（`Unsupported image URL`），不猜测、不下载。
- 本地图片超过 5 MB → `ToolResult.error(...)`，不注入图片。
- 图片读取忽略 `limit` / `offset`（描述中说明这两个参数只对文本有效）。
- 同步 `run()` 与异步 `arun()` 行为一致；异步读字节用 `aiofiles`，避免阻塞 loop。

工具结果文本保持中性、不承诺「已附加」：

```text
Image read: <source> (<mime>, <bytes> bytes).
```

`source` 对本地图是解析后的绝对路径，对 URL 是原始 URL。失败走既有
`ToolResult.error()` / 异常转换路径。

### 4.2 `ToolResult` 扩展

`core/tools/tool_result.py` 新增不可变值：

```python
@dataclass(frozen=True)
class ImageAttachment:
    source: str      # 本地绝对路径或原始 URL（注入消息里按此顺序列出）
    mime: str
    image_url: str   # data URL 或 http(s) URL，直接填进 image_url.url
```

`ToolResult` 增加 `images: tuple[ImageAttachment, ...] = ()`。`error()` / `cancelled()` /
`unknown()` 不带图片。

`ToolPool.execute()` / `execute_sync()` 增加一条规则：工具返回 `ToolResult` 时原样返回，
否则维持 `ToolResult(content=str(result))`。这样 `read_file` 可以在返回文本的同时带回图片，
其他工具零改动。

## 5. 一轮中的图片注入

### 5.1 收集

`core/agent_step.py` 的 `_run_tools()`：

- 在收尾段按**原 tool call 顺序**收集每个成功、未取消结果的 `outcome.images`；
- 失败 / 取消的读取不产生图片；
- 同一图片被读多次不去重（顺序即语义）；
- 收集到的 `ImageAttachment` 只在**本轮所有工具执行完之后**统一处理。

### 5.2 视觉能力检查

`core/client/model.py` 新增公开方法：

```python
def supports_vision(self) -> bool:
    # 未选择模型 → False；否则取当前模型注册配置的 vision 字段
```

`run_agent_step()` 把 `client.supports_vision()` 传入 `_run_tools()`（仅布尔，不把 client
泄漏进工具层）。

- `vision=true`：注入带图片 part 的 user 消息。
- `vision=false`：`read_file` 已在工具侧跳过编码并返回文本说明，因此新读图片不会产生附件；
  若自定义工具仍返回了图片，step 层兜底不附加，只注入一条文本消息说明当前模型不支持视觉输入
  并列出路径。这样模型仍知道发生过什么，接口也不会因 image part 报错。

### 5.3 注入消息形态

`vision=true` 时，`followup_messages` 末尾追加：

```python
{
    "role": "user",
    "content": [
        {
            "type": "text",
            "text": INJECTION_MESSAGES_TEMPLATE.format(content=(
                "The following images were returned by tools for visual inspection "
                "(in order):\n1. /abs/a.png\n2. https://example.com/b.jpg"
            )),
        },
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
        {"type": "image_url", "image_url": {"url": "https://example.com/b.jpg"}},
    ],
}
```

- 文本在数组首位，来源编号顺序与后续 image part 严格一致。
- 整条消息只出现一次，位于**所有 tool 消息之后**（满足 OpenAI「tool 结果紧跟 assistant
  tool_calls」的约束）。
- 使用统一注入模板，因此 `is_injected_content()` 为真，TUI 回放 / 标题 / fork 都会跳过。
- `vision=false` 时（自定义工具兜底路径）注入的是纯文本 user 消息（`content` 为 `str`），
  没有 image part；说明文字放在注入模板内部，消息仍以 `</injection_messages>` 结尾，TUI 回放照常跳过。

### 5.4 与其他注入的关系

现有顺序是 `injected（after_llm）+ tool_messages + tool_injected（after_tool / 后台）`。
图片注入追加在 `tool_injected` 末尾：扩展注入的消息在前，图片消息在最后。

- `after_tool` 扩展仍只看到文本结果，不能增删图片（本期不做）。
- `tool_execute` 扩展接管 `read_file` 时只能返回文本，不会产生图片（已知行为，见 §9）。
- 后台任务不经 `read_file`（`read_file` 不是慢命令），因此不需要后台结果携带图片。

## 6. 会话、TUI 与压缩

### 6.1 SessionManager

- `add_message()` / `update_messages()` / 落盘不需要改数据结构，数组 content 自然序列化。
- `_update_session_title()`：先 `content_text()`，再用 `is_synthetic_content()` 跳过合成消息
  （原来的 `startswith(("<", "["))`），因此图片注入不会改写标题。
- `_fork_title()` 与 `fork_session()` 用 `content_text()` 取标题文本。
- `load_user_messages()`（`/fork` 列表）过滤**合成用户消息**：用 `is_synthetic_content()`
  （`content_text(content).lstrip()` 以 `<` 或 `[` 开头），而不是只匹配注入模板。因此
  图片注入、cron、后台通知、续跑提示、压缩摘要都不会出现在 fork 列表里，与「不参与标题」
  的口径一致。标题与 fork 两处共用该判定，补测试。

### 6.2 TUI

本期**不渲染图片**，只保证不崩、不误显示注入消息：

- `render._is_injected_message()` 改为调用 `is_injected_content()`（支持数组）；
  `render_session_history()` 遇到图片注入消息直接跳过。
- `render_user_input()` 接受 `str | list`，内部用 `content_text()` 压平（防御性；正常路径
  不会把注入消息送到这里）。
- `/fork` 的列表预览与回填输入框：`core/tui/screens/fork.py` 与
  `core/tui/commands.py` 改用 `content_text()`。
- 实时回合中注入消息本来就不渲染（只有 assistant / tool 事件走 renderer），无需改动。

### 6.3 压缩

- `estimate_size()` 支持数组（§3.2），每个图片按固定 1000 token 计入压缩预算。
- `summarize_history()` 通过 `to_llm_messages(..., vision=client.supports_vision())` 构造请求：
  视觉模型下图片随消息一起被总结（与「压缩请一并压缩」一致）；非视觉模型下整条图片注入
  消息被替换为纯文本占位，压缩本身不会失败。
- 因此「会话里已有图片、后来切到非视觉模型」时，`/compact` 仍可用；被省略的是本次请求里的
  图片，会话文件中的原图不受影响。
- 自动压缩保留的最近消息前缀里如果有图片注入消息，数组原样保留；被切掉的部分随摘要消失。
- 不做图片 token 的精确计算，固定按 1000 / 图估算。压缩流程本身不剥离图片：存储与保留段
  保持原样；只有非视觉模型构造请求时，才由 `to_llm_messages(vision=False)` 把整条图片
  注入消息换成纯文本占位。

## 7. 非目标

- 剪贴板粘贴、拖拽、`@` 图片附件、TUI 内联图片渲染；
- 图片下载、缩放、压缩、格式转换、EXIF 处理；
- 图片 token 精确计算与预算；
- 把图片复制进 `.harness/` 或单独的资源目录；
- 同一图片去重 / 引用计数；
- 后台任务、`tool_execute` 扩展、MCP 工具携带图片；
- 用户直接输入 `data:` URL（本期只认本地路径与 `http(s)` 图片 URL）。

## 8. 改动清单

| 文件 | 改动 |
|---|---|
| `core/template/message_template.py` | `IMAGE_OMITTED_MESSAGE` 占位常量 |
| `core/context/message.py` | `content` 类型、`content_text()`、`is_injected_content()` / `is_synthetic_content()`、`to_llm_messages(messages, *, vision=...)` 数组透传与视觉门控 |
| `core/context/__init__.py` | 导出新 helper |
| `core/context/token.py` | 数组 content 按 text 正常计、每个 image part 固定 1000 token |
| `core/context/session/session.py` | 标题 / fork 用 `content_text()`；fork 列表过滤合成用户消息 |
| `core/tools/tool_result.py` | `ImageAttachment`、`ToolResult.images` |
| `core/tools/tool_pool.py` | 原样透传工具返回的 `ToolResult` |
| `core/tools/base_tools/read.py` | 图片常量与读取、URL 透传、同步 / 异步、描述更新、删除空壳 |
| `core/client/model.py` | `supports_vision()` |
| `core/agent_step.py` | 收集图片、vision 门控、构造注入消息、放在 tool 消息之后；请求构造传 `vision=client.supports_vision()` |
| `core/context/compact/context_compact.py` | 压缩请求构造传 `vision=client.supports_vision()` |
| `core/tui/render.py` | 注入识别支持数组；用户卡片压平 |
| `core/tui/commands.py` / `core/tui/screens/fork.py` | fork 回填 / 预览用 `content_text()` |

## 9. 已知限制

- 会话文件会因 base64 明显变大：单图 5 MB 对应约 6.7 MB JSON，且每次读取都可能新增一条；
  这是「存请求视图、不存原图」的直接代价。
- 不做图片 token 的精确计算：固定按 1000 token / 图估算，与真实用量可能有偏差；
  导致压缩触发过早或过晚。
- `vision` 字段是注册表声明，不保证服务端真实支持；声明错误时仍会得到接口错误。
- 旧会话切到 `vision=false` 模型后，整条图片注入消息在请求里变成纯文本占位；模型只能从
  紧邻的 tool 结果知道读过哪些路径，看不到图，切回视觉模型后恢复。
- 不做单轮 / 单会话图片总量上限：只限制单图 5 MB；一次读多张会生成同样大的请求与会话行。
- 图片 URL 不下载、不校验，接口不可达 / 非图片内容由服务端报错。
- `tool_execute` 扩展接管 `read_file`、后台任务、MCP 工具都不能携带图片。
- 本地图片不缩放：超大尺寸（像素）图片即使小于 5 MB 也可能被服务端拒绝。
- 注入消息在实时回合不显示，重载历史时也跳过；用户不会在 TUI 里看到「已附加图片」的提示。

## 10. 测试计划

- `read_file`：
  - 本地 png/jpg/jpeg/gif/webp → 文本 + data URL + mime；同步 / 异步一致；
  - 超过 5 MB → error，无图片；
  - `http(s)` 图片 URL → 原样 URL、无网络调用；
  - 非图片文本 / 文档路径行为不变（现有测试回归）。
- `ToolPool`：工具返回 `ToolResult` 时原样透传（含 `images`）。
- `agent_step`：
  - 同一轮多个 `read_file` → 所有 tool 消息之后只有一条注入消息，路径与图片顺序一致；
  - 失败 / 取消的读取不注入；
  - `vision=false` → `read_file` 不编码、无附件；自定义工具返回图片时无 image part，仅有文本注入；
  - 无图片时不产生额外消息。
- 消息层：`content_text()`、`is_injected_content()`、`is_synthetic_content()`、
  `to_llm_messages()` 数组透传、`estimate_size()` 数组按 1000 token / 图计。
- 会话：数组 content 落盘 / 重读；标题不被注入消息改写；fork 列表排除合成用户消息。
- 视觉门控：`to_llm_messages(vision=False)` 把含图片的整条消息替换为
  `IMAGE_OMITTED_MESSAGE`（纯文本 user 消息）；会话存储不变；压缩请求同样走门控；
  切回 `vision=True` 后图片 part 恢复。
- TUI：数组注入消息回放跳过；非注入数组 content 压平渲染不崩。
- 压缩：含图片数组的历史可正常压缩（`estimate_size` 不崩；保留段图片仍在）。

## 11. 文档更新

- `README.md`：工具特性补一句图片输入；
- `docs/tools.md`：`read_file` 参数 / 图片分支 / URL 透传 / 5 MB 限制；
- `docs/architecture.md`：Message content 数组与请求视图；
- `docs/context-compaction.md`：图片 token 按 1000 / 图固定估算与偏差；
- `GLOSSARY.md`：`Message` 词条补充 content 数组；新增 `ImageAttachment`（可选）。

## 12. 留给实施者的决定

- `content_text()` 多个 text part 的分隔符（本设计取 `"\n"`）；
- 占位常量 `IMAGE_OMITTED_MESSAGE` 的最终文案；
- 图片注入文本的具体措辞（只要保持路径顺序清晰、使用注入模板）；
- 是否把 `ImageAttachment` 放进 `core/tools/__init__.py` 的导出（按现有导出习惯处理）；
- URL 后缀识别的实现细节（`urlparse` / 正则）与错误文案。
