# TUI 界面

Textual 全屏界面。安装、启动与快捷键 / 内置指令见 [README](../README.md#tui-使用)。

弹窗列表 ↑/↓ 选择；表单窗 `Tab` / ↑/↓ 切字段；`/skills` 里 `Tab` 把调用信息填入输入栏（不发送）；
多数窗单击行 = `Enter`（切换 / 打开 / 执行），注销窗单击仅选中、`Delete` 执行。
`/settings` 表单支持鼠标：点开关 / 下拉选项即落盘，点锁定输入框进入编辑态（输入后 `Enter` 保存）。

## 布局

左右分栏（约 4:1）：左侧标题栏 + 卡片式聊天记录 + 状态行 + 输入条 + 页脚（cwd / git 分支）
+ 底行：`Context Usage:` 标签 + 上下文占用条（盲文点阵：20 格 × 8 点，末格按点亮点数呈现部分填充，
一格内 0.625% 一档；40%/80% 阈值绿→黄→红）+ 占用百分比，
末尾括号提示压缩阈值（K/M 单位，青色，如 `(compact at 160K)`；`auto_compact` 关闭时改为 `(auto-compact off)`）
+ 其下方：左 `Approval Mode: <mode>` 显示当前实际生效的审批模式（`LLM` / `Decision Model` 暂按 `Manual`），
右端按实际行宽填充对齐显示当前模型（`(provider) model * level`）；
标签保持次级灰，模式值按危险度染色：`Manual` 黄 `#e5c890`、`Decision Model` / `LLM` 绿 `#a6d189`、
`Yolo` 红 `#e78284`（色表见 `core/tui/theme.py` 的 `APPROVAL_MODE_COLORS`，表外模式回落次级灰）；
右侧 Information 面板实时展示 Todos（`in_progress` 转圈）、后台任务（`running` 转圈）与
运行中的子代理（条目以 `role` 替代内部 id 展示）；分区按内容有无自动展开/折叠（空分区只留 `▶ 标题 · 0` 标题行，有内容的分区展开，
已完成 Todo / 后台任务也算内容），分区与条目均可点击折叠/展开，手动状态保持到该分区下次
「空 ↔ 非空」跳变（手动展开空分区时显示 `(Empty)`）；运行中且可停的后台任务 / 子代理条目行尾
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
同理，占用条与状态行 spinner 依赖盲文点阵字形（U+2800–U+28FF）：终端字体缺字形时会显示方块，
宽度仍按 1 格计（缺字形不影响列对齐，只影响观感）。

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
