# 权限与 Hook

`core/hook/hook.py` 注册了三个 hook，`pre_tool_call` 在工具分发前运行：

- 硬拒绝（`DENY_LIST`）：清根删除、提权（`sudo`/`doas`）、关机重启、`mkfs`/`wipefs`、`dd` 与裸设备覆写、fork bomb、Windows `diskpart`/`format c:`，命中即拒；
- 危险命令（`DESTRUCTIVE`）：删除类（`rm`/`del`/`shred`…）、系统目录重定向、递归 chmod/chown、强杀进程、`git reset --hard`/强推、`crontab -r`、`curl | sh` 等管道执行，弹出确认卡，用户从停靠区 yes/no 列表作答，默认高亮「No」；
- 匹配大小写不敏感；词条首尾为字母或数字时要求单词边界（`sudo` 不命中 `sudoku`，`| sh` 不命中 `| shasum`）；
- `read_file` / `write_file` / `edit_file` 的路径解析后落在工作目录之外时同样需要确认；
- `terminal` 命令中显式引用的路径（独立 token、`--file=/etc/x` 这类 `=` 值、引号包裹的路径、`~`/环境变量、重定向目标；裸 `cd` 按家目录处理）解析到工作目录之外时同样需要确认，并与危险命令合并为一次确认卡；只做尽力而为的文本解析，变量拼接、`sh -c` 内层脚本与粘连 flag（`-o/tmp/x`）不覆盖；
- 另注册了 `tool_call_log_hook`（调用日志）与 `post_tool_call` 的 `large_tool_output_hook`（超长输出告警）。
