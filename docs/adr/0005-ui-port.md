# 域层零 TUI 依赖：交互与渲染统一走 UI port

## Status

accepted

`core/interaction.py` 是域层 → UI 的端口（叶子 module）：除现有的 `ask_permission` / `ask_clarify` 外，新增渲染事件 `stream_assistant_response` / `render_tool_call` / `render_tool_result` / `render_tool_result_diff` / `render_background_notification` 与状态槽 `render_thinking_status` / `render_working_status` / `render_scope`，默认全部 no-op。`core/tui/render.py` 导入时经 `register_render(...)` 注册实现；`StepRenderer` 仍由主循环从端口函数组装并注入 Agent step，step 不依赖端口。

## Considered Options

- **扩展既有端口**（采纳）：一个 module 承载交互与渲染，headless 默认值集中可见；域层不再 import `core.tui`。
- **新建独立渲染端口**（拒绝）：域层要依赖两个 port，注册点分裂。
- **step 直接调用端口全局函数**（拒绝）：丢失 per-step 的 `StepRenderer` 注入 seam；端口若提供 renderer 工厂又会与 agent_step / tools 成环。
