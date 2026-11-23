#!/usr/bin/env bash
# 抓取 arena.ai 排行榜页面的实时快照（真实 Chromium + CDP）。
#
# arena.ai 全站走 Cloudflare：curl 直连会被 WAF 硬封禁（403 Attention Required），
# 走代理会拿到 JS 挑战（Just a moment...），必须用真浏览器跑完 JS 才能拿到数据。
#
# 用法:
#   bash fetch_leaderboard.sh <url> <outfile> [waitSec] [chrome 额外参数...]
#
# 例:
#   bash fetch_leaderboard.sh https://arena.ai/leaderboard/agent out.html 20 \
#        --proxy-server=http://127.0.0.1:7890
#
# 环境变量:
#   CHROME_BIN  指定 chrome/chromium 可执行文件；默认从 %LOCALAPPDATA%\ms-playwright 找最新版
#   CDP_PORT    指定调试端口；默认 9300-9399 随机
#   UA          覆盖 User-Agent
set -u

if [ $# -lt 2 ]; then
  sed -n '2,20p' "$0"
  exit 2
fi

URL="$1"
OUT="$2"
WAIT="${3:-20}"
shift 2
if [ $# -ge 1 ]; then
  shift   # 吃掉 waitSec，剩下的都是 chrome 参数
fi

# --- 1. 定位 chrome ---
LOCAL_UNIX="$(cygpath -u "${LOCALAPPDATA:-$HOME/AppData/Local}" 2>/dev/null || echo "$HOME/AppData/Local")"
if [ -z "${CHROME_BIN:-}" ]; then
  CHROME_BIN="$(ls -d "$LOCAL_UNIX"/ms-playwright/chromium-*/chrome-win64/chrome.exe 2>/dev/null | sort -V | tail -1)"
fi
if [ -z "${CHROME_BIN:-}" ]; then
  CHROME_BIN="$(command -v chrome 2>/dev/null || command -v google-chrome 2>/dev/null || true)"
fi
if [ -z "${CHROME_BIN:-}" ]; then
  echo "找不到 chrome，请设置 CHROME_BIN" >&2
  exit 2
fi

PORT="${CDP_PORT:-$((9300 + RANDOM % 100))}"
TMPBASE="${TMPDIR:-/tmp}"
PROFILE_DIR="$TMPBASE/cdp-prof-$PORT"
rm -rf "$PROFILE_DIR"
PROFILE_WIN="$(cygpath -w "$PROFILE_DIR" 2>/dev/null || echo "$PROFILE_DIR")"

# --- 2. 启动 chrome ---
"$CHROME_BIN" --headless=new \
  --remote-debugging-port="$PORT" --remote-allow-origins='*' \
  --user-data-dir="$PROFILE_WIN" \
  --no-first-run --no-default-browser-check --disable-gpu \
  --disable-blink-features=AutomationControlled \
  --lang=zh-CN \
  --user-agent="${UA:-Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36}" \
  "$@" "$URL" > "$TMPBASE/cdp-$PORT.log" 2>&1 &
CPID=$!

# 无论 node 成功、失败还是被 Ctrl-C，都要收掉本次启动的 chrome。
# 绝不能用 taskkill //IM chrome.exe：会把你正开着的浏览器一起杀掉。
cleanup() {
  taskkill //PID "$CPID" //T //F >/dev/null 2>&1
  # root 先死时部分子进程会 reparent 存活，再按 profile 名兜底（只匹配本次端口）
  powershell -NoProfile -Command \
    "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | Where-Object { \$_.CommandLine -like '*cdp-prof-$PORT*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" \
    >/dev/null 2>&1
  sleep 1  # 等 chrome 真正放手 profile 目录，否则删除会刷 "Device or resource busy"
  rm -rf "$PROFILE_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
sleep 1

# --- 3. 抓 DOM ---
node "$(dirname "$0")/cdp_dom.mjs" "$PORT" "$OUT" "$WAIT"
exit $?
