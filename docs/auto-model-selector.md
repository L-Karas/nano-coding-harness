# Auto model selector

## 当前状态

本期已实现「子代理显式模型选择」：`spawn_subagent` 的 `model_choice` 与 `list_models`，
详见 [ADR-0016](adr/0016-subagent-model-choice.md)。下文的价格 / 性能指标 / 难度 router
仍未实现，属于后续 skill 的范畴。

## 目的
在启用子代理时，能够根据任务难度和模型成本自动选择合适的模型进行处理

## 所需功能
1. 定时更新的模型性能指标
2. 模型成本
3. 当前可用的模型列表
4. 基于任务难度的模型router