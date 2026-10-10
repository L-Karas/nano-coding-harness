# 工具失败的唯一信号是异常

## Status

accepted

工具失败一律以异常表达：工具实现（`run` / `arun`）抛出异常，执行器 `ToolPool` 是唯一把异常编码成 `[Tool Error]:` / `[Unknown Tool]:` 文本并构造 `ToolResult` 的位置；工具返回的字符串一律是内容，执行器不解析返回串里的前缀。

## Considered Options

- **异常唯一**（采纳）：失败信号单一，`is_error` 只在执行器的异常路径为真。
- **兼容解析**（拒绝）：执行器额外识别返回串里的错误前缀并标记失败。它保留两条失败路径，让失败语义依赖文本格式，并与 `test_tool_error_convention.py` 保护的「工具实现抛异常」约定冲突。
