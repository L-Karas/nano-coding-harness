# nano-harness

一个用 Python 写的 coding agent harness：全屏 TUI、流式工具调用、分层上下文压缩、子代理与队友协作。本文档只记录领域词汇，不承载实现细节。

## Language

**ToolResult**:
一次工具调用的结局：交还给模型的内容，加上这次调用是否失败的结构标志。
_Avoid_: 工具输出字符串、错误前缀串、handler 返回值

**Tool**:
模型可调用的一项能力：它的参数（即 schema）与它的实现一起定义。
_Avoid_: handler、run_* 函数

**ToolPool**:
按 agent 类型（main / sub-agent / teammate）装配好的一组 Tool，是取 schema 与执行调用的唯一入口。
_Avoid_: tools/handlers 平行表、工具注册表

**ToolContext**:
一次工具执行的运行上下文：取消信号、工作目录与发起 agent 的身份，由调用方持有并传入。
_Avoid_: 全局变量、handler 包装

**Agent step**:
一次模型调用及其触发的工具派发；main / sub-agent / teammate 共用同一实现，差异经 StepPolicy 显式化。
_Avoid_: 回合（turn）、loop

**Runtime state**:
harness 运行期的展示状态：todo 列表、后台任务、子代理；由 RuntimeState 单例持有，经类型化快照读取。
_Avoid_: 全局变量、面板数据源

**Turn runner**:
驱动一次 turn 的线程契约：私有事件循环、串行锁与当前回合的上下文 / 任务登记都在一个 implementation 内，调用方只提「跑回合 / 压缩 / 中断」。
_Avoid_: AGENT_LOCK、submit 阻塞、直接操作 loop

**UI port**:
域层访问 UI 的唯一入口：权限 / 澄清提问与渲染事件；TUI 适配器导入时注册实现，headless 走 no-op 默认值。
_Avoid_: 全局渲染函数、直接 import TUI

**Message**:
一条会话记录：角色、内容、推理、工具调用 / 结果与 diff payload；贯穿 SessionManager 的 interface，JSON 只在文件边界出现。
_Avoid_: 消息 dict、asdict 往返

**Session**:
一串 Message 及其标题 / 时间戳；SessionManager 持有当前会话并负责 map / 指针 / 文件三者一致。
_Avoid_: 会话文件、session_map
