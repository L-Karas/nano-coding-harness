"""arena.ai 实时排行榜 → 结构化 JSON + 可读 Markdown。

输入是 fetch_leaderboard.sh 抓到的页面 HTML；数据不在 DOM 文本里，而在
Next.js 的 RSC payload（self.__next_f.push）和 JSON-LD 的 FAQPage 里。

用法:
    # 标准用法：生成 arena-leaderboard.md / arena-leaderboard.json
    python extract_leaderboard.py page.html

    # 指定输出
    python extract_leaderboard.py page.html --out-md report.md --out-json data.json

    # 调试：页面里有哪些大 JSON 对象
    python extract_leaderboard.py page.html --list

    # 调试：直接取某个对象
    python extract_leaderboard.py page.html --grab costStats --json

支持的页面: https://arena.ai/leaderboard/<board>
已知: /leaderboard/agent（Agent 榜）、/leaderboard/chat/text（Text 榜）
字段名（snapshot / indexBoard / costStats）是 arena.ai 的页面契约，站点改版时
优先改 collect()。
"""
from __future__ import annotations

import argparse
import html as htmlmod
import json
import re
import sys
from pathlib import Path

PUSH_KEY = "self.__next_f.push([1,"


# ---------------------------------------------------------------------------
# RSC / JSON-LD 提取
# ---------------------------------------------------------------------------
def extract_flight(html: str) -> str:
    """拼接所有 self.__next_f.push([1,"..."]) 的字符串负载。"""
    dec = json.JSONDecoder()
    chunks: list[str] = []
    pos = 0
    while True:
        i = html.find(PUSH_KEY, pos)
        if i < 0:
            break
        start = i + len(PUSH_KEY)
        try:
            s, end = dec.raw_decode(html, start)
            if isinstance(s, str):
                chunks.append(s)
            pos = end
        except ValueError:
            pos = start + 1
    return "".join(chunks)


def extract_jsonld(html: str) -> list:
    """提取所有 <script type="application/ld+json"> 块。"""
    dec = json.JSONDecoder()
    out = []
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>', html):
        try:
            obj, _ = dec.raw_decode(html, m.end())
            out.append(obj)
        except ValueError:
            continue
    return out


def grab(flight: str, key: str):
    """取 `"key": {...}` 的对象。"""
    quoted = f'"{key}":'
    i = flight.find(quoted)
    if i < 0:
        raise KeyError(f'flight 里没有 "{key}":')
    j = flight.find("{", i + len(quoted))
    obj, _ = json.JSONDecoder().raw_decode(flight, j)
    return obj


def list_objects(flight: str, min_chars: int = 2000) -> list[tuple[str, int]]:
    """扫描 `"key":{...}`，按 JSON 字符数降序列出。"""
    dec = json.JSONDecoder()
    best: dict[str, int] = {}
    for m in re.finditer(r'"([A-Za-z_][A-Za-z0-9_]{2,48})":\{', flight):
        try:
            obj, _ = dec.raw_decode(flight, m.end() - 1)
        except ValueError:
            continue
        size = len(json.dumps(obj, ensure_ascii=False))
        if size >= min_chars:
            key = m.group(1)
            best[key] = max(best.get(key, 0), size)
    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)


# ---------------------------------------------------------------------------
# 页面解析
# ---------------------------------------------------------------------------
def detect_source(html: str) -> tuple[str, str]:
    """返回 (canonical url, page title)。"""
    url = ""
    # 不依赖属性顺序：先找带 rel="canonical" 的 link 标签，再取它的 href
    for tag in re.findall(r"<link\b[^>]*>", html):
        if 'rel="canonical"' in tag:
            m = re.search(r'href="([^"]+)"', tag)
            if m:
                url = m.group(1)
                break
    t = re.search(r"<title>(.*?)</title>", html, re.S)
    title = htmlmod.unescape(re.sub(r"\s+", " ", t.group(1)).strip()) if t else ""
    return url, title


def parse_frontier(html: str) -> list[dict]:
    """解析 DOM 里 `On the frontier` 卡片的 <ol> 列表（页面展示的 Pareto 点）。"""
    i = html.find("On the frontier")
    if i < 0:
        return []
    j = html.find("<ol", i)
    k = html.find("</ol>", j)
    out = []
    for raw in re.split(r"<li\b", html[j:k])[1:]:
        t = re.sub(r"<svg.*?</svg>", "", raw, flags=re.S)
        t = re.sub(r"<title>.*?</title>", "", t, flags=re.S)
        t = re.sub(r"<[^>]+>", "", t)
        t = re.sub(r"\s+", " ", t).strip()
        # <li> 已被 split 切掉，剩下一段属性文本（class="..."），用第一个 > 截掉
        t = re.sub(r"^[^<>]*>", "", t)
        m = re.match(r"^(.+?)([\d,]{3,})\$([\d.]+)$", t)
        if m:
            out.append(
                {"model": m.group(1).strip(), "index": int(m.group(2).replace(",", "")), "usd": float(m.group(3))}
            )
    return out


def collect(html: str) -> dict:
    flight = extract_flight(html)
    snapshot = grab(flight, "snapshot")
    index_board = grab(flight, "indexBoard")
    cost_stats = grab(flight, "costStats")

    rows = {r["contenderName"]: r for r in snapshot["rows"]}
    cost = {c["contenderName"]: c for c in cost_stats["entries"]}
    weights = index_board["index"]["inputs"]
    sig_defs = {s["name"]: s for s in snapshot["signals"]}
    label = {w["signal"]: sig_defs[w["signal"]]["displayName"] for w in weights}

    models = []
    for e in index_board["index"]["entries"]:
        r = rows.get(e["contenderName"], {})
        c = cost.get(e["contenderName"], {})
        time_ms = (c.get("timePerTask") or {}).get("medianMs")
        ci_lo = e.get("indexScoreCiLower")
        ci_hi = e.get("indexScoreCiUpper")
        models.append(
            {
                "rank": e["rank"],
                "model": r.get("model") or e.get("modelKey") or "?",
                "contender": e["contenderName"],
                "org": r.get("modelOrganization"),
                "license": r.get("license"),
                "model_url": r.get("modelUrl"),
                "index": round(e["indexScore"], 2),
                "ci_lower": round(ci_lo, 2) if ci_lo is not None else None,
                "ci_upper": round(ci_hi, 2) if ci_hi is not None else None,
                "cost_usd_p50": round(c["medianUsd"], 4) if c else None,
                "cost_usd_p25": round(c["p25Usd"], 4) if c else None,
                "cost_usd_p95": round(c["p95Usd"], 4) if c else None,
                "time_ms_p50": time_ms,
                "sessions": r.get("sessions"),
                "observations": r.get("observations"),
                "signals": {
                    label[name]: {
                        "index": round(e["signals"][name]["indexScore"], 2),
                        "raw": round(e["signals"][name]["rawScore"], 6),
                    }
                    for name in label
                    if name in e["signals"]
                },
            }
        )

    source, title = detect_source(html)
    faq = next((b for b in extract_jsonld(html) if b.get("@type") == "FAQPage"), None)

    return {
        "source": source,
        "page_title": title,
        "last_updated": snapshot["lastUpdated"],
        "cost_window_days": cost_stats["costWindowDays"],
        "total_sessions": snapshot["totalSessions"],
        "total_observations": snapshot["totalObservations"],
        "model_count": snapshot["modelCount"],
        # 一个列表就够：weight 为 None 表示该信号只统计、不参与 Index 加权
        "signals": [
            {
                "name": s["displayName"],
                "weight": next((w["weight"] for w in weights if w["signal"] == s["name"]), None),
                "observations": s["observationCount"],
                "description": s["description"],
            }
            for s in snapshot["signals"]
        ],
        "models": models,
        "pareto_frontier": parse_frontier(html),
        "faq": faq,
    }


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------
def _int(v):
    # 不用 format 的 .0f：它走 banker's rounding，1448.5 会变 1448
    return f"{int(v + 0.5):,}" if v is not None else "—"


def _money(v):
    return f"${v:,.2f}" if v is not None else "—"


def _min(ms):
    return f"{ms / 60000:.1f} min" if ms else "—"


def _ci(m):
    if m["ci_lower"] is None or m["ci_upper"] is None:
        return "—"
    half = (m["ci_upper"] - m["ci_lower"]) / 2
    return f"±{int(half + 0.5)}"


def build_markdown(d: dict) -> str:
    L: list[str] = []
    add = L.append

    add(f"# {d['page_title'] or 'arena.ai Leaderboard'}")
    add("")
    add(f"> **来源**：{d['source']}")
    add(f"> **榜单快照**：`{d['last_updated']}`")
    add(f"> **规模**：{d['total_sessions']:,} sessions · "
        f"{d['total_observations']:,} observations · {d['model_count']} models")
    add(f"> **成本/耗时窗口**：最近 {d['cost_window_days']} 天中位数（P50）")
    add("")
    add("---")
    add("")

    # 1. 榜单
    add(f"## 1. 总榜（{len(d['models'])} 个模型）")
    add("")
    weighted = [s for s in d["signals"] if s["weight"] is not None]
    add("Index 加权信号：" + "、".join(f"`{s['name']}` ×{s['weight']}" for s in weighted) + "。")
    add("")
    add("| # | Model | Org | License | Index | ±95%CI | $/task (P50) | Time/task (P50) | Sessions |")
    add("|---:|---|---|---|---:|---:|---:|---:|---:|")
    for m in d["models"]:
        add(f"| {m['rank']} | {m['model']} | {m['org'] or '—'} | {m['license'] or '—'} "
            f"| {_int(m['index'])} | {_ci(m)} | {_money(m['cost_usd_p50'])} "
            f"| {_min(m['time_ms_p50'])} | {_int(m['sessions'])} |")
    add("")

    # 2. 信号
    add("## 2. 信号表现（Top 15）")
    add("")
    add("| 信号 | 权重 | observations | 说明 |")
    add("|---|---:|---:|---|")
    for s in d["signals"]:
        add(f"| {s['name']} | {s['weight'] if s['weight'] is not None else '—'} "
            f"| {s['observations']:,} | {s['description']} |")
    add("")
    for n, sig in enumerate(weighted, 1):
        name = sig["name"]
        add(f"### 2.{n} {name}（Top 15）")
        add("")
        add("| # | Model | Signal Index | Raw score |")
        add("|---:|---|---:|---:|")
        ranked = sorted(d["models"], key=lambda m: m["signals"].get(name, {}).get("index", 0), reverse=True)
        for i, m in enumerate(ranked[:15], 1):
            sg = m["signals"].get(name, {})
            add(f"| {i} | {m['model']} | {_int(sg.get('index'))} | {sg.get('raw', 0) * 100:.1f}% |")
        add("")

    # 3. Pareto
    if d["pareto_frontier"]:
        add("## 3. Pareto 前沿（Index vs 成本，页面展示值）")
        add("")
        add("| # | Model | Index | $/task (P50) |")
        add("|---:|---|---:|---:|")
        for i, f in enumerate(d["pareto_frontier"], 1):
            add(f"| {i} | {f['model']} | {_int(f['index'])} | ${f['usd']:.2f} |")
        add("")

    # 4. FAQ
    add("## 4. FAQ")
    add("")
    if d["faq"]:
        for qa in d["faq"]["mainEntity"]:
            add(f"### {qa['name']}")
            add("")
            add(qa["acceptedAnswer"]["text"])
            add("")
    else:
        add("(本页没有 FAQPage 结构化数据)")
        add("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
def run_debug(html: str, args) -> None:
    """--list / --grab：站点改版时用来确认字段名。"""
    flight = extract_flight(html)
    print(f"html: {len(html):,} chars · flight: {len(flight):,} chars", file=sys.stderr)
    if args.list:
        for key, size in list_objects(flight):
            print(f"{size:>10,}  {key}", file=sys.stderr)
    if args.grab:
        obj = grab(flight, args.grab)
        if args.json:
            print(json.dumps(obj, ensure_ascii=False, indent=2))
        else:
            print(f"{args.grab}: {len(json.dumps(obj)):,} chars", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="arena.ai 排行榜 HTML → JSON + Markdown")
    ap.add_argument("html", help="fetch_leaderboard.sh 抓到的 HTML")
    ap.add_argument("--out-md", default="arena-leaderboard.md")
    ap.add_argument("--out-json", default="arena-leaderboard.json")
    ap.add_argument("--list", action="store_true", help="只列出 RSC 里的大 JSON 对象")
    ap.add_argument("--grab", help="只取指定 key 的 JSON 对象")
    ap.add_argument("--json", action="store_true", help="--grab 结果打 JSON 到 stdout")
    args = ap.parse_args(argv)

    html = Path(args.html).read_text(encoding="utf-8", errors="replace")

    if args.grab or args.list:
        run_debug(html, args)
        return 0

    data = collect(html)
    Path(args.out_md).write_text(build_markdown(data), encoding="utf-8")
    Path(args.out_json).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{data['model_count']} models @ {data['last_updated']}")
    print(f"  -> {args.out_md}")
    print(f"  -> {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
