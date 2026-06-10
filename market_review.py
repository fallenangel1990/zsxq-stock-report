"""A 股盘后复盘任务。

按大盘、板块、资金、个股、策略、仓位、新闻和明日计划生成结构化复盘。
"""

import json
import math
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from sector_monitor import (
    _money_yi,
    _request_json,
    _safe_float,
    capture_market_signals,
    evaluate_market_environment,
    fetch_boards,
    fetch_market_indices,
)


STATE_FILE = Path(__file__).parent / "data" / "state" / "market_review.json"
A_SHARE_FIELDS = "f12,f14,f3,f6,f8,f20,f62,f100"
A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
EASTMONEY_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _fmt_pct(value) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def _fmt_yi(value) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}亿"


def _load_previous_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_review_state(snapshot: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_a_share_snapshot(limit_pages: int = 20, page_size: int = 300) -> list[dict]:
    """抓取全 A 快照，用于市场宽度、涨停跌停和风格判断。

    东方财富偶发 502 时，返回已获取的部分样本；若首页不可用则返回空列表，
    后续由 summarize_breadth() 使用主要指数成分做降级近似，避免复盘任务中断。
    """
    stocks = []
    for page in range(1, limit_pages + 1):
        params = {
            "pn": page,
            "pz": page_size,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": A_SHARE_FS,
            "fields": A_SHARE_FIELDS,
        }
        data = _request_json(params, timeout=15)
        rows = data.get("data", {}).get("diff", []) or []
        if not rows:
            if page == 1:
                print("[复盘] 东方财富全A快照首页不可用，将使用指数成分兜底。", flush=True)
            else:
                print(f"[复盘] 东方财富全A快照第 {page} 页为空，使用已获取的 {len(stocks)} 只样本。", flush=True)
            break
        for raw in rows:
            stocks.append({
                "code": raw.get("f12", ""),
                "name": raw.get("f14", ""),
                "change_pct": _safe_float(raw.get("f3")),
                "amount_yi": _money_yi(raw.get("f6")),
                "turnover_rate": _safe_float(raw.get("f8")),
                "market_cap_yi": _money_yi(raw.get("f20")),
                "main_net_yi": _money_yi(raw.get("f62")),
                "sector": raw.get("f100", ""),
            })
        total = data.get("data", {}).get("total")
        if total and len(stocks) >= int(total):
            break
    return stocks


def summarize_breadth(stocks: list[dict], market: Optional[dict] = None) -> dict:
    market = market or {}
    if not stocks:
        up = int(market.get("total_up") or 0)
        down = int(market.get("total_down") or 0)
        flat = int(market.get("total_flat") or 0)
        source = market.get("breadth_source") or "上证指数+深证成指"
        money_effect = "强" if up > down * 1.5 else "弱" if down > up * 1.3 else "中性"
        return {
            "total": up + down + flat,
            "up": up,
            "down": down,
            "flat": flat,
            "limit_up": 0,
            "limit_down": 0,
            "total_amount_yi": market.get("total_amount_yi", 0),
            "avg_turnover_rate": 0.0,
            "money_effect": money_effect,
            "source": source,
            "data_status": f"全A快照不可用，已使用{source}涨跌家数近似；涨停/跌停和平均换手率暂不可用。",
        }

    up = sum(1 for s in stocks if s.get("change_pct", 0) > 0)
    down = sum(1 for s in stocks if s.get("change_pct", 0) < 0)
    flat = max(0, len(stocks) - up - down)
    limit_up = sum(1 for s in stocks if s.get("change_pct", 0) >= 9.8)
    limit_down = sum(1 for s in stocks if s.get("change_pct", 0) <= -9.8)
    total_amount = round(sum(s.get("amount_yi", 0) for s in stocks), 2)
    avg_turnover = 0.0
    turnover_values = [s.get("turnover_rate", 0) for s in stocks if s.get("turnover_rate")]
    if turnover_values:
        avg_turnover = round(sum(turnover_values) / len(turnover_values), 2)
    money_effect = "强" if up > down * 1.5 else "弱" if down > up * 1.3 else "中性"
    data_status = "正常" if len(stocks) >= 3000 else f"全A快照仅获取 {len(stocks)} 只样本，按已获取样本计算。"
    return {
        "total": len(stocks),
        "up": up,
        "down": down,
        "flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "total_amount_yi": total_amount,
        "avg_turnover_rate": avg_turnover,
        "money_effect": money_effect,
        "source": "东方财富全A快照",
        "data_status": data_status,
    }


def _request_lhb_json(trade_date: str, page_size: int = 80) -> dict:
    params = {
        "sortColumns": "BILLBOARD_NET_AMT",
        "sortTypes": "-1",
        "pageSize": page_size,
        "pageNumber": 1,
        "reportName": "RPT_DAILYBILLBOARD_DETAILS",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": "https://data.eastmoney.com/stock/tradedetail.html",
        "Accept": "application/json, text/plain, */*",
    }
    resp = requests.get(EASTMONEY_DATACENTER_URL, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_lhb_details(max_days: int = 10, page_size: int = 80) -> dict:
    """抓取最近一个有数据交易日的龙虎榜明细。"""
    last_error = ""
    today = _now_shanghai().date()
    for offset in range(max_days):
        trade_date = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        try:
            data = _request_lhb_json(trade_date, page_size=page_size)
            result = data.get("result") or {}
            rows = result.get("data", []) or []
        except Exception as exc:
            last_error = str(exc)
            print(f"[复盘] 龙虎榜 {trade_date} 获取失败: {exc}", flush=True)
            continue
        if not rows:
            continue
        normalized = []
        for row in rows:
            normalized.append({
                "date": (row.get("TRADE_DATE") or trade_date)[:10],
                "code": row.get("SECURITY_CODE", ""),
                "name": row.get("SECURITY_NAME_ABBR", ""),
                "change_pct": _safe_float(row.get("CHANGE_RATE")),
                "turnover_rate": _safe_float(row.get("TURNOVERRATE")),
                "buy_yi": _money_yi(row.get("BILLBOARD_BUY_AMT")),
                "sell_yi": _money_yi(row.get("BILLBOARD_SELL_AMT")),
                "net_yi": _money_yi(row.get("BILLBOARD_NET_AMT")),
                "deal_yi": _money_yi(row.get("BILLBOARD_DEAL_AMT")),
                "deal_ratio": _safe_float(row.get("DEAL_AMOUNT_RATIO")),
                "reason": row.get("EXPLANATION", ""),
                "explain": row.get("EXPLAIN", ""),
                "market": row.get("TRADE_MARKET", ""),
            })
        return {
            "date": normalized[0]["date"],
            "rows": normalized,
            "source": "东方财富龙虎榜",
            "data_status": "正常",
        }
    return {
        "date": "",
        "rows": [],
        "source": "东方财富龙虎榜",
        "data_status": f"龙虎榜数据暂不可用{f'：{last_error}' if last_error else ''}",
    }


def summarize_lhb(lhb: dict) -> dict:
    rows = lhb.get("rows", []) or []
    if not rows:
        return {
            "date": lhb.get("date", ""),
            "count": 0,
            "row_count": 0,
            "total_buy_yi": 0.0,
            "total_sell_yi": 0.0,
            "total_net_yi": 0.0,
            "total_deal_yi": 0.0,
            "net_buy_count": 0,
            "net_sell_count": 0,
            "top_buy": [],
            "top_sell": [],
            "reason_counts": [],
            "data_status": lhb.get("data_status", "龙虎榜数据暂不可用"),
        }
    reason_counter = Counter((row.get("reason") or "未分类").split("，")[0][:24] for row in rows)
    total_buy = round(sum(row.get("buy_yi", 0) for row in rows), 2)
    total_sell = round(sum(row.get("sell_yi", 0) for row in rows), 2)
    total_deal = round(sum(row.get("deal_yi", 0) for row in rows), 2)
    total_net = round(sum(row.get("net_yi", 0) for row in rows), 2)
    unique_rows = {}
    for row in rows:
        key = row.get("code") or row.get("name")
        if not key:
            continue
        old = unique_rows.get(key)
        if old is None or abs(row.get("net_yi", 0)) > abs(old.get("net_yi", 0)):
            unique_rows[key] = row
    display_rows = list(unique_rows.values())
    return {
        "date": lhb.get("date", ""),
        "count": len(display_rows),
        "row_count": len(rows),
        "total_buy_yi": total_buy,
        "total_sell_yi": total_sell,
        "total_net_yi": total_net,
        "total_deal_yi": total_deal,
        "net_buy_count": sum(1 for row in display_rows if row.get("net_yi", 0) > 0),
        "net_sell_count": sum(1 for row in display_rows if row.get("net_yi", 0) < 0),
        "top_buy": sorted(
            [row for row in display_rows if row.get("net_yi", 0) > 0],
            key=lambda row: row.get("net_yi", 0),
            reverse=True,
        )[:5],
        "top_sell": sorted(
            [row for row in display_rows if row.get("net_yi", 0) < 0],
            key=lambda row: row.get("net_yi", 0),
        )[:5],
        "reason_counts": reason_counter.most_common(5),
        "data_status": lhb.get("data_status", "正常"),
    }


def style_bias(stocks: list[dict]) -> str:
    """粗略判断市场风格：大盘蓝筹/成长/中小盘。"""
    if not stocks:
        return "未知"
    large = [s for s in stocks if s.get("market_cap_yi", 0) >= 500]
    mid_small = [s for s in stocks if 0 < s.get("market_cap_yi", 0) < 200]
    growth_keywords = ("AI", "人工智能", "算力", "半导体", "芯片", "机器人", "新能源", "创新药")
    growth = [s for s in stocks if any(k in (s.get("sector") or "") for k in growth_keywords)]

    def avg_change(items):
        values = [s.get("change_pct", 0) for s in items]
        return sum(values) / len(values) if values else -99

    scores = {
        "大盘蓝筹": avg_change(large),
        "成长股": avg_change(growth),
        "中小盘": avg_change(mid_small),
    }
    return max(scores.items(), key=lambda item: item[1])[0]


def compare_volume(today_amount: float, previous: dict) -> str:
    prev_amount = previous.get("total_amount_yi")
    if not prev_amount:
        return "无昨日缓存，暂无法比较"
    delta = today_amount - prev_amount
    pct = delta / prev_amount * 100 if prev_amount else 0
    direction = "放大" if delta > 0 else "缩小" if delta < 0 else "持平"
    return f"较上次复盘{direction} {abs(delta):.2f} 亿（{pct:+.2f}%）"


def emotion_score(market: dict, breadth: dict, boards: list[dict]) -> float:
    score = 5.0
    score += (market.get("avg_change_pct", 0) or 0) * 1.2
    if breadth.get("up") and breadth.get("down"):
        score += max(-2.0, min(2.0, math.log((breadth["up"] + 1) / (breadth["down"] + 1)) * 1.5))
    score += min(1.5, breadth.get("limit_up", 0) / 40)
    score -= min(1.0, breadth.get("limit_down", 0) / 20)
    strong_boards = sum(1 for b in boards if b.get("signal_level") == "强异动/疑似建仓")
    score += min(1.0, strong_boards * 0.35)
    return round(max(1.0, min(10.0, score)), 1)


def load_watchlist_performance() -> list[dict]:
    """用最近一次推荐股票近似自选股表现。"""
    try:
        from storage import load_latest_stock_data
        from price_fetcher import fetch_prices
        stocks, _ = load_latest_stock_data()
        codes = [s.get("code") for s in stocks if s.get("code")]
        prices = fetch_prices(codes)
    except Exception:
        return []

    rows = []
    for stock in stocks[:30]:
        code = stock.get("code")
        quote = prices.get(code, {}) if code else {}
        if not code or not quote:
            continue
        rows.append({
            "name": stock.get("name", ""),
            "code": code,
            "change_pct": quote.get("change_pct"),
            "score": stock.get("score"),
            "action": stock.get("action", ""),
            "entry_ref": stock.get("entry_ref", ""),
            "exit_trigger": stock.get("exit_trigger", ""),
        })
    rows.sort(key=lambda r: r.get("change_pct") or 0, reverse=True)
    return rows


def _strongest_and_weakest_boards(boards: list[dict]) -> tuple[list[dict], list[dict]]:
    strongest = sorted(
        boards,
        key=lambda b: (b.get("signal_score", 0), b.get("change_pct", 0), b.get("main_net_yi", 0)),
        reverse=True,
    )[:5]
    weakest = sorted(boards, key=lambda b: (b.get("change_pct", 0), b.get("main_net_yi", 0)))[:5]
    return strongest, weakest


def summarize_board_stats(boards: list[dict]) -> dict:
    if not boards:
        return {
            "total": 0,
            "industry_count": 0,
            "concept_count": 0,
            "up": 0,
            "down": 0,
            "flat": 0,
            "strong_count": 0,
            "weak_count": 0,
            "main_in_count": 0,
            "main_out_count": 0,
            "total_amount_yi": 0.0,
            "total_main_net_yi": 0.0,
            "avg_up_ratio": 0.0,
            "top_amount": [],
            "top_inflow": [],
            "top_outflow": [],
            "data_status": "板块数据不可用",
        }

    up = sum(1 for board in boards if board.get("change_pct", 0) > 0)
    down = sum(1 for board in boards if board.get("change_pct", 0) < 0)
    flat = max(0, len(boards) - up - down)
    top_amount = sorted(boards, key=lambda b: b.get("amount_yi", 0), reverse=True)[:5]
    top_inflow = sorted(boards, key=lambda b: b.get("main_net_yi", 0), reverse=True)[:5]
    top_outflow = sorted(boards, key=lambda b: b.get("main_net_yi", 0))[:5]
    return {
        "total": len(boards),
        "industry_count": sum(1 for board in boards if board.get("type") == "industry"),
        "concept_count": sum(1 for board in boards if board.get("type") == "concept"),
        "up": up,
        "down": down,
        "flat": flat,
        "strong_count": sum(1 for board in boards if board.get("change_pct", 0) >= 2 and board.get("up_ratio", 0) >= 60),
        "weak_count": sum(1 for board in boards if board.get("change_pct", 0) <= -2),
        "main_in_count": sum(1 for board in boards if board.get("main_net_yi", 0) > 0),
        "main_out_count": sum(1 for board in boards if board.get("main_net_yi", 0) < 0),
        "total_amount_yi": round(sum(board.get("amount_yi", 0) for board in boards), 2),
        "total_main_net_yi": round(sum(board.get("main_net_yi", 0) for board in boards), 2),
        "avg_up_ratio": round(sum(board.get("up_ratio", 0) for board in boards) / len(boards), 2),
        "top_amount": top_amount,
        "top_inflow": top_inflow,
        "top_outflow": top_outflow,
        "data_status": "正常",
    }


def _append_table(lines: list[str], headers: list[str], rows: list[list[str]]) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(v) for v in row) + " |")


def build_review_report(
    indices: list[dict],
    market: dict,
    breadth: dict,
    all_boards: list[dict],
    board_stats: dict,
    lhb_summary: dict,
    signal_boards: list[dict],
    watchlist: list[dict],
    previous: dict,
) -> str:
    now = _now_shanghai().strftime("%Y-%m-%d %H:%M:%S 北京时间")
    volume_desc = compare_volume(breadth.get("total_amount_yi", 0), previous)
    style = style_bias_from_boards(all_boards) or "未知"
    emotion = emotion_score(market, breadth, signal_boards)
    strongest, weakest = _strongest_and_weakest_boards(all_boards)
    top_signal = signal_boards[0] if signal_boards else (strongest[0] if strongest else {})
    breadth_source = breadth.get("source", "东方财富全A快照")
    breadth_status = breadth.get("data_status", "正常")
    market_status = market.get("data_status", "正常")
    board_status = board_stats.get("data_status", "正常")
    lhb_status = lhb_summary.get("data_status", "正常")
    data_status = "；".join(
        status for status in (market_status, breadth_status, board_status, lhb_status)
        if status and status != "正常"
    ) or "正常"
    amount_label = "全A约" if breadth_source == "东方财富全A快照" else "主要指数合计约"

    lines = [
        "# A股盘后复盘报告",
        "",
        f"> 生成时间: {now}",
        "> 数据源: 东方财富指数/板块/个股快照/龙虎榜；真实持仓需接入券商或手工持仓数据后补齐。",
        f"> 数据完整性: {data_status}",
        "",
        "## 一、大盘行情与市场情绪",
        "",
        f"- 指数环境: **{market.get('level', '未知')}**，大盘评分 {market.get('score', 0)} / 100。",
        f"- 今日成交额: {amount_label} **{_fmt_yi(breadth.get('total_amount_yi'))}**；{volume_desc}。",
        f"- 市场宽度: {breadth.get('up', 0)} 涨 / {breadth.get('down', 0)} 跌 / {breadth.get('flat', 0)} 平，赚钱效应 **{breadth.get('money_effect', '未知')}**。",
        f"- 市场风格: **{style}**；平均换手率约 {breadth.get('avg_turnover_rate', 0):.2f}%。",
        f"- 涨停/跌停: 涨停 {breadth.get('limit_up', 0)} 只，跌停 {breadth.get('limit_down', 0)} 只；连板数据待接入涨停池数据源。",
        f"- 主线情绪评分: **{emotion} / 10**。",
        "",
    ]
    _append_table(
        lines,
        ["指数", "收盘", "涨跌幅", "成交额", "上涨家数占比"],
        [
            [i["name"], f"{i['price']:.2f}", _fmt_pct(i["change_pct"]), _fmt_yi(i["amount_yi"]), f"{i['up_ratio']:.1f}%"]
            for i in indices
        ],
    )

    lines.extend([
        "",
        "## 二、板块与题材",
        "",
        f"- 今日最强势板块: **{top_signal.get('name', '暂无')}**。",
        f"- 主要驱动: {top_signal.get('logic_hint', '资金扩散与板块强度待确认')}",
        f"- 板块接力: 主力净流入 {_fmt_yi(top_signal.get('main_net_yi'))}，上涨家数占比 {top_signal.get('up_ratio', 0):.1f}%。",
        f"- 板块统计: 共 {board_stats.get('total', 0)} 个板块（行业 {board_stats.get('industry_count', 0)} / 概念 {board_stats.get('concept_count', 0)}），{board_stats.get('up', 0)} 涨 / {board_stats.get('down', 0)} 跌 / {board_stats.get('flat', 0)} 平。",
        f"- 板块强弱: 强势扩散 {board_stats.get('strong_count', 0)} 个，弱势下跌 {board_stats.get('weak_count', 0)} 个；平均上涨家数占比 {board_stats.get('avg_up_ratio', 0):.1f}%。",
        f"- 板块资金: 总成交 {_fmt_yi(board_stats.get('total_amount_yi'))}，主力净流入合计 {_fmt_yi(board_stats.get('total_main_net_yi'))}；主力净流入 {board_stats.get('main_in_count', 0)} 个 / 净流出 {board_stats.get('main_out_count', 0)} 个。",
        "",
    ])
    _append_table(
        lines,
        ["板块", "涨幅", "主力净流入", "上涨家数占比", "龙头/领涨", "信号"],
        [
            [
                b["name"],
                _fmt_pct(b["change_pct"]),
                _fmt_yi(b["main_net_yi"]),
                f"{b['up_ratio']:.1f}%",
                f"{b.get('leader_name', '-')}",
                b.get("signal_level", "-"),
            ]
            for b in strongest
        ],
    )
    lines.extend(["", "弱势板块观察："])
    _append_table(
        lines,
        ["板块", "涨幅", "主力净流入", "可能原因"],
        [
            [b["name"], _fmt_pct(b["change_pct"]), _fmt_yi(b["main_net_yi"]), "资金流出或缺少领涨扩散"]
            for b in weakest
        ],
    )
    lines.extend(["", "成交额前五板块："])
    _append_table(
        lines,
        ["板块", "类型", "成交额", "涨幅", "主力净流入", "上涨家数占比"],
        [
            [
                b["name"],
                "行业" if b.get("type") == "industry" else "概念",
                _fmt_yi(b.get("amount_yi")),
                _fmt_pct(b.get("change_pct")),
                _fmt_yi(b.get("main_net_yi")),
                f"{b.get('up_ratio', 0):.1f}%",
            ]
            for b in board_stats.get("top_amount", [])
        ],
    )
    lines.extend(["", "主力净流入/流出排行："])
    _append_table(
        lines,
        ["方向", "板块", "涨幅", "主力净流入", "成交额", "领涨股"],
        [
            ["流入", b["name"], _fmt_pct(b.get("change_pct")), _fmt_yi(b.get("main_net_yi")), _fmt_yi(b.get("amount_yi")), b.get("leader_name", "-")]
            for b in board_stats.get("top_inflow", [])[:3]
        ] + [
            ["流出", b["name"], _fmt_pct(b.get("change_pct")), _fmt_yi(b.get("main_net_yi")), _fmt_yi(b.get("amount_yi")), b.get("leader_name", "-")]
            for b in board_stats.get("top_outflow", [])[:3]
        ],
    )

    lines.extend([
        "",
        "## 三、龙虎榜与短线资金",
        "",
        f"- 板块主力资金主要押注: {_format_board_names([b for b in strongest if b.get('main_net_yi', 0) > 0], 5)}。",
        f"- 龙虎榜日期: {lhb_summary.get('date') or '暂无'}；上榜 {lhb_summary.get('count', 0)} 只（明细 {lhb_summary.get('row_count', 0)} 条），净买入 {lhb_summary.get('net_buy_count', 0)} 只 / 净卖出 {lhb_summary.get('net_sell_count', 0)} 只。",
        f"- 龙虎榜金额: 买入合计 {_fmt_yi(lhb_summary.get('total_buy_yi'))}，卖出合计 {_fmt_yi(lhb_summary.get('total_sell_yi'))}，净额 {_fmt_yi(lhb_summary.get('total_net_yi'))}，上榜成交 {_fmt_yi(lhb_summary.get('total_deal_yi'))}。",
        f"- 上榜原因集中: {_format_lhb_reasons(lhb_summary.get('reason_counts', []))}。",
        "- 热点追逐判断: 若强势板块主力净流入为正且龙虎榜净买入集中在同方向，短线资金仍在追逐热点；否则以轮动为主。",
        "",
        "龙虎榜净买入前五：",
    ])
    _append_table(
        lines,
        ["股票", "涨跌幅", "净买入", "买入", "卖出", "上榜原因/解读"],
        [
            [
                row.get("name", "-"),
                _fmt_pct(row.get("change_pct")),
                _fmt_yi(row.get("net_yi")),
                _fmt_yi(row.get("buy_yi")),
                _fmt_yi(row.get("sell_yi")),
                row.get("explain") or row.get("reason", "-"),
            ]
            for row in lhb_summary.get("top_buy", [])
        ],
    )
    lines.extend(["", "龙虎榜净卖出前五："])
    _append_table(
        lines,
        ["股票", "涨跌幅", "净买入", "买入", "卖出", "上榜原因/解读"],
        [
            [
                row.get("name", "-"),
                _fmt_pct(row.get("change_pct")),
                _fmt_yi(row.get("net_yi")),
                _fmt_yi(row.get("buy_yi")),
                _fmt_yi(row.get("sell_yi")),
                row.get("explain") or row.get("reason", "-"),
            ]
            for row in lhb_summary.get("top_sell", [])
        ],
    )

    lines.extend([
        "",
        "## 四、个股复盘",
        "",
        "- 今日自选股表现使用最近一次推荐池近似；真实持仓盈亏需接入券商/手工持仓数据。",
    ])
    if watchlist:
        _append_table(
            lines,
            ["股票", "涨跌幅", "推荐指数", "建议", "买点/风控"],
            [
                [
                    row["name"],
                    _fmt_pct(row.get("change_pct")),
                    f"{row.get('score', 0):.1f}" if row.get("score") is not None else "-",
                    row.get("action", "-"),
                    row.get("exit_trigger") or row.get("entry_ref") or "-",
                ]
                for row in watchlist[:12]
            ],
        )
    else:
        lines.append("- 暂无自选/推荐池行情数据。")

    lines.extend([
        "",
        "## 五、主线与策略分析",
        "",
        f"- 今日市场主线: **{_format_board_names(signal_boards or strongest, 3)}**。",
        f"- 主线状态: {_mainline_state(signal_boards, emotion)}。",
        f"- 明日资金可能偏向: {_format_board_names(signal_boards[:3], 3)}。",
        f"- 策略改进: 大盘评分低于 58 或主线情绪低于 5 时，减少追高，等待回踩或二次确认。",
        "",
        "## 六、持仓与仓位管理",
        "",
        f"- 建议仓位上限: **{market.get('position_ceiling', '未知')}**。",
        "- 今日总仓位/现金比例: 真实账户数据待接入。",
        "- 止损止盈: 对推荐池个股按各自买点/风控执行；跌破关键均线且板块弱化时优先减仓。",
        "",
        "## 七、信息与新闻",
        "",
        "- 重大公告/政策/研报: 数据待接入公告与新闻源。",
        "- 过度反应识别: 涨幅靠前但主力净流入弱、板块上涨家数占比低的方向，按短线情绪过热处理。",
        "- 新机会: 重点观察新进入强势榜且主力净流入为正的板块。",
        "",
        "## 八、明日交易计划",
        "",
        f"- 重点指数: 观察主要指数能否维持 {market.get('level', '未知')} 对应的成交额与上涨家数扩散。",
        f"- 重点板块: {_format_board_names(signal_boards[:5], 5)}。",
        "- 买入条件: 板块继续放量、龙头不破位、二线跟风扩散；避免单日大涨后无承接追高。",
        "- 风险点: 成交额缩量、涨停数量下降、跌停增多、主线龙头高位放量滞涨。",
        "- 策略目标: 趋势跟随为主，强主线龙头确认后再找补涨；弱市只观察不追高。",
        "",
        "## 九、心理与复盘总结",
        "",
        "- 今日是否被涨跌影响: 需要在交易日志中记录主观情绪评分。",
        "- 纪律检查: 是否只在计划内买卖、是否按止损/止盈执行、是否追高。",
        "- 今日经验: 优先相信数据共振，不因单只股波动改变整体策略。",
        "- 明日复盘重点: 成交额、上涨家数、涨停数量、主线板块持续性、龙头与跟风梯队。",
        "",
        "---",
        "",
        "*免责声明：本复盘由规则模型自动生成，仅用于交易复盘和计划管理，不构成投资建议。*",
    ])
    return "\n".join(lines)


def style_bias_from_boards(boards: list[dict]) -> str:
    if not boards:
        return "未知"
    top = sorted(boards, key=lambda b: (b.get("change_pct", 0), b.get("main_net_yi", 0)), reverse=True)[:20]
    names = " ".join(b.get("name", "") for b in top)
    if any(k in names for k in ("银行", "证券", "保险", "白酒", "中字头", "煤炭", "石油")):
        return "大盘蓝筹"
    if any(k in names for k in ("AI", "人工智能", "算力", "半导体", "芯片", "机器人", "创新药")):
        return "成长股"
    if any(k in names for k in ("小盘", "专精特新", "次新", "微盘")):
        return "中小盘"
    return "主题成长/轮动"


def _format_board_names(boards: list[dict], limit: int) -> str:
    names = [b.get("name", "") for b in boards[:limit] if b.get("name")]
    return "、".join(names) if names else "暂无明确方向"


def _format_lhb_reasons(reason_counts: list[tuple[str, int]]) -> str:
    if not reason_counts:
        return "暂无龙虎榜原因分布"
    return "、".join(f"{reason}({count})" for reason, count in reason_counts[:5])


def _mainline_state(signal_boards: list[dict], emotion: float) -> str:
    if emotion >= 7 and len(signal_boards) >= 3:
        return "强化"
    if emotion >= 5 and signal_boards:
        return "维持"
    return "弱化或轮动"


def generate_market_review(top_n: int = 10, board_type: str = "all") -> tuple[str, dict]:
    try:
        indices = fetch_market_indices()
    except Exception as exc:
        print(f"[复盘] 主要指数获取失败，继续生成降级报告: {exc}", flush=True)
        indices = []
    market = evaluate_market_environment(indices)
    all_stocks = fetch_a_share_snapshot()
    breadth = summarize_breadth(all_stocks, market=market)
    previous = _load_previous_state()
    try:
        all_boards = fetch_boards(board_type=board_type, limit=500)
    except Exception as exc:
        print(f"[复盘] 板块快照获取失败，继续生成降级报告: {exc}", flush=True)
        all_boards = []
    board_stats = summarize_board_stats(all_boards)
    try:
        lhb = fetch_lhb_details()
    except Exception as exc:
        print(f"[复盘] 龙虎榜获取失败，继续生成降级报告: {exc}", flush=True)
        lhb = {"date": "", "rows": [], "data_status": f"龙虎榜数据暂不可用：{exc}"}
    lhb_summary = summarize_lhb(lhb)
    try:
        _, _, signal_boards = capture_market_signals(
            mode="review",
            top_n=top_n,
            board_type=board_type,
            with_ai=False,
        )
    except Exception as exc:
        print(f"[复盘] 板块信号获取失败，继续生成降级报告: {exc}", flush=True)
        signal_boards = []
    watchlist = load_watchlist_performance()
    report = build_review_report(
        indices=indices,
        market=market,
        breadth=breadth,
        all_boards=all_boards,
        board_stats=board_stats,
        lhb_summary=lhb_summary,
        signal_boards=signal_boards,
        watchlist=watchlist,
        previous=previous,
    )
    snapshot = {
        "generated_at": _now_shanghai().isoformat(),
        "total_amount_yi": breadth.get("total_amount_yi", 0),
        "market": market,
        "breadth": breadth,
        "board_stats": {
            key: value
            for key, value in board_stats.items()
            if key not in ("top_amount", "top_inflow", "top_outflow")
        },
        "lhb": {
            "date": lhb_summary.get("date", ""),
            "count": lhb_summary.get("count", 0),
            "row_count": lhb_summary.get("row_count", 0),
            "total_net_yi": lhb_summary.get("total_net_yi", 0),
            "data_status": lhb_summary.get("data_status", ""),
        },
        "top_boards": [
            {
                "name": b.get("name", ""),
                "change_pct": b.get("change_pct"),
                "main_net_yi": b.get("main_net_yi"),
                "signal_score": b.get("signal_score"),
            }
            for b in signal_boards[:10]
        ],
    }
    _save_review_state(snapshot)
    return report, snapshot
