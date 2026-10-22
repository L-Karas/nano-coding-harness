"""命令权限模式：地板（硬拒绝）与默认层（确认卡）的规则表及匹配。

对外只暴露两个查询：`floor_denial(command)` 与 `is_destructive(command)`；
正则编译、命令位锚定、空白归一等细节都封装在本模块内。

规则表分四张（由旧 hook 权限门迁移并收窄误伤）：
- `DENY_LIST`：灾难子串，命令任意位置命中即拒绝（清根、写裸设备、fork bomb）。
- `DENY_COMMAND_LIST`：危险词，仅在命令位命中（提权、关机、格式化），避免 `echo sudo`
  这类提及被硬拒。命令位 = 行首或 ; && || | ( { 换行、$(、反引号之后，允许前置 VAR=x。
- `DESTRUCTIVE` / `DESTRUCTIVE_REGEX`：默认层危险命令，子串与正则互补。

匹配大小写不敏感；匹配前把 tab 与连续空格折叠为单空格（调用方展示仍用原文），
因此 `rm\t-rf /`、`git  clean -fd` 这类空白变体与普通写法同判。
"""
import re

__all__ = ["floor_denial", "is_destructive"]


def floor_denial(command: str) -> str | None:
    """命中地板时返回其展示标签（label，用于错误文案）；未命中返回 None。"""
    normalized = _normalize_command(command)
    for regex, label in FLOOR_PATTERNS:
        if regex.search(normalized):
            return label
    return None


def is_destructive(command: str) -> bool:
    """是否命中默认层危险命令（Manual 下触发确认；Yolo 下默认层被跳过）。"""
    normalized = _normalize_command(command)
    return any(regex.search(normalized) for regex in DESTRUCTIVE_PATTERNS)


_COMMAND_PREFIX = r"(?:^|[;&|({\n]|`)\s*(?:[A-Za-z_]\w*=\S*\s+)*"
# 命令路径前缀：/usr/bin/sudo、./mkfs.ext4 等；wrapper（env/nice/xargs sudo）不在覆盖内
_COMMAND_START = _COMMAND_PREFIX + r"(?:[^\s]*[\\/])?"


def _command_position(pattern: str) -> re.Pattern:
    """命令位匹配：模式只在命令开头或分隔符后命中（允许前置 VAR=x 赋值与命令路径前缀）。"""
    return re.compile(_COMMAND_START + pattern, re.IGNORECASE)


def _substring_pattern(pattern: str) -> re.Pattern:
    """大小写不敏感的子串匹配；模式首尾为字母/数字时要求单词边界（'sudo' 不命中 'sudoku'）。"""
    left = r"(?<!\w)" if pattern[0].isalnum() else ""
    right = r"(?!\w)" if pattern[-1].isalnum() else ""
    return re.compile(left + re.escape(pattern) + right, re.IGNORECASE)


_SPACE_RUN_RE = re.compile(r"[ \t]+")


def _normalize_command(command: str) -> str:
    """折叠空白供匹配使用（调用方展示仍用原文）。"""
    return _SPACE_RUN_RE.sub(" ", command)


# 硬拒绝·灾难子串：任意位置命中即拒绝。元组为 (正则, 展示标签)。
DENY_LIST = [
    # 清根：只匹配根目录本身（/tmp/foo 等具体绝对路径走确认卡），兼容多空格/制表符
    (r"rm\s+-rf\s+/(?=$|[\s*;&|)/])", "rm -rf /"),
    (r"rm\s+-fr\s+/(?=$|[\s*;&|)/])", "rm -fr /"),
    (r"--no-preserve-root", "--no-preserve-root"),
    # 覆写裸设备（/dev/null 等字符设备不在此列，仅拦块设备）
    (r">\s*/dev/sd", "> /dev/sd"),
    (r">\s*/dev/hd", "> /dev/hd"),
    (r">\s*/dev/nvme", "> /dev/nvme"),
    (r">\s*/dev/vd", "> /dev/vd"),
    (r">\s*/dev/mmcblk", "> /dev/mmcblk"),
    (r">\s*/dev/mapper", "> /dev/mapper"),
    (r">\s*/dev/md", "> /dev/md"),
    (r">\s*/dev/loop", "> /dev/loop"),
    # 结构匹配 fork bomb：覆盖 :(){ :|:& };: 、: () { :|:& };: 与 bomb(){ bomb|bomb& };bomb；
    # 要求结尾再次调用同名函数，避免把引号里的函数定义文本当成炸弹
    (r"([:.\w]+)\s*\(\s*\)\s*\{(?=[^}\n]*\1\s*\|)(?=[^}\n]*&)[^}\n]*\}\s*;\s*\1(?!\w)",
     ":(){ :|:& };:"),
]

# 硬拒绝·命令位：仅在命令位命中，避免 `echo sudo`、`man mkfs`、`man reboot` 这类提及误伤。
DENY_COMMAND_LIST = [
    (r"sudo\b", "sudo"),      # 提权
    (r"doas\b", "doas"),
    (r"shutdown\b", "shutdown"),  # 关机/重启
    (r"reboot\b", "reboot"),
    (r"poweroff\b", "poweroff"),
    (r"systemctl\s+reboot\b", "systemctl reboot"),
    (r"systemctl\s+poweroff\b", "systemctl poweroff"),
    (r"systemctl\s+halt\b", "systemctl halt"),
    (r"mkfs\b", "mkfs"),      # 格式化/抹除签名
    (r"wipefs\b", "wipefs"),
    (r"dd\b[^;\n|&]*of\s*=\s*/dev/(?:sd|hd|nvme|vd|mmcblk|mapper|md|loop)", "dd of=/dev/<disk>"),
    (r"diskpart\b", "diskpart"),  # Windows 磁盘操作
    (r"format\s+[A-Za-z]:", "format <drive>:"),
    (r"clear-disk\b", "clear-disk"),
]

# 危险命令·子串：弹出确认卡，用户确认后才放行。
DESTRUCTIVE = [
    "rm ", "rmdir ", "del ", "rd ", "remove-item", "shred ", "truncate -s", "mkswap",
    "chown -R", "chgrp -R", "takeown", "reg delete",
    "fdisk", "parted",
    "killall", "pkill", "taskkill /f",
    "git reset --hard", "git push --force", "git push -f",
    "git restore .", "git checkout .", "git checkout -- ", "-delete",
    "crontab -r", "iptables -F", "ufw disable", "nft flush",
    "/dev/tcp/",  # 反弹 shell
]

# 危险命令·正则：子串写不清的变体（含空白、无空格重定向、解释器版本号等）。
DESTRUCTIVE_REGEX = [
    r">\s*/(?:etc|usr|var|bin|sbin|boot|lib64|lib)/",  # 覆写系统目录（含 `>/etc` 粘连写法）
    r"git\s+clean\s+-[\w-]*f",  # git clean -f / -fd / -fdx
    r"(?<!\w)chmod\s+(?:-{1,2}[\w-]+\s+)*(?:0?777|a\+rwx|ugo\+rwx)\b",
    r"(?<!\w)kill\s+(?:-9|-KILL|-SIGKILL|-s\s*(?:9|KILL|SIGKILL))\b",
    r"\|\s*(?:sh|bash|zsh|ksh|dash|fish)\d*\b",  # 管道执行：| sh / |bash / |bash5
    r"\|\s*(?:python|node(?:js)?|perl|ruby|php|pwsh|powershell|deno)\d*\b",  # | python3 / | node18
    r"\|\s*(?:iex|Invoke-Expression)\b",
    _COMMAND_START + r"dd\s",  # dd 读写（写裸设备已被地板单独拦下，这里降为确认）
]

# 模式表静态不变，导入时编译一次；地板项保留原文用于错误文案
FLOOR_PATTERNS = (
    [(re.compile(pattern, re.IGNORECASE), label) for pattern, label in DENY_LIST]
    + [(_command_position(pattern), label) for pattern, label in DENY_COMMAND_LIST]
)
DESTRUCTIVE_PATTERNS = (
    [_substring_pattern(pattern) for pattern in DESTRUCTIVE]
    + [re.compile(pattern, re.IGNORECASE) for pattern in DESTRUCTIVE_REGEX]
)
