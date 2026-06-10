"""A 股盘后复盘任务。

按大盘、板块、资金、个股、策略、仓位、新闻和明日计划生成结构化复盘。
"""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

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
            "source": "主要指数成分兜底",
            "data_status": "全A快照不可用，已使用主要指数成分涨跌家数近似；涨停/跌停和平均换手率暂不可用。",
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
    amount_label = "全A约" if breadth_source == "东方财富全A快照" else "主要指数合计约"

    lines = [
        "# A股盘后复盘报告",
        "",
        f"> 生成时间: {now}",
        "> 数据源: 东方财富指数/板块/个股快照；龙虎榜、北向资金和真实持仓需接入独立数据源后补齐。",
        f"> 数据完整性: {breadth_status}",
        "",
        "## 一、大盘行情与市场情绪",
        "",
        f"- 指数环境: **{market.get('level', '未知')}**，大盘评分 {market.get('score', 0)} / 100。",
        f"- 今日成交额: {amount_label} **{_fmt_yi(breadth.get('total_amount_yi'))}**；{volume_desc}。",
        f"- 市场宽度: {breadth.get('up', 0)} 涨 / {breadth.get('down', 0)} 跌 / {breadth.get('flat', 0)} 平，赚钱效应 **{breadth.get('money_effect', '未知')}**。",
        f"- 市场风格: **{style}**；平均换手率约 {breadth.get('avg_turnover_rate', 0):.2f}%。",
        f"- 涨停/跌停: 涨停 {breadth.get('limit_up', 0)} 只，跌停 {breadth.get('limit_down', 0)} 只；连板数据待接入涨停池数据源。",
        f"- 北向资金: 数据待接入，当前复盘不编造净流入/流出数字。",
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

    lines.extend([
        "",
        "## 三、资金流向与龙虎榜",
        "",
        f"- 主力资金主要押注: {_format_board_names([b for b in strongest if b.get('main_net_yi', 0) > 0], 5)}。",
        "- 北向资金方向: 数据待接入。",
        "- 龙虎榜机构/游资净买卖: 数据待接入。",
        "- 热点追逐判断: 若强势板块主力净流入为正且领涨股保持高位，短线资金仍在追逐热点；否则以轮动为主。",
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


def _mainline_state(signal_boards: list[dict], emotion: float) -> str:
    if emotion >= 7 and len(signal_boards) >= 3:
        return "强化"
    if emotion >= 5 and signal_boards:
        return "维持"
    return "弱化或轮动"


def generate_market_review(top_n: int = 10, board_type: str = "all") -> tuple[str, dict]:
    indices = fetch_market_indices()
    market = evaluate_market_environment(indices)
    all_stocks = fetch_a_share_snapshot()
    breadth = summarize_breadth(all_stocks, market=market)
    previous = _load_previous_state()
    try:
        all_boards = fetch_boards(board_type=board_type, limit=500)
    except Exception as exc:
        print(f"[复盘] 板块快照获取失败，继续生成降级报告: {exc}", flush=True)
        all_boards = []
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
        signal_boards=signal_boards,
        watchlist=watchlist,
        previous=previous,
    )
    snapshot = {
        "generated_at": _now_shanghai().isoformat(),
        "total_amount_yi": breadth.get("total_amount_yi", 0),
        "market": market,
        "breadth": breadth,
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
