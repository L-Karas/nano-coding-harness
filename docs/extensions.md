# 自定义 Extension 指南

外部 extension 让你**不修改 harness 源码**，就能在四个关键节点介入 agent 执行：

- LLM 调用前（`before_llm`）/ 后（`after_llm`）
- 工具调用前（`before_tool`）/ 后（`after_tool`）

扩展是运行在 agent 进程内的普通 Python 包，启动时从工作目录下的 `.harness/extensions/` 加载。它可以观察数据、原地修改数据，也可以拦截 LLM 调用或工具执行、注入额外消息。

> 源码：`core/extension/`（`api.py` / `context.py` / `dispatcher.py` / `loader.py`）
> 完整设计契约：[`superpowers/specs/2026-10-08-extension-hooks-design.md`](superpowers/specs/2026-10-08-extension-hooks-design.md)

- [1. 快速开始](#1-快速开始)
- [2. 加载规则](#2-加载规则)
- [3. API 参考](#3-api-参考)
- [4. 执行语义](#4-执行语义)
- [5. 实战配方](#5-实战配方)
- [6. 排错](#6-排错)
- [7. 测试扩展](#7-测试扩展)
- [8. 安全与边界](#8-安全与边界)

## 1. 快速开始

### 1.1 目录结构

每个扩展是一个包含 `extension.json` 与 `__init__.py` 的文件夹，放在 `.harness/extensions/` 下：

```
.harness/extensions/
  my_ext/
    extension.json     # 启用开关：{"enabled": true}
    __init__.py        # 入口：必须定义 register(api)
    helpers.py         # 可选：同包模块，包内导入必须用相对导入
  disabled_ext/
    extension.json     # {"enabled": false} → 不加载
    __init__.py
```

### 1.2 最小示例

`extension.json`：

```json
{
    "enabled": true
}
```

`__init__.py`：

```python
"""最小扩展：注册一个同步回调与一个异步回调。"""


def register(api):
    api.on("before_llm", on_before_llm)
    api.on("after_tool", on_after_tool)


def on_before_llm(ctx):
    # 给本轮模型调用追加一条提醒；只影响本次调用，不写回会话历史。
    ctx.messages.append({"role": "user", "content": "[Reminder] 回答前先确认文件是否存在。"})


async def on_after_tool(ctx):
    # 在工具结果末尾追加标记。
    ctx.result = f"{ctx.result}\n[my_ext] 已处理"
```

### 1.3 冒烟验证

仓库自带可直接复制的最小示例：

```bash
mkdir -p .harness/extensions
cp -r examples/extension_example .harness/extensions/extension_example
```

**重启进程**后（扩展在启动时加载一次），正常发起一轮对话，即可在模型输入或工具结果中看到示例扩展追加的标记。也可以直接观察日志：

```bash
tail -f .harness/log/loader.log       # 加载 / 清单门槛相关日志
tail -f .harness/log/dispatcher.log   # hook 运行异常日志
```

## 2. 加载规则

启动流程为 `bootstrap()` → `load_extensions()`，加载器逐个扫描扩展目录：

| 规则 | 行为 |
| --- | --- |
| 扫描范围 | `.harness/extensions/` 的**直接子文件夹**（不递归）；文件与非文件夹忽略 |
| 跳过前缀 | 名称以 `_` 开头的文件夹跳过（如 `_draft/`） |
| 启用门槛 | `extension.json` 中 `enabled` **严格为布尔 `true`**（Python 的 `is True`）才加载 |
| 不启用的情况 | `enabled` 为 `false`、缺失、`"true"`、`1`、`null`、清单缺失、JSON 非法 → 一律按不启用处理并记日志 |
| 入口 | 固定为 `__init__.py`，以包方式加载（模块名 `nano_extension_<文件夹名>`）；扩展目录不在 `sys.path` 上，包内多文件**必须用相对导入**（`from .helpers import x`） |
| 注册入口 | 必须定义可调用的 `register(api)`；缺失或不认识 `api.on` 的非法参数 → 该扩展整体作废 |
| 原子性 | `api.on()` 只缓冲注册；`register(api)` **正常返回后**才一次性提交全部注册。中途抛错 → 该扩展零注册 |
| 失败隔离 | 单个扩展导入失败 / 注册失败只影响自己并记日志，不阻断启动，也不影响其它扩展 |
| 加载顺序 | 文件夹名字典序；同一扩展内按 `api.on()` 调用顺序 |
| 幂等 | 进程内 `load_extensions()` 只生效一次 |
| 生效时机 | 启动时读取一次；修改 `enabled` 或扩展代码后**必须重启进程** |

`.harness/extensions/` 相对于**启动进程时的工作目录**（`WORKDIR`）。扩展目录已被 `.gitignore` 忽略，扩展代码不进版本控制。

> **包内导入规则**：扩展目录**不在 `sys.path` 上**（加载器刻意如此，避免与标准库/项目包重名），因此扩展包内部的多文件组织**必须使用相对导入**（如 `from .helpers import x`、`from .subpkg import y`）；裸绝对导入（`import helpers`）不会命中扩展目录，会导致 `ModuleNotFoundError` 或误命中同名模块。harness 内部模块（`core.*`）与第三方包不受此限制，照常绝对导入。

## 3. API 参考

### 3.1 `register(api)` 与 `api.on()`

```python
def register(api):
    api.on(event, callback)   # event ∈ EVENTS，callback 为同步或异步可调用对象
```

- `event` 取值：`before_llm` / `after_llm` / `before_tool` / `after_tool`；未知事件名抛 `ValueError`。
- `callback` 非 callable 抛 `TypeError`。
- `register` 内抛出的任何异常（包括上面两种）都会让**整个扩展作废**，因此建议只做注册、把逻辑放在独立函数里。
- 回调签名固定为一个上下文对象：`def hook(ctx)` 或 `async def hook(ctx)`；返回值会被忽略，只通过修改 `ctx` 生效。

### 3.2 上下文对象

四个事件各对应一个普通 dataclass。**加粗字段可写**，其余只读：

| 事件 | 上下文对象（构造参数） | 可写字段 | 控制方法 |
| --- | --- | --- | --- |
| `before_llm` | `BeforeLLMContext(messages, tools, max_tokens)` | **`messages`**（统一为即将发出的完整请求视图：`[system] + to_llm_messages(history)`）、**`tools`**、**`max_tokens`** | `ctx.abort(reason)` |
| `after_llm` | `AfterLLMContext(content, tool_calls, finish_reason="", usage=None)` | **`content`**、**`tool_calls`**、**`inject_messages`** | 无 |
| `before_tool` | `BeforeToolContext(tool_name, args)` | **`args`** | `ctx.block(reason)` |
| `after_tool` | `AfterToolContext(tool_name, args, result, is_error=False)` | **`result`**、**`inject_messages`** | 无 |

补充说明：

- `messages` / `tool_calls` / `inject_messages` 中的消息是普通 dict（`{"role": ..., "content": ...}`）；`before_llm` 里的消息就是即将发出的请求消息（已裁剪），追加的消息需是 API 可接受的普通 dict，不会再被二次裁剪。
- `tool_calls` 在无工具调用时为空列表（不会为 `None`）。
- `abort()` / `block()` 的原因参数会做 `str()` 归一；`abort_reason` / `block_reason` 字段可读。
- `finish_reason`、`usage`、`tool_name`、`is_error` 为只读信息，供回调判断，不要写入。

### 3.3 事件触发时机

| 事件 | 触发时机（`core/agent_step.py`，main / sub-agent / teammate 相同） |
| --- | --- |
| `before_llm` | 请求视图（system + history 裁剪）构造后、发出前 |
| `after_llm` | 流式响应解析后、工具执行前；`finish_reason == "length"` 也会触发，扩窗 / 续写恢复由外层决定 |
| `before_tool` | 工具参数 JSON 解析后、permission hook（`pre_tool_call`）之前 |
| `after_tool` | 工具真正执行完成后、结果写入消息前 |

## 4. 执行语义

### 4.1 顺序与链式修改

多个回调（同一扩展内、不同扩展间）按「文件夹字典序 → 注册顺序」串行执行，共享同一个上下文对象：

```
扩展 A 的 hook1 → 扩展 A 的 hook2 → 扩展 B 的 hook1 …
```

后一个回调能看到前一个对 `ctx` 的修改（例如 A 改了 `ctx.args`，B 读到的是改后的值）。

### 4.2 短路控制

- `ctx.block(reason)`（`before_tool`）：跳过**该事件剩余回调**、跳过权限检查、跳过工具执行；工具结果写为 `[Extension blocked] <reason>`。
- `ctx.abort(reason)`（`before_llm`）：跳过该事件剩余回调，不再调用 LLM。
  - 主循环：写入一条 `[Extension aborted] <reason>` 的 assistant 消息并结束本回合；
  - 子代理：返回文本 `(subagent aborted by extension: <reason>)`。

被 `block` 拦截的工具调用**不会**触发 `after_tool`，但下一轮模型会看到 `[Extension blocked] ...` 工具结果。

### 4.3 fail-open 与中断

- 每个回调独立 `try/except Exception`：单个回调抛异常只记日志（`dispatcher.log`）并跳过，后续回调与主流程继续。
- `asyncio.CancelledError` 不会被捕获：用户中断（Esc）能正常取消正在执行的异步回调。
- **没有超时机制**：同步回调卡住会卡住整个 agent，异步回调里的阻塞 IO 也会阻塞事件循环。见 [8. 安全与边界](#8-安全与边界)。

### 4.4 字段归一与注入消息

事件派发结束后、写回主流程之前，派发器会做最小防御性归一：

| 字段 | 规则 |
| --- | --- |
| `result` | 一律 `str()` 后写回 |
| `content` | 非 `None` 时 `str()`（`None` 表示无文本的 tool-call 响应，保留） |
| `tool_calls` / `inject_messages` | 必须是 `list`；不是则置空并记 ERROR |

其余字段的类型契约由扩展自行保证，写坏类型可能在主循环抛出（属于扩展 bug）。

`inject_messages` 的插入位置：

- `after_llm`：在 assistant 消息之后插入。**tool-calling 轮次中它会落在 assistant `tool_calls` 与 tool 结果之间**，部分对消息邻接性校验严格的 provider 可能返回 400；这类轮次建议改用 `after_tool` 注入。
- `after_tool`：在该批全部工具结果写回之后追加，位置安全。

```python
def on_after_tool(ctx):
    if ctx.tool_name == "read_file" and not ctx.is_error:
        ctx.inject_messages.append(
            {"role": "user", "content": f"[审计] 已读取 {ctx.args.get('path')}"}
        )
```

### 4.5 会话与生命周期

- **主循环**：`before_llm` 对 `messages` 的修改只用于本次模型调用。会话早在派发前已回写，回调中的增删不会持久化，下一回合会重新从会话加载。
- **子代理**：`before_llm` 的修改以及 `after_llm` / `after_tool` 注入的消息会保留在该子代理的本地消息列表中，参与其后续轮次；但子代理全程不进主会话历史（只回传最终文本结论）。
- `after_llm` 把 `tool_calls` 清空（置为 `[]`）：本回合直接以当前 `content` 结束，不再执行工具。
- 扩展可在模块级保存状态（进程内单例），跨回合复用；进程重启后清零。

## 5. 实战配方

以下代码均放在扩展包的 `__init__.py`（或同包模块）中，并在 `register(api)` 里注册。

### 5.1 注入提示 / 上下文（`before_llm`）

```python
def register(api):
    api.on("before_llm", inject_reminder)


def inject_reminder(ctx):
    # 只影响本次调用，不污染会话历史。
    ctx.messages.append({"role": "user", "content": "[Reminder] 不要执行未经验证的危险命令。"})
```

### 5.2 改写工具参数（`before_tool`）

```python
def force_git_no_pager(ctx):
    if ctx.tool_name != "terminal":
        return
    command = ctx.args.get("command", "")
    if command.startswith("git ") and "--no-pager" not in command:
        ctx.args["command"] = "git --no-pager " + command[len("git "):]
```

修改后的参数会回写到 `tool_call.function.arguments`，并用于渲染和**后续的权限检查**（权限门看到的是最终参数）。

### 5.3 拦截危险工具调用（`before_tool` + `block`）

```python
import re

_DANGEROUS = re.compile(r"\brm\s+-[a-z]*[rf]|\bmkfs\b|\bshutdown\b")


def guard_dangerous_commands(ctx):
    if ctx.tool_name == "terminal" and _DANGEROUS.search(ctx.args.get("command", "")):
        ctx.block("危险命令已被扩展拦截")
```

模型下一轮收到的工具结果为 `[Extension blocked] 危险命令已被扩展拦截`。

### 5.4 结果脱敏 / 改写（`after_tool`）

```python
import re

_SECRET = re.compile(r"sk-[A-Za-z0-9]{8,}")


async def redact_secrets(ctx):
    if not ctx.is_error:
        ctx.result = _SECRET.sub("sk-***", ctx.result)
```

`is_error=True` 表示工具自身报了错（结果以 harness 错误前缀开头），可按需跳过处理。

### 5.5 审计日志（`after_tool`）

```python
import json
from datetime import datetime
from pathlib import Path


def audit_tool_call(ctx):
    path = Path(".harness") / "log" / "my_ext_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "time": datetime.now().isoformat(timespec="seconds"),
            "tool": ctx.tool_name,
            "args": ctx.args,
            "is_error": ctx.is_error,
        }, ensure_ascii=False) + "\n")
```

注意：hook 是在主流程里被 `await` 的，**同步文件 IO 会卡住 agent**；高频场景请异步写或走队列。

### 5.6 中止本回合（`before_llm` + `abort`）

```python
def stop_on_policy(ctx):
    last_user = next((m for m in reversed(ctx.messages) if m.get("role") == "user"), None)
    if last_user and "禁止发送" in (last_user.get("content") or ""):
        ctx.abort("内容策略命中")
```

主循环会写入 assistant 消息 `[Extension aborted] 内容策略命中` 并结束本回合；子代理则返回 `(subagent aborted by extension: 内容策略命中)`。

## 6. 排错

### 6.1 日志位置

| 日志文件 | 内容 |
| --- | --- |
| `.harness/log/loader.log` | 加载成功（INFO，含扩展名与注册回调数） / 清单缺失 / JSON 非法 / `enabled` 非严格 `true` / 导入失败 / 缺少 `register` / 注册中途抛错，前缀 `[Extension]` |
| `.harness/log/dispatcher.log` | 每次 hook 执行成功（DEBUG，含模块名、函数名与事件名） / hook 运行期的异常（fail-open，ERROR） |

### 6.2 常见「扩展没生效」原因

1. **忘了重启进程**：`enabled` 与代码只在启动时读取一次。
2. **`enabled` 不是严格布尔 `true`**：`"true"`、`1`、`"yes"` 都不加载。
3. **文件夹以 `_` 开头**：会被跳过。
4. **缺少 `__init__.py` 或 `register`**：该扩展被跳过，检查 `loader.log`。
5. **`register` 里抛错**：例如 `api.on("before_llm", "not-callable")`（`TypeError`）或事件名拼写错误（`ValueError`）。原子提交意味着此时**该扩展零注册**，其它扩展不受影响。
6. **写错了目录**：必须是**启动进程时工作目录**下的 `.harness/extensions/`，且只扫描直接子文件夹。
7. **回调异常被 fail-open 吞掉**：看 `dispatcher.log`；`inject_messages` 写成非 list 也会被丢弃并记 ERROR。
8. **包内模块用了裸绝对导入**：`import helpers` / `from helpers import x` 不会命中扩展目录（扩展目录不在 `sys.path`），必须写成 `from .helpers import x`；此时扩展整体加载失败，见 `loader.log`。

## 7. 测试扩展

可以不经加载器，直接用派发器注册回调做单元测试；用 `reset_extensions()`（或 `dispatcher.reset()`）隔离用例：

```python
import asyncio

import pytest

from core.extension import reset_extensions
from core.extension.context import BeforeToolContext
import core.extension.dispatcher as dispatcher


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_extensions()
    yield
    reset_extensions()


def test_blocks_rm_commands():
    from my_extension import guard_dangerous_commands  # 回调需位于可导入模块；仅存在于扩展包内的写法见下段

    dispatcher.add("before_tool", guard_dangerous_commands, "my_ext")
    ctx = BeforeToolContext(tool_name="terminal", args={"command": "rm -rf /"})
    asyncio.run(dispatcher.dispatch("before_tool", ctx))
    assert ctx.blocked
    assert ctx.block_reason == "危险命令已被扩展拦截"
```

想要连加载门槛一起验证（`enabled`、相对导入、原子性），可参考 `tests/test_extension_loader.py` 的做法：把扩展目录复制到临时目录，再调用 `load_extensions(tmp_path)`，用 `dispatcher._HANDLERS` 断言注册数量。

可测试性导出：`core.extension` 提供 `EVENTS`、四个上下文类、`dispatch`、`add`、`load_extensions`、`reset_extensions`。

## 8. 安全与边界

- **信任模型**：扩展在 agent 进程内以**完整进程权限**运行，可以 import harness 内部模块、读写任意文件。只放可信代码。
- **无超时**：hook 同步阻塞会阻塞 agent；长耗时的网络 / 磁盘 / 计算请自行异步化或降级为快速操作。
- **不阻断启动**：单个扩展加载失败只记日志并跳过。
- **修改需重启**：`enabled` 与扩展代码不支持运行时热切换。
- **覆盖范围**：主 agent 循环与子代理循环都会触发四个事件。
- **不在覆盖范围**：
  - 后台任务的执行结果不触发 `after_tool`（派发时的 `before_tool` 已触发）；
  - 上下文压缩、标题生成等辅助 LLM 调用不触发 `before_llm` / `after_llm`；
  - 旧 `.hooks.json` 教学示例与旧事件系统相互独立，不参与加载；工具节点顺序固定为「外部 `before_tool` → 参数回写 → 旧权限门 → 执行 → 外部 `after_tool`」；
  - 被扩展 / 权限拦截、后台任务占位、中断占位、参数 JSON 解析失败等路径没有真实执行结果，不触发 `after_tool`。

## 参考

- 设计契约：[`superpowers/specs/2026-10-08-extension-hooks-design.md`](superpowers/specs/2026-10-08-extension-hooks-design.md)
- 实现源码：[`core/extension/`](../core/extension/)（`api.py`、`context.py`、`dispatcher.py`、`loader.py`）
- 最小示例：[`examples/extension_example/`](../examples/extension_example/)
- 进阶示例（`after_tool` 读取并按需改写 `web_search` 结果）：[`examples/jev-for-web-search/`](../examples/jev-for-web-search/)
- 主循环接线：[`core/loop_with_interrupt.py`](../core/loop_with_interrupt.py)
- 子代理接线：[`core/sub_agent.py`](../core/sub_agent.py)
