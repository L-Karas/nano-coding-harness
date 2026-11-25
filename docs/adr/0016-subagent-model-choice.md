# 子代理模型按 spawn 显式选择

## Status

accepted

`spawn_subagent` 增加可选 `model_choice`：`{model: "provider:model", thinking_level:
"minimal" | "low" | "medium" | "high" | "max"}`。对象缺省时沿用 settings 的
`default_sub_model` / `default_sub_model_thinking_level`；一旦给出，两个字段都必填（`ModelChoice`
两个必填字段，类型层面禁止「只给模型」或「只给档位」）。

校验发生在工具构造期：`validate_model_choice` 检查 provider / 模型已注册、provider 已配置非空 API key。
`ToolPool.precheck_call` 构造工具时即失败，错误作为同步工具结果回到主代理，不会变成稍后的后台
通知。该模型不支持某档（`thinking_level_map[level] == false`）时沿用 `/effort` 的降级语义：
思考照开、不发送 `reasoning_effort`，不视为错误；`thinking: false` 的模型则完全不发送 thinking
参数，右栏显示 `off`。

client 来源：`model_client_for(choice)` 按 `(provider, model, thinking_level)` 缓存；构造路径直接
绑定模型，不读 settings，因此「显式指定却悄悄回退」不可能发生。该缓存与
`shared_model_client` / `shared_sub_model_client` 分离：多个子代理并发使用不同模型时各持各的
client；注册表重载、provider 换 key、模型注销时清空缓存，使新建 client 不复用旧凭证
（已在运行的子代理保留自己的实例）。

上下文压缩：`prepare_messages` / `compact_history` / `summarize_history` 的 `sub_model: bool`
改为显式 `client`。子代理把本次实际 client 传下去，触发阈值、保留预算与摘要模型都跟随该模型；
主代理省略 client 时回退主 client。`list_models`（main-only）列出已配置 provider 的模型能力与
档位，主代理据此选择。右栏 Subagents 条目展开显示 `provider:model · level`，日志同步。

本期不实现按任务难度自动路由、模型价格与性能指标；`docs/auto-model-selector.md` 保留为后续
skill 的路线图。

## Considered Options

- **主代理显式参数 + 缺省走 settings**（采纳）：零额外分类调用，行为可预测；主代理看
  `list_models` 后自行决定难度与成本的权衡。
- **扁平 `model` + `thinking_level` 字段 + 运行时配对校验**（拒绝）：非法状态仍可表示，错误只能
  在运行期补；嵌套 `ModelChoice` 让「必须成对」成为类型不变量。
- **在 `arun` 内校验未知模型**（拒绝）：子代理固定后台执行，错误会退化成异步后台通知；放进
  pydantic validator 才能走 `precheck_call` 同步路径。
- **每次 spawn 新建 `ModelClient`**（拒绝）：同一模型重复建连接、丢连接池；**改共享
  `_SUB_CLIENT`**（拒绝）：并发子代理会互相覆盖模型与档位。
- **保留 `sub_model: bool` 兼容层**（拒绝）：两套来源会让压缩用错 client / 算错阈值；仓库内
  调用点有限，一次改净。
- **本期实现难度 router 与价格数据**（推迟）：需要外部性能指标与分类器，交给后续 skill；先把
  选择参数与执行链路做对。
