# 上下文压缩（两层预算管线）

每次模型调用前，历史消息都要经过「工具输出截断 → 触发式摘要」两层预算管线，尽量保证
单次请求的 `输入 + 输出 < 当前模型上下文`。入口是 `core/context/compact/context_compact.py`
的 `prepare_messages`；只有第二层会调用模型。

## 1. 概览

主 agent 每回合、sub-agent 每次 step 前的调用链：

```
SESSION_MANAGER.load_messages()
        │
        ▼
prepare_messages(messages, ctx, sub_model=?)
        │
        ├─ 1. truncate_large_tool_outputs(messages)   ← 总是执行，不调用模型
        │
        └─ 2. auto_compact ? compact_history(...)     ← 超阈值才调用模型做摘要
                 │
                 └─ 未触发 / 无可用窗口 → 原样返回
```

- 第一层原地修改、无模型调用；第二层在估算 token 超阈值时才做摘要。
- 手动 `/compact` 不经过 `prepare_messages`，且用 `auto_compact=False` 跳过阈值判断，直接做全量摘要。
- `auto_compact=false` 时只做第一层：超长历史交给 API 报 context 超限，切模型后尤其要注意。
- 主 agent 回合在 `prepare_messages` 后把结果写回会话（`core/agent_loop.py`）；
  `/compact` 只在真正压缩时回写。

## 2. 预算与触发

### 2.1 配置字段（`core/config.py` 的 `AgentConfig`）

| 字段                       | 默认值       | 约束     | 含义                             |
|--------------------------|-----------|--------|--------------------------------|
| `compact_trigger_tokens` | `500_000` | `> 0`  | 历史估算 token 超过它时触发压缩            |
| `compact_reserve_tokens` | `50_000`  | `>= 0` | 自动压缩后保留的最近消息 token 预算          |
| `summary_max_tokens`     | `20_000`  | 整数     | 生成摘要时的最大 token 生成数，经当前模型 `max_output` 钳制 |

三个字段都是绝对值；阈值字段不接受比例。

### 2.2 按当前模型解析（`core/client/model.py`）

`ModelClient.compact_trigger_tokens()` / `compact_reserve_tokens()` 每次调用时都按当前模型重新解析，
所以 `/model` 切换后立即生效：

- 有效触发阈值按优先级选取，兜底 = `max(1, context_length × 80%)`：
  最终阈值不超过兜底（窗口过小时保底 1），防止用户把配置改得过大。
    1. `compact_trigger_tokens` 不超过兜底时直接使用（优先尊重配置值）；
    2. 否则计算 `max(1, context_length − escalated_max_tokens)`，不超过兜底时使用它；
    3. 若它也超过兜底，则使用兜底。
- 注意：优先级 1 会绕过输出余量预留。若 `compact_trigger_tokens` 很小但
  `escalated_max_tokens` 很大，阈值不会被抬高；需要为输出留空间时应同时调小后者。
- `max_output` 不参与压缩阈值计算：部分模型声明 `max_output == context_length`，
  按它预留会把阈值压到 `1`，导致几乎每轮都压缩；输出钳制仍使用 `max_output`。
- `context_length <= 0`（未选择模型 / 上下文未知）→ 阈值解析为 `0`，压缩管线原样返回、不触发摘要；
- 退化：`escalated_max_tokens >= context_length` 时 `context_length − escalated_max_tokens`
  保底为 `1`，等于几乎每轮都压缩。
- 有效保留预算 = `min(compact_reserve_tokens, 有效触发阈值)`：保留预算永远不超过触发阈值。
- `summary_max_tokens` 经 `clamp_max_tokens` 钳到当前模型 `max_output`。

上下文长度与最大输出取自当前模型的注册表配置：`core/client/models.json`、
`.harness/.custom_models.json`。

### 2.3 切换模型后的行为

- `/model` 只替换内存中的 client：不做预检、不立即压缩，也不提示当前会话是否超窗。
- 有效阈值按新模型重算：切到小窗口模型、配置值超过窗口时，阈值被**封顶**在新窗口边缘
  （不是等比缩小）；下一回合进入 `prepare_messages` 时，旧会话若已超阈值会先压缩再发请求。
- `auto_compact=false` 时没有这层兜底，只能等 API 报 context 超限。
- 触发判断用的估算只覆盖历史消息，不含 system prompt 与 tools schema（见 4.2）。

## 3. 第一层：工具输出截断

`truncate_large_tool_outputs(messages)`（总是执行，原地修改并返回）：

- 只处理 `role == "tool"` 的消息；工具名从 assistant 的 `tool_calls` 映射得到
  （工具结果消息自身不带工具名），查不到的孤儿 `tool_call_id` 视为非 `read`。
- `read_file` 的输出已在工具侧按同一上限截断，并自带 offset 续读提示 → 跳过，不落盘、不二次截断。
- 其余工具经 `truncate_tool_output` 按**行数 2000 / 字节 50KB 双上限**截断，任一先到即停，
  停处附 `[Truncated: … offset=N]` 续读提示（`core/context/truncate.py`）。
- 一旦截断：全文落盘到 `.harness/.task_outputs/tool_results/<tool_call_id>.text`
  （无 id 时用随机文件名），正文替换为 `<persisted-output>` 包裹：
  `<saved-path>` + `<content-preview>`（有界预览）。需要全文时用
  `read_file <saved-path> offset=N` 续读。
- 已是 `<persisted-output>` 的消息直接跳过（幂等）；未超限的输出原样保留，没有内联预算。

模板与常量见 `core/template/message_template.py`。

## 4. 第二层：触发式摘要

`compact_history(messages, ctx=None, auto_compact=True, sub_model=False) -> (messages, compacted)`。

### 4.1 触发判断

- 取当前 client 的有效触发阈值：`sub_model=True` 用 `shared_sub_model_client()`（子代理模型），
  否则用主 client；
- 阈值 `<= 0`（未选模型）→ 原样返回 `(messages, False)`，不调用模型；
- `auto_compact=True`（自动压缩）时，`estimate_size(messages) < 阈值` → 原样返回 `(messages, False)`，不调用模型；
- `auto_compact=False`（手动 `/compact`）跳过阈值判断，即使历史低于阈值也直接压缩；未选模型时仍是 no-op，
  UI 提示 Nothing to compact。

### 4.2 token 估算（`core/context/token.py`）

- tiktoken `encoding_for_model("gpt-5")`（编码器缓存）；
- 每条消息计入 `content` + `reasoning_content` + `tool_calls`（函数名 + arguments）；
- `content` 为数组时，`text` part 正常计，每个 `image_url` part 固定计 `IMAGE_TOKEN_ESTIMATE = 1000` token；
  不按尺寸 / 分辨率精确计算，估算偏差可能导致压缩触发过早或过晚。
- 已知缺口：不含 system prompt 与 tools schema，估算只代表历史大小，不是请求的完整大小。

### 4.3 自动压缩的切分规则（`auto_compact=True`）

`find_index_to_split(messages, reserve_tokens)`：

1. 优先按 turn 切：从最早的 user 消息开始，找「后缀（保留部分）token ≤ reserve」的 user 边界；
2. 单个 turn 就超预算时，退化为按 assistant 消息切，尽量多保留 assistant + tool 组
   （tool_calls 与其结果不得拆开）；
3. 找不到满足预算的边界 → 返回 `len(messages)`，等价于全量摘要。

产物 = 一条摘要消息 + 保留的尾部：

```
[user] <compacted_messages>
{summary}
</compacted_messages>
     + messages[split_index:]
```

### 4.4 全量摘要（`auto_compact=False`，`/compact` 走这条）

- 不再切分：整段历史交给模型摘要，结果只有一条 `<compacted_messages>` 消息，不留尾部消息；
- 请求 = `to_llm_messages(messages, vision=client.supports_vision()) + SUMMARY_PROMPT_TEMPLATE`（结构化 checkpoint 摘要模板，
  明确要求不总结 system prompt），流式生成，`max_tokens = clamp(summary_max_tokens)`；非视觉模型下，
  含图片的消息整条替换为 `IMAGE_OMITTED_MESSAGE` 文本占位；
- 手动 `/compact` 带 `ctx`：流式过程中检查取消（Esc），中断则不落盘；回合内自动压缩同样可被 Esc 中断。

## 5. 入口与调用方

| 入口            | 路径                                                                                          | 行为                                            |
|---------------|---------------------------------------------------------------------------------------------|-----------------------------------------------|
| 主 agent 每回合   | `core/agent_loop.py`：`prepare_messages(...)` → `update_messages(...)`              | 超阈值自动压缩；未触发时同样写回                              |
| sub-agent     | `core/sub_agent.py`：每轮 `prepare_messages(history, ctx, sub_model=True)`                     | 用子代理 client 的预算与摘要模型                          |
| 手动 `/compact` | TUI → `AgentRuntime.run_compact()` → `TurnRunner` 锁 → `compact_history(auto_compact=False)` | 与用户回合 / cron 互斥；跳过阈值判断直接压缩；Esc 可中断；中断不落会话；成功后重放历史；未选模型时只提示 |

UI 入口在 `core/tui/commands.py`（`_run_compact` / `_compact_worker`），runtime 在
`core/agent_loop.py`（`AgentRuntime.compact`）。

## 6. 配置

`.harness/.settings.json` 由 `/settings` 写入，加载时直接交给 `AgentConfig` 校验。
`/settings` 中两个压缩字段为整数输入（`type="integer"`），回显不带 `.0`。
旧字段 `compact_threshold` / `reserve_threshold` 与比例语义已废弃，文件中残留的未知字段会被忽略。

## 7. 日志与排错

`log_compact_step` 给每个压缩步骤打 `Running … / Finished …` 日志，异常带堆栈（`core/shared/logger.py`）。

| 现象                         | 可能原因 / 处理                                                                            |
|----------------------------|--------------------------------------------------------------------------------------|
| "Nothing to compact"       | 未选模型 / 会话为空                                                                          |
| 压缩后仍然超窗                    | 估算不含 system + tools，且摘要本身可能很大；调小 `compact_trigger_tokens` 或 `compact_reserve_tokens` |
| 切模型后立刻压缩                   | 有效阈值按新模型封顶后低于当前历史大小                                                                  |
| `auto_compact=false` 下的安全性 | 历史必须小于目标模型窗口，否则只能靠 `/compact` 手动处理                                                   |

## 8. 相关文件

- `core/context/compact/context_compact.py`：两层管线、切分、摘要生成、工具结果落盘；
- `core/context/compact/__init__.py`：导出 `truncate_large_tool_outputs` / `compact_history` / `prepare_messages`；
- `core/context/token.py`：token 估算；
- `core/context/truncate.py`：行数 / 字节截断；
- `core/config.py`：预算字段、按模型解析；
- `core/client/model.py`：`compact_trigger_tokens()` / `compact_reserve_tokens()` / `clamp_max_tokens()`；
- `core/template/message_template.py`、`core/template/prompt_template.py`：落盘包裹与摘要模板。
