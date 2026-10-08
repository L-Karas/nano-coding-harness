# TUI 界面

Textual 全屏界面。安装、启动与快捷键 / 内置指令见 [README](../README.md#tui-使用)。

## 布局

左右分栏（约 4:1）：左侧标题栏 + 卡片式聊天记录 + 状态行 + 输入条 + 页脚（cwd / git 分支）
+ 底行（左：上下文占用条（当前会话 token 占上下文长度的进度条与百分比），右：当前模型）；
右侧 Information 面板实时展示 Todos（`in_progress` 转圈）与后台任务（`running` 转圈），
分区与条目均可点击折叠/展开。

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
