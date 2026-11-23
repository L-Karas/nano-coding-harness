# arena-live-leaderboard — 抓取 arena.ai 的实时排行榜

面向 `https://arena.ai/leaderboard/<board>` 的抓取工具：**用真实 Chromium 过 Cloudflare，再从 Next.js RSC payload 里取出榜单数据**，产出结构化 JSON + 可读 Markdown。

一次抓取能拿到：

| 数据 | 说明 |
|---|---|
| 全量模型榜 | 69 个模型的 `index` 分数、95% CI、排名 |
| 成本 / 耗时 | 每任务中位成本（P50/P25/P95）、中位耗时、Token 用量 |
| 信号明细 | `Task Completion` / `Praise vs Complaint` / `Steerability` / `Bash Reliability` 的 index 与 raw 分数 |
| 元数据 | `license`、`org`、会话数、观测数、榜单快照时间 `lastUpdated` |
| FAQ | 页面折叠项的完整正文（藏在 JSON-LD `FAQPage` 里） |

---

## 为什么不能直接 HTTP 抓

arena.ai 全站走 Cloudflare，四种通道实测：

| 通道 | 结果 |
|---|---|
| `curl` 直连 | ❌ `403 Attention Required!` — **WAF 硬封禁**（CN IP 段），5.9 KB 拒绝页 |
| `curl` 经代理 | ❌ `403 Just a moment...` — 只是 **JS 挑战**，但 curl 不跑 JS |
| `web_extract` 类渲染工具 | ⚠️ 能读首屏，但只渲染卡片，且**数值会偏差**（读到 `$5.91`，真实 DOM `$5.85`） |
| **本目录（真实 Chromium + 代理）** | ✅ 848 KB 完整 DOM，数据全、数值准 |

结论：挑战是在浏览器里跑 JS 完成的，必须真浏览器；直连会挨 WAF 硬封禁，所以还要挂代理。

---

## 文件

| 文件 | 作用 |
|---|---|
| `fetch_leaderboard.sh` | 启动 headless Chromium → 等挑战完成 → 用 CDP 取最终 DOM |
| `cdp_dom.mjs` | CDP 客户端，被上面的脚本调用，一般不用单独跑 |
| `extract_leaderboard.py` | 从 HTML 提取榜单 → `*.json` + `*.md` |
| `example-output.md` | 一份真实产出示例（2026-11-22 快照，69 模型），用来对照格式 |

---

## 前置条件

- **Windows + Git Bash（MSYS）** — 脚本用到 `cygpath` / `taskkill`
- **Node ≥ 22** — 需要内置 `WebSocket` 与 `fetch`
- **Chromium** — 默认找 `%LOCALAPPDATA%\ms-playwright\chromium-*\chrome-win64\chrome.exe`，或用 `CHROME_BIN` 指定
- **Python ≥ 3.13** — 只用标准库
- **可用代理** — 例如 `http://127.0.0.1:7890`（没有代理时先测 `curl -s -x http://127.0.0.1:7890 https://www.cloudflare.com/cdn-cgi/trace`）

---

## 用法

### 1. 抓页面

```bash
bash examples/arena-live-leaderboard/fetch_leaderboard.sh \
     https://arena.ai/leaderboard/agent \
     temp/agent.html 20 \
     --proxy-server=http://127.0.0.1:7890
```

成功时输出：

```
target: https://arena.ai/leaderboard/agent
waiting 20 s for page settle / challenge...
title: "Agent Arena Leaderboard: AI Agent Rankings & Benchmarks"
href : https://arena.ai/leaderboard/agent
len  : 848870 -> temp/agent.html
```

**`title` 是成功判据**：出现 `请稍候…` / `Just a moment...` / `Attention Required!` 都算没抓到。

### 2. 提数据

```bash
python examples/arena-live-leaderboard/extract_leaderboard.py temp/agent.html \
       --out-md temp/agent.md --out-json temp/agent.json
```

```
69 models @ 2026-11-22T14:00:00.000Z
  -> temp/agent.md
  -> temp/agent.json
```

Markdown 包含：总榜（69 行）、4 个信号 Top 15、Pareto 前沿、7 条 FAQ。

### 3. 调试（站点改版时用）

```bash
# 页面里有哪些大 JSON 对象
python .../extract_leaderboard.py page.html --list
#   158,136  snapshot     <- 模型行 + 信号定义
#    86,002  indexBoard   <- Index 分数 + CI + 分信号
#    37,108  costStats    <- 成本/耗时/Token 分位

# 直接看原始对象
python .../extract_leaderboard.py page.html --grab costStats --json
```

---

## 示例输出

[`example-output.md`](example-output.md) 是上面第 2 步的一份真实产出（2026-11-22 快照，69 个模型，224 行）：总榜 → 4 个信号 Top 15 → Pareto 前沿 → FAQ。可直接对照它确认字段含义，不用先跑一遍抓取。

只放了 Markdown 没放 JSON（后者 75 KB 纯数据），需要时一条命令就能生成：

```bash
python examples/arena-live-leaderboard/extract_leaderboard.py temp/agent.html --out-json temp/agent.json
```

---

## 输出结构（`*.json`）

```jsonc
{
  "source": "https://arena.ai/leaderboard/agent",
  "page_title": "Agent Arena Leaderboard: ...",
  "last_updated": "2026-11-22T14:00:00.000Z",   // 榜单快照时间（判断新鲜度看这个）
  "cost_window_days": 14,
  "total_sessions": 3175883,
  "signals": [
    // weight=null 表示该信号只统计、不参与 Index 加权（当前是 Tool Hallucination）
    {"name": "Task Completion", "weight": 1, "observations": 1720678, "description": "..."}
  ],
  "models": [
    {
      "rank": 1,
      "model": "Claude Fable 5.2 (Max)",
      "contender": "claude-fable-5.2-max-n8f7",
      "org": "Anthropic",
      "license": "Proprietary",
      "model_url": "https://www.anthropic.com/...",
      "index": 1448.5,
      "ci_lower": 1409.82, "ci_upper": 1487.19,
      "cost_usd_p50": 9.163, "cost_usd_p25": 2.3817, "cost_usd_p95": 99.0313,
      "time_ms_p50": 1159988,
      "sessions": 12418, "observations": 2888793,
      "signals": {"Task Completion": {"index": 1348.4, "raw": 0.229135}, ...}
    }
  ],
  "pareto_frontier": [{"model": "...", "index": 1449, "usd": 9.16}, ...],
  "faq": {"@type": "FAQPage", "mainEntity": [...]}
}
```

> `index` 保留 2 位小数，页面显示值是四舍五入后的整数；两者不要混用。

---

## 实时性

- 页面数据由 `snapshot.lastUpdated` 标记（通常是当天 14:00Z 的一次批量更新），抓取本身是即时的。
- 想持续跟踪，可定时跑这两步并对比 `last_updated`：

```bash
while true; do
  bash fetch_leaderboard.sh https://arena.ai/leaderboard/agent temp/agent.html 20 \
       --proxy-server=http://127.0.0.1:7890 >/dev/null
  python extract_leaderboard.py temp/agent.html --out-json temp/agent.json >/dev/null
  echo "$(date -Iseconds) $(python -c "import json;print(json.load(open('temp/agent.json'))['last_updated'])")"
  sleep 3600
done
```

- 每次抓取会启动/销毁一个 headless Chromium，约 25-35 秒；不要并发太多，容易被 WAF 盯上。

---

## 支持的榜单

| URL | 说明 |
|---|---|
| `/leaderboard/agent` | Agent Arena Index（69 模型，4 信号） |
| `/leaderboard/chat/text` | Text Arena（注意 `/leaderboard/text` 会 302 到这里） |

其他 `board` 页面的页面结构与字段契约基本一致，`--list` 先确认有 `snapshot` / `indexBoard` / `costStats` 三个对象即可。

---

## 常见问题

| 现象 | 处理 |
|---|---|
| `title` 是 `请稍候…` / `Just a moment...` | 挑战没跑完：`waitSec` 加到 25-30，确认挂了代理 |
| `title` 是 `Attention Required!` | WAF 硬封禁，换出口；**不要**改成直连 |
| node 无输出，最后 `unsettled top-level await` | 少了 `--remote-allow-origins=*`（Chrome 111+ 的 Origin 校验） |
| HTML 是 0 字节 | 用了 `--dump-dom`，本 build 不支持，必须走 CDP |
| 一堆 `Device or resource busy` | chrome 未退干净就删 profile，脚本已加 `sleep 1`；仍报可忽略 |
| `KeyError: 'snapshot'` / `modelKey` 缺失 | 站点改版，用 `--list` 看新字段名，改 `collect()` |
| 数值和页面差 0.01 | 页面卡片有自己的取整逻辑；以 DOM 展示值为准，JSON 里是原始分位值 |
| 想清理卡住的浏览器 | **只**用 `taskkill //PID <pid> //T //F`；`taskkill //IM chrome.exe` 会杀掉用户自己的浏览器 |

---

## 已知局限

- 只抓单页最终 DOM，不点分页、不滚动、不展开 "View all" 之外的交互。
- `pareto_frontier` 只覆盖页面 `On the frontier` 卡片展示的点，不是全部 69 个模型的散点坐标。
- 模型详情页（`/leaderboard/agent/models/<slug>`）未抓取。
- 脚本以 Windows 为目标平台；换 Linux/macOS 需替换 `taskkill` / `cygpath`。
