# 工具系统

工具即 `BaseTool` 子类（pydantic 模型）：类名 snake_case 即工具名，docstring 即工具描述，
字段即 JSON Schema；`agent_type` 集合决定该工具对哪些代理可见。
执行入口是类上的 `run` / `arun` 方法：schema 字段即参数（`self.*`），覆写其一即可
（`arun` 缺省做取消检查后调 `run`，`run` 缺省用 `asyncio.run` 跑 `arun`）。新增工具只需在对应目录
定义类，并在 `base_tools/__init__.py`、`extra_tools/__init__.py` 或 `web_search/__init__.py` 导出。

运行期由 `assemble_tool_pool()` 按 `agent_type` / `experimental` 装配成 `ToolPool`：向模型提供 schema
列表、按名执行，并以 `ToolResult` 承载成功 / 失败；MCP 工具在装配处包成同一条目。

同一轮内连续出现的多个工具调用按 `BaseTool.parallel_safe` 声明分组（分组函数
`group_parallel_tool_calls`，经 `ToolPool.is_parallel_safe()` 查询）：声明 `parallel_safe = True`
的工具组内并发执行（`asyncio.gather`），其余工具（含未声明的 MCP 工具）作为屏障自成一组、组间串行。
只有可被并发调用（不共享可变状态）的工具才应声明 True。同步实现（`run`）的工具在事件循环上逐个执行、
与其它工具不会真正重叠，声明只影响分组；原生异步（`arun`）的工具才是真正并发等待。
整组被 Esc 取消时，组内未收尾的调用补 `[User interrupted]` 占位。

## 基础工具（main / sub-agent）

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `terminal` | `command`, `should_run_in_background` | shell 执行（同步路径 120s 超时），输出截断 5 万字符；`should_run_in_background=true` 或命中慢命令启发式时自动转后台 |
| `read_file` | `path`, `limit=2000`, `offset=1` | 分页读取文本（默认 2000 行 / 50KB，任一先到即停），超限时返回续读 offset 提示；图片路径（png/jpg/jpeg/gif/webp，单图 ≤ 5 MB，超出时等比缩小）与 `http(s)` 图片 URL 作为图片附件返回，供视觉模型使用；`limit` / `offset` 仅对文本有效 |
| `write_file` | `path`, `content` | 写文件，执行前渲染 diff 预览 |
| `edit_file` | `path`, `old_text`, `new_text` | 单次精确替换（`old_text` 必须非空且唯一），执行前渲染 diff 预览；保留原文件 LF / CRLF 风格与权限，混合换行按多数行统一 |
| `glob` | `pattern`, `use_ignore=true` | 按 glob 模式查找文件，返回相对工作目录的排序路径（只含文件）；`*` / `?` 不跨 `/`，`**` 递归，不含 `/` 的模式按文件名任意深度匹配；默认跳过隐藏路径与 .gitignore 命中项（`use_ignore=false` 放开），ripgrep 与 Python 回退路径语义一致 |
| `grep` | `pattern`, `path=""`, `file_pattern="*"`, `use_ignore=true` | 正则搜文件内容（Python `re` 语法），返回 `path:line:content` 单行记录，按路径 / 行号排序，最多 50 条；`path` 可为文件或目录，缺省为工作目录且不得越界；`file_pattern` 按路径尾部过滤；默认跳过隐藏与 .gitignore 命中项（`use_ignore=false` 放开），含 NUL 的文件不搜；优先 ripgrep，缺失时回退纯 Python，两条路径的隐藏 / .gitignore 取舍与行模型一致（回退只实现 .gitignore）；部分路径不可读时保留命中并附警告 |
| `clarify` | `options`, `multi_select=false` | 停靠区选项列表询问用户（main / sub-agent）：单选或 Space 多选 + Enter 确认，末尾 Other 可键入自定义回答，Esc 取消 |
| `web_search` | `query`, `max_results=10` | 联网搜索：按已配置 Key 的提供方顺序（firecrawl → tavily → exa）尝试，失败自动禁用该提供方，最终回退免 Key 的 ddgs；`max_results` 最小 1 |
| `web_extract` | `url` | 抓取网页正文：先 `npx defuddle` 解析，再按同一提供方顺序回退，最终 ddgs；结果截断 15000 字符 |

工具经 `ToolContext` 拿到取消信号、工作目录与发起者身份（`run` / `arun` 的 `tctx` 参数）。

`read_file` 读取本地图片（`png` / `jpg` / `jpeg` / `gif` / `webp`，单图 ≤ 5 MB）或 `http(s)` 图片 URL 时，
返回文本结果并把图片附件交给 agent step；本轮所有工具结束后统一注入一条 user 消息（按调用顺序
列出来源，随后是同序的 `image_url` part）。URL 图片不下载、不校验；超过大小上限的本地图片按
需要等比缩小（保留原格式，正文注明 `downscaled from <size>`），只有缩不下来（解不开 / 多帧动图 /
1x1 仍超限）才返回工具错误、不注入图片；非视觉模型（`ToolContext.vision=false`）不读取 /
编码图片，只返回文本结果说明未附加。

图片判定、附件构造与超限缩小集中在 `core/tools/vision.py`：其他工具想随结果带回图片时，调
`local_image_result()` / `alocal_image_result()` / `url_image_result()`（`vision=` 传
`ToolContext.vision`），不必复制 MIME 表、上限与文案；字节已经在手上的来源用 `fit_image()`
拿到「缩小后的 mime + 字节」，再用 `data_url()` 拼附件。超限时缩小还是报错的取舍见
[ADR-0010](adr/0010-image-over-limit-is-downscaled.md)。

## 扩展工具

| 工具 | 参数 | 可见 | 说明 |
| --- | --- | --- | --- |
| `todo_write` | `todos[{content,status}]` | main | 整体替换任务清单，右栏实时展示 |
| `spawn_subagent` | `description`, `goal`, `working_directory=""` | main | 独立上下文子代理，后台运行，最多 30 轮，结论经后台通知回传；`goal` 为右栏展示的简短目标，`working_directory` 指定子代理工作目录（缺省与主代理一致） |
| `load_skill` | `name` | main | 返回技能正文与 `SKILL.md` 路径（`<skill>` 标签包裹） |
| `save_memory` | `title`, `content`, `mem_type` | main | `mem_type`: user / feedback / project / reference |
| `schedule_cron` | `cron_expression`, `prompt`, `recurring`, `durable` | main | 注册 5 段式 cron，触发时把 prompt 作为用户消息注入会话 |
| `list_crons` / `cancel_cron` | `job_id` | main | 查看 / 取消任务 |

MCP 工具以 `mcp__<server>__<tool>` 命名合并进同一工具池（仅 main / sub-agent）。
MCP 结果中的 `ImageContent` 与 `read_file` 的图片走同一条注入链路：文本块拼成工具结果正文，
每个图片块单独成为一个 `ImageAttachment`（data URL）。图片先校自报 MIME（只收 `png` / `jpeg` /
`gif` / `webp`，大小写与 `; charset=` 之类参数会归一化），再受单图 5 MB 上限约束：超限的与
本地图片同样等比缩小（正文追加一行缩小说明）；缩不下来的图片即整个调用返回工具错误、不附
图片。`is_error` 的结果转成工具错误。
调用失败（未连接 / 超时 / 服务端报错）不在 MCP 层转文本：格式化成 `McpToolError`（带工具名
与原因，原始异常留在 `__cause__`）后抛出，由 `ToolPool` 统一编码成工具错误（ADR-0001）。

## 后台任务

`should_run_background()` 据此判定：`terminal` 显式传 `should_run_in_background=true`，
或命令命中慢命令启发式（`make` / `pytest` / `sleep` / `pip install` / `npm test` / `cargo build` 等，
只看每条子命令的命令词，宁可漏判不误判）。

命中后立即返回 `[Background task <bg-id> started]` 占位结果，工具在后台继续执行（原生异步挂
回合 loop，纯同步工具走守护线程）；下一轮开始时结果以 `<background-task-notification>` 注入会话，通知含 `<task-id>` / `<status>`
（`completed` / `error` / `cancelled`）/ `<tool>` / `<result>`，右栏 Background Tasks 同步显示状态。

运行中且可停的条目行尾有 `■`：点击即指名停止（句柄跨回合有效；Esc 仍按旧路径取消本回合
启动的工作，见 [ADR-0009](adr/0009-background-cancel.md)）。原生异步任务经 `task.cancel()` 停掉（`terminal`
会 terminate 子进程），结果仍经同一通知通道回传，状态为 `cancelled`。worker 线程路径
（纯同步工具）没有可杀句柄，不给 `■`。
