# TUI 界面

Textual 全屏界面。安装、启动与快捷键 / 内置指令见 [README](../README.md#tui-使用)。

弹窗列表 ↑/↓ / Tab 浏览；多数窗单击行 = `Enter`（切换 / 打开 / 执行），注销窗单击仅选中、`Delete` 执行。
`/settings` 表单支持鼠标：点开关 / 下拉选项即落盘，点锁定输入框进入编辑态（输入后 `Enter` 保存）。

## 布局

左右分栏（约 4:1）：左侧标题栏 + 卡片式聊天记录 + 状态行 + 输入条 + 页脚（cwd / git 分支）
+ 底行（左：`Context Usage:` 标签 + 上下文占用条（当前会话 token 占上下文长度的进度条与百分比），右：当前模型）
+ 其下方 `Approval Mode: <mode>` 行显示当前实际生效的审批模式（`LLM` / `Decision Model` 暂按 `Manual`）；
标签保持次级灰，模式值按危险度染色：`Manual` 黄 `#e5c890`、`Decision Model` / `LLM` 绿 `#a6d189`、
`Yolo` 红 `#e78284`（色表见 `core/tui/theme.py` 的 `APPROVAL_MODE_COLORS`，表外模式回落次级灰）；
右侧 Information 面板实时展示 Todos（`in_progress` 转圈）、后台任务（`running` 转圈）与
运行中的子代理，分区与条目均可点击折叠/展开；运行中且可停的后台任务 / 子代理条目行尾
有 `■`，点击指名停止（句柄存于 `RuntimeState`，回合结束后仍可停，见 [ADR-0009](adr/0009-background-cancel.md)）。

信息栏默认折叠为左缘 `»`/`«` 标签：Todos / 后台任务 / 子代理任一非空时自动展开，
全部清空后自动收回（用户手动展开过则保持）；`Ctrl+←` / `Ctrl+→` 按 10% 档调宽
（20%–40%），收窄到 20% 以下即折叠，折叠态按 `Ctrl+←` 回到上次宽度。

终端任务栏进度（OSC 9;4，Windows Terminal 等支持）：前台回合、runtime 回合（含 auto_loop 的
cron / 后台结果注入自动回合）、后台任务、子代理任一在跑即动态条；权限确认 / clarify 挂起时
黄色满格；全部结束才清除（退出时兜底清除）。回合外活动由 1s 轮询重估，写状态去重。

终端单元格宽度：键帽 emoji（`1️⃣` = 数字 + U+FE0F + U+20E3）在 Windows Terminal 缺组合圈字形时
按 3 格渲染，比 Textual 的单元格模型多 1 格，会把该行后续单元格（分栏线 / 滑块）整体右推；
正文 Markdown、工具 / diff 卡、用户输入与右栏条目在入 UI 前统一降级为基字符（`1️⃣` → `1`）。

## 界面模块

`core/tui/` 各文件职责：

| 文件 | 职责 |
| --- | --- |
| `ui_textual.py` | App 装配 + 入口（含 `--smoke` 无头自检） |
| `surface.py` | 卡片 / 流式 / 状态行 |
| `footer.py` | 页脚 |
| `commands.py` | 弹窗指令 / 回合 / 压缩 |
| `interactions.py` | 权限 / clarify |
| `render.py` | 线程安全渲染 |
| `widgets.py` | 输入框与补全 |
| `screens/` | 会话 / 模型 / MCP / 登录等弹窗 |
| `panels.py` | 左栏分区 |
| `cards.py` | 折叠卡片 |
| `info_panel.py` | 右栏信息面板 |
| `theme.py` / `app.css` | 主题与样式 |
