# 外部扩展 Hook 系统设计（nano-harness）

- 日期：2026-10-08
- 状态：待评审
- 关联代码：`core/hook/`、`core/loop_with_interrupt.py`、`core/sub_agent.py`、`core/bootstrap.py`、`core/config.py`

## 1. 背景与目标

Agent 执行流程的关键节点（LLM 调用前/后、工具调用前/后）需要一个扩展机制：处理逻辑位于 harness 源码之外的目录，由用户自行编写，在不修改（或仅少量增量接线）原有代码的前提下介入执行。

已确认的设计决策：

| 决策点 | 结论 |
|---|---|
| 核心改动幅度 | 允许少量增量接线：补 4 个触发点 + 加载器；处理逻辑全部在外部目录 |
| 能力级别 | 观察 + 修改数据 + 控制流（拦截、注入消息、触发额外动作） |
| 运行形式 | 进程内 Python 模块，启动时用 `importlib` 载入 |
| 失败语义 | fail-open：单个 hook 异常只记日志并跳过，不阻断主流程 |
| 覆盖范围 | 主 agent 循环 + 子代理循环 |
| 目录约定 | `.harness/extensions/<扩展名>/`，每个扩展一个文件夹、自带启用标志 |
| 启用切换 | 启动时读取一次；修改后需重启进程生效 |
| 与旧 hook 的关系 | 不动 `core/hook/hook.py`；旧 registry 继续承担权限门与日志，新系统独立 |

## 2. 非目标（本次不做）

- 后台任务执行结果（`core/background_task.py`）不触发 `after_tool`；派发时的 `before_tool` 已触发。
- 上下文压缩、标题生成等辅助 LLM 调用不触发 `before_llm`/`after_llm`。
- 旧 `.hooks.json`（教学示例用）保持现状，不在本次范围。
- 不支持子进程/命令式扩展（已选进程内 Python）。
- 不支持运行时热切换 `enabled`。
- 只扫描 `.harness/extensions/` 的直接子文件夹，不递归嵌套目录。

## 3. 目录结构与扩展清单

```
.harness/extensions/
  extension1/
    extension.json     # {"enabled": true}
    __init__.py        # 入口，必须定义 register(api)
    helpers.py         # 可选，同包模块（必须相对导入）
  extension2/
    extension.json     # {"enabled": false} → 跳过
    __init__.py
```

- `.harness/` 已被 `.gitignore` 忽略，扩展代码不进入版本控制。
- 扫描直接子文件夹，跳过 `_` 开头文件夹。
- 清单 `extension.json` 只含 `enabled` 布尔字段：
  - `false` → 跳过（DEBUG 日志）；
  - 字段缺失、清单缺失、JSON 非法 → 一律按**不启用**处理（INFO/ERROR 日志）。没有明确 `true` 就不执行代码。
- 入口固定为 `__init__.py`，以包方式加载，允许扩展内部多文件组织（`from .helpers import x`）：
  - `spec_from_file_location("nano_extension_<文件夹名>", folder / "__init__.py", submodule_search_locations=[str(folder)])`；
  - 执行前注册进 `sys.modules`，失败时清理，避免半加载状态；
  - 不把扩展目录加入 `sys.path`，避免与标准库/项目包重名；因此扩展包内部的模块导入必须使用相对形式（`from .helpers import x`），裸绝对导入（`import helpers`）不可用。
- 加载失败（导入错误、缺少 `register`、`register` 抛错）→ 整扩展作废并记日志，不阻断启动。
- 注册原子性：`api.on()` 先缓冲，`register(api)` 正常返回后才把该模块的全部注册一次性提交；中途抛错则零注册。
- `load_extensions()` 进程内幂等；`reset_extensions()` 供测试清空注册表。

## 4. 扩展 API 与事件契约

### 4.1 注册入口

```python
# .harness/extensions/my_ext/__init__.py
def register(api):
    api.on("before_llm", on_before_llm)   # api.on(event, callback)
    api.on("after_tool", on_after_tool)

async def on_before_llm(ctx):             # 同步/异步回调都支持
    ctx.messages.append({"role": "user", "content": "[Reminder] ..."})
```

- 事件名：`before_llm` / `after_llm` / `before_tool` / `after_tool`（与旧 `pre_llm_call` 等刻意区分）。
- `api.on()` 对未知事件名抛 `ValueError`（按模块注册失败处理）。
- 同一模块内按 `on()` 调用顺序执行；模块间按文件夹名字典序加载。
- 支持 sync/async 回调：`inspect.iscoroutinefunction` 判断后 await。

### 4.2 上下文对象

普通 dataclass，**加粗**字段可写，其余只读：

| 事件 | 字段 | 控制方法 |
|---|---|---|
| `before_llm` | **messages**（本轮发给模型、不含 system）、**tools**、**max_tokens** | `ctx.abort(reason)`：本回合不再调 LLM，以带原因的消息结束 |
| `after_llm` | **content**、**tool_calls**、**inject_messages**；只读 `finish_reason`、`usage` | 无（没有可拦截对象） |
| `before_tool` | 只读 `tool_name`、**args** | `ctx.block(reason)`：跳过执行，原因写回为工具结果 |
| `after_tool` | 只读 `tool_name`、`args`、`is_error`、**result**、**inject_messages** | 无 |

语义说明：

- `before_llm` 的修改**只影响本次调用**，不自动回写会话历史；需要持久化由扩展自行决定（进程内可达）。
- `inject_messages` 的 dict 在主消息写入会话后追加（主循环进 `SESSION_MANAGER`，子代理进本地 `messages`）。
- 多个 hook 共享同一 context，按注册顺序链式执行，后一个能看到前一个的修改。
- `after_llm` 的 `tool_calls` 始终为列表（无工具调用时为空列表）。
- `block`/`abort` 短路：跳过该事件剩余 hook。

### 4.3 派发语义

- 每个 hook 独立 `try/except Exception`（fail-open）：记 `nano_extension_<文件夹>:<函数> 在 <事件> 失败` + traceback，继续下一个。
- 不捕获 `BaseException`/`CancelledError`，保证 Esc 中断能正常取消异步 hook。
- 无超时机制：进程内同步 hook 卡住会卡住 agent；信任模型见第 6 节。
- 最小数据防御：`result` 写回前做 `str()` 归一；`content` 非 `None` 时归一为 `str`（`None` 保留，表示无文本的 tool-call 响应）；`inject_messages` 非 list 则丢弃并记 ERROR。其余字段类型契约由扩展自负。

## 5. 核心接线

### 5.1 `core/config.py`（+1 行）

```python
EXTENSION_DIR = HARNESS_CONFIG_DIR / "extensions"
```

与 `SKILL_DIR`、`MEMORY_DIR` 等并列。

### 5.2 `core/bootstrap.py`（+2 处）

- `_CONFIG_DIRS` 加入 `EXTENSION_DIR`（只创建根目录，不创建子扩展目录）。
- `bootstrap()` 顺序：`install_default_hooks()` → `load_extensions()` → `scan_skills()` → `load_model_registry()` → `start_cron_scheduler()`；函数内导入，与现有重依赖隔离写法一致。

### 5.3 主循环 `core/loop_with_interrupt.py`

| 节点 | 位置 | 语义 |
|---|---|---|
| `before_llm` | `run()`：`prepare_messages`/`update_messages`/`assemble_tool_pool` 之后、`call_llm` 之前 | hook 修改 `messages/tools/max_tokens` 直接用于本次调用；`abort(reason)` → 写入 `[Extension aborted] reason` 的 assistant 消息、UI 通知并结束回合 |
| `after_llm` | `run()`：`finish_reason` 重试判断之后、写会话之前 | 只对完整响应触发；`finish_reason == "length"` 的升级重试与恢复路径不触发。应用 `content`/`tool_calls` 后写会话；清空 `tool_calls` 即结束回合；`inject_messages` 在 assistant 消息后写入 |
| `before_tool` | `call_tools()`：参数解析后、现有权限 hook 前 | 参数改动回写到 `tool_call.function.arguments` 并用于渲染；**再**跑 `trigger_hooks("pre_tool_call")`，保证权限检查看到最终参数；`block(reason)` → 结果写为 `[Extension blocked] reason`，跳过权限检查与执行 |
| `after_tool` | `call_tools()`：执行完成、结果写回前 | 仅对真正执行过的工具触发（含工具自身报错，`is_error=True`）；`result` 可整体替换；`inject_messages` 在该批工具结果全部写回后追加 |

不触发 `after_tool` 的场景：被扩展/权限拦截、后台任务占位、中断产生的占位结果、参数 JSON 解析失败（各类占位路径无真实执行结果）。

### 5.4 子代理 `core/sub_agent.py`

- 四个节点语义与主循环一致。
- `before_llm` 的 `abort`：子代理返回 `(subagent aborted by extension: reason)` 文本。
- `after_llm`：`streaming_message` 返回后、`messages.append` 之前；`tool_calls` 清空即返回文本结论。
- 工具循环：与主循环相同的"外部 before_tool → 参数回写 → 旧权限门 → 执行 → after_tool"顺序。
- `inject_messages` 追加到子代理本地 `messages`。

### 5.5 新老机制共存

- `core/hook/hook.py` 保持不动：`permission_hook` 等继续在工具节点生效，`pre_llm_call`/`post_tool_call` 旧事件维持现状（未触发），不做迁移。
- 工具节点顺序固定：外部扩展先改参数 → 旧权限门检查最终参数，避免权限被绕过。
- README 说明新老差异，避免使用者混淆两套机制。

## 6. 错误处理与信任模型

- 扩展代码在 agent 进程内以完整权限运行，只放可信代码。
- 加载失败绝不阻断启动；启用与否由 `.harness/extensions/<扩展>/extension.json` 的 `enabled` 明确控制。
- hook 异常 fail-open；数据契约违规（类型不符）可能在主循环抛出，属于扩展 bug，文档写明。
- 修改 `enabled` 需重启进程生效。

## 7. 测试策略

命令：`uv run pytest tests/`，新增 `tests/test_extension_*.py`。

- **dispatcher 单测**：注册顺序、链式修改可见、`block`/`abort` 短路剩余 hook、sync/async 回调、单 hook 异常不影响后续（caplog 断言）、`reset_extensions()`。
- **loader 单测**（monkeypatch `EXTENSION_DIR` → tmp_path）：enabled true 注册成功；false 跳过；清单缺失/非法 JSON/无 `register`/导入错误 → 跳过且不影响其它扩展；注册中途抛错 → 整模块零注册；同包相对导入可用；`_` 开头跳过；重复调用幂等。
- **主循环集成测试**（复用现有 stub 模型/工具模式）：`before_llm` 修改送达模型、`abort` 结束回合；`after_llm` 改内容写回会话、清空 `tool_calls` 结束回合；`before_tool` 改参数送达 handler、`block` 写回结果；`after_tool` 改结果写回会话、`inject_messages` 追加；权限门看到外部修改后的参数（顺序断言）。
- **子代理集成测试**：至少覆盖一条工具链路（`before_tool` 修改/拦截或 `after_tool` 修改）。
- **回归**：现有测试全绿，`trigger_hooks` 相关行为不变。

## 8. 文档与示例

- README 新增"扩展（extensions）"一节：目录结构、清单、API、事件表、fail-open、信任模型、切换需重启、明确排除的路径。
- `examples/extension_example/`：含 `extension.json` + `__init__.py` 的最小示例，说明复制到 `.harness/extensions/` 后手工冒烟。
- 本 spec 存放于 `docs/superpowers/specs/`；`.gitignore` 已移除对 `/docs` 的忽略。

## 9. 变更文件清单

新增：

- `core/extension/__init__.py`、`api.py`、`context.py`、`dispatcher.py`、`loader.py`
- `tests/test_extension_dispatcher.py`、`tests/test_extension_loader.py`、`tests/test_extension_loop_wiring.py`
- `examples/extension_example/`

修改（全部为增量接线）：

- `core/config.py`：+`EXTENSION_DIR`
- `core/bootstrap.py`：`_CONFIG_DIRS` + `load_extensions()` 调用
- `core/loop_with_interrupt.py`：1 行 import + 4 个触发点
- `core/sub_agent.py`：1 行 import + 4 个触发点
- `README.md`：扩展章节
- `.gitignore`：移除 `/docs`
