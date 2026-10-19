# Image Input Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `read_file` 能读取本地图片 / 图片 URL，把图片作为 user content 数组附加到会话，并让会话、压缩、TUI 与请求视图安全地承载它。

**Architecture:** `Message.content` 扩展为 `str | list[dict]`；`read_file` 通过 `ToolResult.images` 带回 `ImageAttachment`；`agent_step` 在一轮工具全部结束后统一追加一条注入 user 消息；`to_llm_messages()` 负责把会话数组转成请求视图，并在当前模型无视觉能力时整条替换为纯文本占位。

**Tech Stack:** Python 3.13、OpenAI 兼容 chat completions、pydantic、aiofiles、pytest、Textual（仅兼容性改动）。

**Spec:** `docs/superpowers/specs/2026-10-19-image-input-design.md`

## Global Constraints

- `requires-python >= 3.13`；不新增第三方依赖，base64 / `urllib.parse` / `aiofiles` 均为现成能力。
- 本地图片只支持 `png` / `jpg` / `jpeg` / `gif` / `webp`，单图上限 **5 MB**；URL 图片只透传、不下载。
- 图片请求只放在 `user` 消息 content 数组：`{"type":"text"}` + `{"type":"image_url"}`。
- `image_url` part 的 token 估算固定为 **1000 token / 图**（`IMAGE_TOKEN_ESTIMATE`）。
- 非视觉模型：请求视图把含图片的整条消息替换为纯文本 `IMAGE_OMITTED_MESSAGE`；会话文件不改。
- 不复制原图进 `.harness/`；不渲染图片；不改变 tool 消息的纯文本契约。
- 每个任务只 `git add` 该任务 Files 列表中的文件；当前工作树已有其他未提交改动，禁止 `git add -A`。
- 测试统一用 `uv run pytest tests/... -v`。

## Review Focus

- **恢复旧会话 / 切到 `vision=false` 模型**：会话里的图片不能漏进请求导致接口报错；整条消息必须变为 `IMAGE_OMITTED_MESSAGE`。测试在 Task 1 与 Task 7。
- **一轮多张图片**：路径列表与 image part 必须同序，且注入消息位于所有 tool 消息之后。测试在 Task 4。
- **图片 URL 与后缀识别**：带 query 的 URL、大写后缀仍应识别；不支持的 URL 必须明确报错而不是猜。测试在 Task 3。
- **5 MB 边界**：恰好 5 MB 允许，超过 1 字节即报错且不注入。测试在 Task 3。
- **会话标题 / fork 污染**：图片注入属于合成消息，不得改写标题、不得出现在 `/fork` 列表或回填文本中。测试在 Task 5 与 Task 6。

---

### Task 1: Message content arrays, helpers, and token estimation

**Files:**
- Modify: `core/context/message.py:17-70`
- Modify: `core/context/token.py:37-48`
- Modify: `core/context/__init__.py`
- Modify: `core/template/message_template.py` (append after `CONTINUATION_PROMPT`)
- Modify: `core/template/__init__.py`
- Test: `tests/test_message_content.py` (create)

**Interfaces:**
- Produces:
  - `Message.content: str | list[dict]`
  - `content_text(content: Any) -> str`
  - `is_injected_content(content: Any) -> bool`
  - `is_synthetic_content(content: Any) -> bool`
  - `to_llm_messages(messages: list, *, vision: bool = True) -> list[dict]`
  - `IMAGE_OMITTED_MESSAGE: str` in `core.template`
  - `IMAGE_TOKEN_ESTIMATE = 1000` in `core.context.token`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_message_content.py`:

```python
"""Message.content 数组、请求视图 helper 与图片 token 估算。"""
from core.context.message import (
    Message,
    content_text,
    is_injected_content,
    is_synthetic_content,
    to_llm_messages,
)
from core.context.token import IMAGE_TOKEN_ESTIMATE, estimate_size, estimate_token
from core.template import IMAGE_OMITTED_MESSAGE, INJECTION_MESSAGES_TEMPLATE


def _image_content(text="images"):
    return [
        {"type": "text", "text": INJECTION_MESSAGES_TEMPLATE.format(content=text)},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]


def test_content_text_flattens_text_parts():
    assert content_text("plain") == "plain"
    assert content_text(_image_content("a")) == INJECTION_MESSAGES_TEMPLATE.format(content="a")
    assert content_text([
        {"type": "text", "text": "a"},
        {"type": "input_audio", "input_audio": {"data": "x"}},
        {"type": "text", "text": "b"},
    ]) == "a\nb"


def test_injected_and_synthetic_detection():
    content = _image_content()
    assert is_injected_content(content)
    assert is_synthetic_content(content)
    assert is_synthetic_content("<compacted_messages>\nsummary\n</compacted_messages>")
    assert is_synthetic_content("[Fork] x")
    assert not is_injected_content("hello")
    assert not is_synthetic_content([{"type": "text", "text": "hello"}])


def test_llm_view_passes_arrays_through():
    message = Message(role="user", content=_image_content())
    assert to_llm_messages([message]) == [{"role": "user", "content": message.content}]


def test_llm_view_replaces_whole_image_message_without_vision():
    message = Message(role="user", content=_image_content())
    assert to_llm_messages([message], vision=False) == [
        {"role": "user", "content": IMAGE_OMITTED_MESSAGE},
    ]


def test_llm_view_keeps_text_messages_without_vision():
    assert to_llm_messages([{"role": "user", "content": "hi"}], vision=False) == [
        {"role": "user", "content": "hi"},
    ]


def test_estimate_size_counts_image_parts_as_1000_tokens():
    text_only = [{"role": "user", "content": "hello"}]
    with_image = [{
        "role": "user",
        "content": [{"type": "text", "text": "hello"}, *_image_content()[1:]],
    }]
    assert IMAGE_TOKEN_ESTIMATE == 1000
    assert estimate_size(with_image) == estimate_size(text_only) + IMAGE_TOKEN_ESTIMATE


def test_estimate_size_handles_message_objects_with_images():
    text = INJECTION_MESSAGES_TEMPLATE.format(content="a")
    message = Message(role="user", content=_image_content("a"))
    assert estimate_size([message]) == estimate_token(text) + IMAGE_TOKEN_ESTIMATE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_message_content.py -v`
Expected: FAIL with `ImportError: cannot import name 'content_text'`（或 `IMAGE_OMITTED_MESSAGE` 不存在）。

- [ ] **Step 3: Add the placeholder template and export**

In `core/template/message_template.py`, next to the other message-level constants:

```python
IMAGE_OMITTED_MESSAGE = "[Image message omitted: the current model does not support vision input.]"
```

Add it to the import list and `__all__` in `core/template/__init__.py`.

- [ ] **Step 4: Implement helpers and vision-gated request view**

In `core/context/message.py`:

- Change `content: str = ""` to `content: str | list[dict] = ""`.
- Add `content_text(content: Any) -> str`:
  - `str` → itself; `list` → join `text` part values with `"\n"`; anything else → `""`.
  - Ignore unknown part types and non-dict entries.
- Add `is_injected_content(content: Any) -> bool`:
  - `text = content_text(content).strip()`
  - return `text.startswith(INJECTION_MESSAGES_PREFIX.strip()) and text.endswith(INJECTION_MESSAGES_SUFFIX.strip())`.
- Add `is_synthetic_content(content: Any) -> bool`:
  - return `content_text(content).lstrip().startswith(("<", "["))`.
- Add private `_has_image_part(content: Any) -> bool` that returns True when a list part has `type == "image_url"`.
- In `to_llm_messages(messages, *, vision=True)`, after reading `content`:
  - `if not vision and _has_image_part(content): content = IMAGE_OMITTED_MESSAGE`
  - keep the rest of the field assembly unchanged.

Import the template constants from `core.template`.

- [ ] **Step 5: Implement image token estimation**

In `core/context/token.py`:

```python
IMAGE_TOKEN_ESTIMATE = 1000

def _estimate_content(content) -> int:
    if isinstance(content, str):
        return estimate_token(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                total += IMAGE_TOKEN_ESTIMATE
            elif part.get("type") == "text":
                total += estimate_token(part.get("text") or "")
        return total
    return 0
```

Use `_estimate_content(message.get("content") or "")` in the dict branch and
`_estimate_content(message.content) if message.content else 0` in the object branch.

- [ ] **Step 6: Export helpers and run the tests**

In `core/context/__init__.py`, export `content_text`, `is_injected_content`, and `is_synthetic_content`
in the existing import/`__all__` style.

Run:

```bash
uv run pytest tests/test_message_content.py tests/test_llm_message_view.py tests/test_context_token.py tests/test_token_budget_wiring.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add core/context/message.py core/context/token.py core/context/__init__.py \
  core/template/message_template.py core/template/__init__.py tests/test_message_content.py
git commit -m "feat: 消息内容支持图像 part 与视觉请求门控"
```

---

### Task 2: `ImageAttachment` on `ToolResult` and ToolPool pass-through

**Files:**
- Modify: `core/tools/tool_result.py`
- Modify: `core/tools/tool_pool.py:92-124`
- Modify: `core/tools/__init__.py`
- Test: `tests/test_tool_result_images.py` (create)

**Interfaces:**
- Produces:
  - `ImageAttachment(source: str, mime: str, url: str)` (frozen dataclass)
  - `ToolResult(content: str, is_error: bool = False, is_cancelled: bool = False, images: tuple[ImageAttachment, ...] = ())`
  - `ToolPool.execute()` / `execute_sync()` return the tool's `ToolResult` unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tool_result_images.py`:

```python
"""ToolResult 携带图片附件；ToolPool 原样透传工具返回的 ToolResult。"""
import asyncio

from core.tools import ImageAttachment, ToolPool
from core.tools.tool_base import BaseTool
from core.tools.tool_result import ToolResult

ATTACHMENT = ImageAttachment(
    source="/tmp/a.png",
    mime="image/png",
    url="data:image/png;base64,AAA",
)


class ImageTool(BaseTool):
    """Image result tool."""
    def run(self, tctx=None):
        return ToolResult(content="image ok", images=(ATTACHMENT,))


class TextTool(BaseTool):
    """Text result tool."""
    def run(self, tctx=None):
        return "text ok"


def test_pool_preserves_images_from_tool_result():
    pool = ToolPool({"image_tool": ImageTool})
    expected = ToolResult(content="image ok", images=(ATTACHMENT,))
    assert pool.execute_sync("image_tool", {}) == expected
    assert asyncio.run(pool.execute("image_tool", {})) == expected


def test_pool_still_wraps_plain_strings():
    pool = ToolPool({"text_tool": TextTool})
    assert pool.execute_sync("text_tool", {}) == ToolResult(content="text ok")


def test_error_results_have_no_images():
    assert ToolResult.error("boom").images == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tool_result_images.py -v`
Expected: FAIL with `ImportError: cannot import name 'ImageAttachment'`。

- [ ] **Step 3: Add `ImageAttachment` and `ToolResult.images`**

In `core/tools/tool_result.py`:

```python
@dataclass(frozen=True)
class ImageAttachment:
    source: str      # 本地绝对路径或原始 URL
    mime: str
    url: str         # data URL 或 http(s) URL


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False
    is_cancelled: bool = False
    images: tuple[ImageAttachment, ...] = ()
```

Keep `error()`, `cancelled()`, and `unknown()` unchanged; they inherit `images=()`.
Export `ImageAttachment` from `core/tools/__init__.py`.

- [ ] **Step 4: Pass `ToolResult` through ToolPool**

In both `ToolPool.execute()` and `execute_sync()`, replace the final
`return ToolResult(content=str(content))` with:

```python
if isinstance(content, ToolResult):
    return content
return ToolResult(content=str(content))
```

Keep the exception handling untouched. `_McpEntry` still returns `str`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tool_result_images.py tests/test_tool_pool.py tests/test_tool_registry.py -v`
Expected: PASS（既有 ToolPool 契约不变）。

- [ ] **Step 6: Commit**

```bash
git add core/tools/tool_result.py core/tools/tool_pool.py core/tools/__init__.py tests/test_tool_result_images.py
git commit -m "feat: ToolResult 支持携带图片附件"
```

---

### Task 3: `read_file` local image and image URL support

**Files:**
- Modify: `core/tools/base_tools/read.py`
- Test: `tests/test_read_image.py` (create)

**Interfaces:**
- Produces:
  - `MAX_IMAGE_BYTES = 5 * 1024 * 1024`
  - `IMAGE_MIME_TYPES: dict[str, str]`
  - `read_file.run()` / `read_file.arun()` return `str` for text/document paths and `ToolResult` for image paths / image URLs.
- Consumes: `ImageAttachment`, `ToolResult` from Task 2.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_read_image.py`:

```python
"""read_file 图片分支：本地 png/jpg/jpeg/gif/webp → data URL；URL 透传；5 MB 上限。"""
import asyncio
import base64

import pytest

import core.tools.base_tools.read as read_mod
from core.tools import ImageAttachment, ToolResult
from core.tools.base_tools.read import ReadFile

PNG = b"\x89PNG\r\n\x1a\n"


def _read(path):
    return ReadFile(path=str(path)).run()


def _read_async(path):
    return asyncio.run(ReadFile(path=str(path)).arun())


def test_max_image_bytes_is_5mb():
    assert read_mod.MAX_IMAGE_BYTES == 5 * 1024 * 1024


@pytest.mark.parametrize(
    ("suffix", "mime"),
    [(".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
     (".gif", "image/gif"), (".webp", "image/webp")],
)
def test_local_image_returns_data_url(tmp_path, suffix, mime):
    path = tmp_path / f"shot{suffix}"
    path.write_bytes(PNG)
    result = _read(path)
    assert isinstance(result, ToolResult)
    assert result.images == (
        ImageAttachment(
            source=str(path.resolve()),
            mime=mime,
            url=f"data:{mime};base64,{base64.b64encode(PNG).decode()}",
        ),
    )
    assert result.content.startswith("Image read:")


def test_async_image_read_matches_sync(tmp_path):
    path = tmp_path / "shot.PNG"
    path.write_bytes(PNG)
    assert _read_async(path) == _read(path)


def test_image_at_limit_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(read_mod, "MAX_IMAGE_BYTES", len(PNG))
    path = tmp_path / "edge.png"
    path.write_bytes(PNG)
    assert not _read(path).is_error


def test_image_over_limit_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(read_mod, "MAX_IMAGE_BYTES", len(PNG) - 1)
    path = tmp_path / "big.png"
    path.write_bytes(PNG)
    result = _read(path)
    assert result.is_error is True
    assert result.images == ()
    assert result.content.startswith("[Tool Error]:")


def test_http_image_url_passes_through_without_fetch():
    url = "https://example.com/shot.PNG?raw=1"
    result = ReadFile(path=url).run()
    assert result.images == (ImageAttachment(source=url, mime="image/png", url=url),)
    assert result.content.startswith("Image read:")


def test_unsupported_http_url_is_error():
    with pytest.raises(ValueError, match="Unsupported image URL"):
        ReadFile(path="https://example.com/page.html").run()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_read_image.py -v`
Expected: FAIL（`MAX_IMAGE_BYTES` 不存在；图片路径按文本读取失败）。

- [ ] **Step 3: Add constants and helpers**

In `core/tools/base_tools/read.py`:

```python
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
```

Add private helpers:

- `_is_http_url(path: str) -> bool`：`urlsplit(path).scheme.lower() in ("http", "https")`。
- `_image_mime(path: str) -> str | None`：`Path(urlsplit(path).path).suffix.lower()` 查表。
- `_data_url(mime: str, data: bytes) -> str`：`f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"`。
- `_image_result(source: str, mime: str, size: str, url: str) -> ToolResult`：
  content 为 `f"Image read: {source} ({mime}, {size})."`，`images=(ImageAttachment(source=source, mime=mime, url=url),)`。
- `_too_large(source: str, size: int) -> ToolResult`：
  `ToolResult.error(f"Image too large: {source} is {size} bytes (limit {MAX_IMAGE_BYTES} bytes).")`。

- [ ] **Step 4: Branch in `run()` and `arun()`**

Before `anydoc.format_from_path(...)` / text reading:

1. If `_is_http_url(self.path)`:
   - `mime = _image_mime(self.path)`
   - `mime is None` → `raise ValueError(f"Unsupported image URL: {self.path}")`
   - return `_image_result(self.path, mime, "remote URL", self.path)`.
2. Resolve the local path as today. If `_image_mime(self.path)` is not None:
   - `size = fp.stat().st_size`
   - `size > MAX_IMAGE_BYTES` → `_too_large(str(fp), size)`
   - `run()`: `data = fp.read_bytes()`；`arun()`: `async with aiofiles.open(fp, "rb") as f: data = await f.read()`
   - return `_image_result(str(fp), mime, f"{size} bytes", _data_url(mime, data))`.

Keep the existing text/document branch exactly as-is after the image branch. Remove the
`read_image()` stub. Update the `run()` / `arun()` return annotations to `str | ToolResult`.
Update `DESCRIPTION` to mention:
- image extensions and the 5 MB cap;
- `http(s)` image URLs are passed through without downloading;
- `limit` / `offset` only apply to text.

- [ ] **Step 5: Run the tests**

Run:

```bash
uv run pytest tests/test_read_image.py tests/test_read_byte_limit.py -v
```

Expected: PASS（文本 / 截断行为零回归）。

- [ ] **Step 6: Commit**

```bash
git add core/tools/base_tools/read.py tests/test_read_image.py
git commit -m "feat: read_file 支持本地图片与图片 URL"
```

---

### Task 4: Vision capability and step-level image injection

**Files:**
- Modify: `core/client/model.py` (add method after `load_max_output`)
- Modify: `core/agent_step.py:91-150,270-360`
- Modify: `tests/test_agent_step.py` (`_Client` + new image tests)
- Test: `tests/test_model_vision.py` (create)

**Interfaces:**
- Consumes: `to_llm_messages(..., vision=...)` (Task 1), `ImageAttachment` / `ToolResult.images` (Task 2).
- Produces:
  - `ModelClient.supports_vision() -> bool`
  - `_image_followup_message(attachments: list[ImageAttachment], vision: bool) -> dict`
  - `run_agent_step()` passes `vision=client.supports_vision()` to `to_llm_messages()` and `_run_tools()`.

- [ ] **Step 1: Write the failing model test**

Create `tests/test_model_vision.py`:

```python
"""ModelClient.supports_vision 读取当前模型的注册声明。"""
import core.client.model as model


def test_supports_vision_reads_current_model(monkeypatch):
    monkeypatch.setitem(model._MODEL_LIST, "P", {
        "model_list": {
            "v": {"vision": True},
            "novision": {"vision": False},
        },
    })
    client = model.ModelClient.__new__(model.ModelClient)
    client.current_provider = "P"
    client.current_model = "v"
    assert client.supports_vision() is True
    client.current_model = "novision"
    assert client.supports_vision() is False
    client.current_model = ""
    assert client.supports_vision() is False
```

- [ ] **Step 2: Write the failing step tests**

In `tests/test_agent_step.py`:

- Extend the imports (the file already imports `ToolPool`):

```python
from core.template import IMAGE_OMITTED_MESSAGE
from core.tools import ImageAttachment, ToolResult, ToolPool
```

- Extend `_Client` with a configurable vision flag:

```python
class _Client:
    current_provider = "test"

    def __init__(self, vision=True):
        self.vision = vision
        self.kwargs = None
        self.requested = None

    def supports_vision(self):
        return self.vision
```

- Add test tools:

```python
class ImageEcho(BaseTool):
    """Image-producing tool."""
    source: str = "/tmp/a.png"

    def run(self, tctx=None):
        return ToolResult(
            content="image ok",
            images=(ImageAttachment(
                source=self.source,
                mime="image/png",
                url=f"data:image/png;base64,{self.source}",
            ),),
        )


class BrokenImageEcho(BaseTool):
    """Failed image tool."""
    def run(self, tctx=None):
        return ToolResult.error("read failed")
```

- Add tests:

```python
def test_image_followup_comes_after_all_tool_results(monkeypatch):
    outcome = _run(monkeypatch, [("", "", [
        _tool_call("image_echo", '{"source": "/tmp/a.png"}', "t1"),
        _tool_call("echo", '{"value": "b"}', "t2"),
    ], "tool_calls", None)], pool=ToolPool({"image_echo": ImageEcho, "echo": Echo}))

    assert [m["role"] for m in outcome.followup_messages] == ["tool", "tool", "user"]
    assert outcome.followup_messages[0]["tool_call_id"] == "t1"
    content = outcome.followup_messages[2]["content"]
    assert content[0]["type"] == "text"
    assert "1. /tmp/a.png" in content[0]["text"]
    assert content[1] == {"type": "image_url",
                          "image_url": {"url": "data:image/png;base64,/tmp/a.png"}}


def test_image_followup_preserves_call_order(monkeypatch):
    outcome = _run(monkeypatch, [("", "", [
        _tool_call("image_echo", '{"source": "/tmp/a.png"}', "t1"),
        _tool_call("image_echo", '{"source": "/tmp/b.png"}', "t2"),
    ], "tool_calls", None)], pool=ToolPool({"image_echo": ImageEcho}))

    content = outcome.followup_messages[-1]["content"]
    assert "1. /tmp/a.png" in content[0]["text"]
    assert "2. /tmp/b.png" in content[0]["text"]
    assert [part["image_url"]["url"] for part in content[1:]] == [
        "data:image/png;base64,/tmp/a.png",
        "data:image/png;base64,/tmp/b.png",
    ]


def test_images_not_attached_without_vision(monkeypatch):
    outcome = _run(monkeypatch, [("", "", [
        _tool_call("image_echo", '{"source": "/tmp/a.png"}', "t1"),
    ], "tool_calls", None)],
        client=_Client(vision=False), pool=ToolPool({"image_echo": ImageEcho}))

    content = outcome.followup_messages[-1]["content"]
    assert isinstance(content, str)
    assert "/tmp/a.png" in content
    assert "vision" in content.lower()


def test_failed_image_tool_adds_no_image_followup(monkeypatch):
    outcome = _run(monkeypatch, [("", "", [
        _tool_call("broken_image", "{}", "t1"),
    ], "tool_calls", None)], pool=ToolPool({"broken_image": BrokenImageEcho}))

    assert [m["role"] for m in outcome.followup_messages] == ["tool"]


def test_request_view_uses_placeholder_without_vision(monkeypatch):
    client = _Client(vision=False)
    history = [{"role": "user", "content": [
        {"type": "text", "text": "see image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]}]
    _run(monkeypatch, [("ok", "", [], "stop", None)], client=client, history=history)

    assert client.kwargs["messages"][1]["content"] == IMAGE_OMITTED_MESSAGE
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_model_vision.py tests/test_agent_step.py -v
```

Expected: FAIL（图片注入断言失败；现有 `_Client` 仍可工作，因为实现会用 `getattr` 对未知 client 默认 `True`）。

- [ ] **Step 4: Implement `supports_vision()`**

In `core/client/model.py`, after `load_max_output()`:

```python
def supports_vision(self) -> bool:
    """当前模型是否声明支持图像输入；未选择模型时为 False。"""
    if not (self.current_provider and self.current_model):
        return False
    return bool(self._model_config().get("vision", False))
```

- [ ] **Step 5: Implement step collection and injection**

In `core/agent_step.py`:

- Import `ImageAttachment` from `core.tools` and `INJECTION_MESSAGES_TEMPLATE` from `core.template`.
- In `run_agent_step()`, read `vision = bool(getattr(client, "supports_vision", lambda: True)())` once, then:
  - `messages = [{"role": "system", "content": system_prompt}] + to_llm_messages(history, vision=vision)`
  - pass `vision=vision` to `_run_tools()`.
- Change `_run_tools()` signature to accept `vision: bool`.
- Inside `_run_tools()`:
  - keep `attachments: list[ImageAttachment] = []` next to `results` / `injected`.
  - in the收尾段, after `is_error` is known and before dispatch (`output = outcome.content`), do:
    `if not is_error and not outcome.is_cancelled: attachments.extend(outcome.images)`.
  - after the `for group ...` loop finishes (inside the `try`, before `finally`), append:
    `if attachments: injected.append(_image_followup_message(attachments, vision))`.
- Add the builder:

```python
def _image_followup_message(attachments: list[ImageAttachment], vision: bool) -> dict:
    lines = ["read_file loaded the following images for visual inspection (in order):"]
    lines += [f"{index}. {attachment.source}" for index, attachment in enumerate(attachments, 1)]
    if not vision:
        lines.append("[Images not attached: the current model does not support vision input.]")
    text = INJECTION_MESSAGES_TEMPLATE.format(content="\n".join(lines))
    if not vision:
        return {"role": "user", "content": text}  # 纯文本，仍带注入前后缀
    parts = [{"type": "text", "text": text}]
    parts += [{"type": "image_url", "image_url": {"url": attachment.url}}
              for attachment in attachments]
    return {"role": "user", "content": parts}
```

- [ ] **Step 6: Run the tests**

Run:

```bash
uv run pytest tests/test_model_vision.py tests/test_agent_step.py tests/test_agent_turn_wiring.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add core/client/model.py core/agent_step.py tests/test_agent_step.py tests/test_model_vision.py
git commit -m "feat: agent step 统一注入 read_file 图片"
```

---

### Task 5: Session title, fork list, and persistence for array content

**Files:**
- Modify: `core/context/session/session.py:40-45,80-125,216-235`
- Test: `tests/test_session_image_content.py` (create)

**Interfaces:**
- Consumes: `content_text()` / `is_synthetic_content()` (Task 1).
- Produces:
  - array `content` round-trips through `add_message()` / `load_session()`;
  - synthetic user messages never update the title and never appear in `load_user_messages()`;
  - fork titles flatten array content.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_image_content.py`:

```python
"""会话 content 数组：落盘 / 重读、标题、fork 列表与标题。"""
import pytest

import core.context.session.session as session_module
from core.context.session.session import SessionManager
from core.template import INJECTION_MESSAGES_TEMPLATE


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(session_module, "SESSION_INDEX_FILE", tmp_path / "session_index.jsonl")
    return SessionManager()


def _image_content(text="images"):
    return [
        {"type": "text", "text": INJECTION_MESSAGES_TEMPLATE.format(content=text)},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]


def test_image_content_round_trips(manager):
    manager.new_session()
    manager.add_message({"role": "user", "content": "look"})
    manager.add_message({"role": "user", "content": _image_content()})
    session_id = manager.current_session

    manager.reset()
    reloaded = manager.load_session(session_id)

    assert reloaded is not None
    assert reloaded.messages[-1].content == _image_content()


def test_synthetic_image_message_does_not_overwrite_title(manager):
    manager.new_session()
    manager.add_message({"role": "user", "content": "fix sidebar"})
    manager.add_message({"role": "user", "content": _image_content()})

    assert manager.session_map[manager.current_session].title == "fix sidebar"


def test_fork_list_excludes_synthetic_user_messages(manager):
    manager.new_session()
    manager.add_message({"role": "user", "content": "real request"})
    manager.add_message({"role": "user", "content": _image_content()})
    manager.add_message({"role": "user",
                         "content": INJECTION_MESSAGES_TEMPLATE.format(content="cron")})
    manager.add_message({"role": "user",
                         "content": "<compacted_messages>\nsummary\n</compacted_messages>"})

    assert [m.content for m in manager.load_user_messages()] == ["real request"]


def test_fork_title_flattens_array_content(manager):
    manager.new_session()
    manager.add_message({"role": "user", "content": "first"})
    manager.add_message({"role": "user",
                         "content": [{"type": "text", "text": "look at\nthis shot"}]})
    messages = manager.session_map[manager.current_session].messages

    forked = manager.fork_session(messages[1].id)

    assert forked is not None
    assert forked.title == "[Fork] look at this shot"


def test_array_user_content_sets_title(manager):
    manager.new_session()
    manager.add_message({"role": "user",
                         "content": [{"type": "text", "text": "describe this"}]})

    assert manager.session_map[manager.current_session].title == "describe this"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session_image_content.py -v`
Expected: FAIL with `AttributeError: 'list' object has no attribute 'startswith'` 或标题断言失败。

- [ ] **Step 3: Implement session changes**

In `core/context/session/session.py`, import `content_text` and `is_synthetic_content` from
`core.context.message`.

- `_fork_title(content: str)` → `content: Any`; start from `content_text(content)`.
- `_update_session_title(user_query: str)` → `content: Any`:
  - `text = content_text(content)`
  - `if is_synthetic_content(text): return`
  - `title = _title_truncate(text)`
- `load_user_messages()`: keep the existing empty-session `None` return; filter the list with
  `message.role == "user" and not is_synthetic_content(message.content)`.

- [ ] **Step 4: Run the tests**

Run:

```bash
uv run pytest tests/test_session_image_content.py tests/test_fork_session.py tests/test_session_contract.py tests/test_session_reload.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/context/session/session.py tests/test_session_image_content.py
git commit -m "feat: 会话支持数组 content 并隔离合成消息"
```

---

### Task 6: TUI compatibility for array content

**Files:**
- Modify: `core/tui/render.py:78-80,152-160`
- Modify: `core/tui/commands.py:95`
- Modify: `core/tui/screens/fork.py:15-17`
- Test: `tests/test_tui_image_content.py` (create)
- Test: `tests/test_ui_fork.py` (add one test)

**Interfaces:**
- Consumes: `content_text()` / `is_injected_content()` (Task 1).
- Produces: TUI replay skips injected image messages; user cards, fork previews, and fork prompt backfill flatten arrays.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tui_image_content.py`:

```python
"""TUI 对 content 数组的兼容：注入消息跳过、用户卡片压平、fork 预览压平。"""
import core.tui.render as render
from core.context.message import Message
from core.context.session.session import Session
from core.template import INJECTION_MESSAGES_TEMPLATE
from core.tui.screens.fork import _preview


class _App:
    def __init__(self):
        self.cards = []

    def _add_card(self, kind, body, **kwargs):
        self.cards.append((kind, body))


def test_render_user_input_flattens_array(monkeypatch):
    app = _App()
    monkeypatch.setattr(render, "_exec", lambda fn: fn(app))

    render.render_user_input([
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "x"}},
    ])

    assert app.cards[0][0] == "user"
    assert "look" in app.cards[0][1].plain


def test_replay_skips_injected_image_message(monkeypatch):
    events = []
    monkeypatch.setattr(render, "render_user_input",
                        lambda text: events.append(("user", text)))
    monkeypatch.setattr(render, "render_assistant_response",
                        lambda text: events.append(("assistant", text)))
    session = Session(messages=[Message(role="user", content=[
        {"type": "text", "text": INJECTION_MESSAGES_TEMPLATE.format(content="images")},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ])])

    render.render_session_history(session)

    assert events == []


def test_preview_flattens_array_content():
    assert _preview([{"type": "text", "text": "a\nb   c"}]) == "a b c"
```

Add to `tests/test_ui_fork.py`:

```python
def test_fork_pick_flattens_array_content_into_prompt(monkeypatch):
    events = []
    app, prompt, pushed = _app(monkeypatch, events)
    session = SimpleNamespace(messages=["m1", "a1"])
    messages = [
        _Message("m1", [{"type": "text", "text": "first"}]),
        _Message("m2", [{"type": "text", "text": "second"}]),
    ]
    app._manager = _FakeManager(messages, forked_session=session)

    app._open_fork()
    pushed[0][1]("m2")

    assert prompt.text == "second"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_tui_image_content.py tests/test_ui_fork.py -v
```

Expected: FAIL（`_preview` / `render_user_input` 对 list 调 `split` / `Text` 报错）。

- [ ] **Step 3: Implement TUI changes**

In `core/tui/render.py`:

- Import `content_text` and `is_injected_content` from `core.context.message`.
- `_is_injected_message(content)` → `return is_injected_content(content)`.
- `render_user_input(user_text: Any)` → `Text(_strip_keycaps(content_text(user_text)), ...)`.

In `core/tui/commands.py`:

- Import `content_text`.
- Change the fork backfill line to `content = content_text(next((m.content for m in messages if m.id == message_id), ""))`.

In `core/tui/screens/fork.py`:

- Import `content_text`.
- `_preview(content: Any)` starts with `text = " ".join(content_text(content).split())`.

- [ ] **Step 4: Run the tests**

Run:

```bash
uv run pytest tests/test_tui_image_content.py tests/test_ui_fork.py tests/test_render_session_history.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/tui/render.py core/tui/commands.py core/tui/screens/fork.py \
  tests/test_tui_image_content.py tests/test_ui_fork.py
git commit -m "feat: TUI 兼容数组 content 并跳过图片注入"
```

---

### Task 7: Vision-gated compaction requests

**Files:**
- Modify: `core/context/compact/context_compact.py:150-165`
- Test: `tests/test_compact_vision.py` (create)

**Interfaces:**
- Consumes: `to_llm_messages(..., vision=...)` (Task 1), `ModelClient.supports_vision()` (Task 4).
- Produces: `summarize_history()` builds its request with the current client's vision capability.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compact_vision.py`:

```python
"""压缩请求也走视觉门控：非视觉模型下整条图片消息变纯文本占位。"""
import asyncio

import core.context.compact.context_compact as compact_mod
from core.context.message import Message
from core.template import IMAGE_OMITTED_MESSAGE, INJECTION_MESSAGES_TEMPLATE


class _Stream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        pass


class _Client:
    def __init__(self, vision):
        self.vision = vision
        self.kwargs = None

    def supports_vision(self):
        return self.vision

    def clamp_max_tokens(self, requested):
        return requested

    def get_model_client(self):
        async def create(**kwargs):
            self.kwargs = kwargs
            return _Stream()
        return create


def _history():
    return [Message(role="user", content=[
        {"type": "text", "text": INJECTION_MESSAGES_TEMPLATE.format(content="img")},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ])]


def _summary_request(monkeypatch, vision):
    client = _Client(vision)
    monkeypatch.setattr(compact_mod, "shared_model_client", lambda: client)
    asyncio.run(compact_mod.summarize_history(_history()))
    return client.kwargs["messages"][0]


def test_summary_replaces_image_message_without_vision(monkeypatch):
    assert _summary_request(monkeypatch, False) == {
        "role": "user",
        "content": IMAGE_OMITTED_MESSAGE,
    }


def test_summary_keeps_image_message_with_vision(monkeypatch):
    message = _summary_request(monkeypatch, True)
    assert message["content"][0]["type"] == "text"
    assert message["content"][1]["type"] == "image_url"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compact_vision.py -v`
Expected: FAIL（非视觉用例仍拿到数组 / `content` 不是占位字符串）。

- [ ] **Step 3: Wire vision into the summary request**

In `summarize_history()`:

- Move the client selection above `request_messages`.
- Build `vision = bool(getattr(client, "supports_vision", lambda: True)())` and
  `request_messages = to_llm_messages(messages, vision=vision) + [...]`.
- The `getattr` default keeps existing test doubles (which only implement `get_model_client` /
  `clamp_max_tokens`) working; real `ModelClient` always provides the method.

- [ ] **Step 4: Run the tests**

Run:

```bash
uv run pytest tests/test_compact_vision.py tests/test_compact_no_model.py tests/test_compact_without_ctx.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/context/compact/context_compact.py tests/test_compact_vision.py
git commit -m "feat: 压缩请求按当前模型视觉能力裁剪"
```

---

### Task 8: Documentation updates and full regression

**Files:**
- Modify: `README.md`
- Modify: `docs/tools.md`
- Modify: `docs/architecture.md`
- Modify: `docs/context-compaction.md`
- Modify: `GLOSSARY.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Update the feature descriptions**

- `README.md`：「工具系统」条目补一句 `read_file` 支持本地图片（5 MB / png·jpg·jpeg·gif·webp）与图片 URL，图片经 user 消息注入视觉模型。
- `docs/tools.md`：更新 `read_file` 行与说明段，写清图片分支、URL 透传、5 MB 限制、`limit`/`offset` 仅对文本有效、失败为工具错误。
- `docs/architecture.md`：在 Message / 请求视图相关段落说明 `content` 可以是数组，图片只在 user 消息、`to_llm_messages(..., vision=...)` 负责非视觉模型占位。
- `docs/context-compaction.md`：写清图片 token 固定按 1000 / 图估算、不按尺寸精确计算、非视觉模型下请求占位。
- `GLOSSARY.md`：`Message` 词条补 content 数组；新增 `ImageAttachment` 词条（source / mime / url）。

- [ ] **Step 2: Run the full test suite**

Run:

```bash
uv run pytest tests/ -q
uv run python -m core.tui.ui_textual --smoke
```

Expected: 全部 PASS；smoke 输出无异常。若有既有失败，先确认与本计划无关再记录。

- [ ] **Step 3: Commit**

```bash
git add README.md docs/tools.md docs/architecture.md docs/context-compaction.md GLOSSARY.md
git commit -m "docs: 补充图像输入能力说明"
```
