# Extension host contract：事实只读、票箱受控、注册原子

## Status

accepted

`core/extension` 的公开缝分成三层，避免扩展作者与 host 共享同一只可变上下文：

- **上下文**：调用方事实（`tool_name` / `cwd` / `finish_reason` / `tctx` 等）只读，提案字段
  （`args` / `messages` / `result` / `inject_messages` 等）可写；控制状态只通过
  `abort()` / `decide()` / `handle()` 维护。上下文使用 `dataclass(slots=True)`，不能再动态塞入
  `aborted` / `handled` 等外来字段。
- **事件规格**：`events.py` 的 `EVENT_SPECS` 绑定事件 → 上下文类型、唯一短路字段与归一字段。
  `dispatch()` 进入时校验事件与上下文配对，短路只认该事件自己的控制字段。
- **注册**：`ExtensionAPI` 缓冲 `Registration` 记录；`host.commit_extension()` +
  `dispatcher.add_many()` 一次校验、一次写入，loader / builtin 不再各自解包位置元组。
  内置身份由内置注册入口私有的 `_BuiltinExtensionAPI` 决定，外部 API 不暴露 `builtin` 开关。
- **决定票**：`Ballot` 只接受合法 `Decision`；扩展通过 `decide()` / `deny()` 写票，
  `ctx.decisions` 是受控票箱（正常写入口是 `decide()`），派发器负责提交、回滚与参数过期。
- **展示状态**：`ExtensionInfo.error` 显式标记加载失败，`status` 不再依赖 `detail` 字符串；
  `toggle_extension()` 返回 `ToggleResult`，UI 不再从三元组和 registry 二次推导。

## Considered Options

- **保留可变 dataclass + 鸭子类型短路**（拒绝）：已复现 user 阶段设置 `ctx.aborted` 跳过权限门、
  改写 `tool_name` / `cwd` 让 gate 误判，以及非法 `decisions` 元素打崩 dispatch。
- **完整 `ExtensionHost` 实例 + 依赖注入**（暂拒绝）：当前只有一个 host，进程内单例可接受；
  真正的问题是低层 mutator 太多，先把它们收进 host 私有入口，而不是增加 seam。
- **保留位置元组式 `commit()`**（拒绝）：loader / builtin 重复编排，且 `add` 循环不是事务性的。

## Consequences

- 外部扩展的 `register(api)` 必须是同步函数；异步 register 按加载失败处理。
- 对只读字段赋值 / 删除抛 `AttributeError`；`aborted` / `handled` 只能通过 `abort()` / `handle()` 修改。
- 向 `ctx.decisions` 直接塞非 `Decision` 对象抛 `TypeError`。
- `tool_execute` 在 gate 之后，`args` / `tctx` 与执行层共享引用；只读是接口约定，不是深拷贝隔离。
- `dispatch()` 对未知事件 / 错误上下文立即报错，不再静默 no-op。
- `/extensions` 的启停结果由 `ToggleResult` 携带，失败原因与 warning 不再由 TUI 反推。
- Python 仍无法阻止可信扩展显式 import 内部模块；这条缝是正常扩展 API 的边界，不是进程内安全沙箱。
