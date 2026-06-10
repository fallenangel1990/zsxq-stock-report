#!/usr/bin/env python3
"""A股主流板块盘中异动捕获与盘后复盘模块。"""

import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import yaml


EASTMONEY_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"

BOARD_FIELDS = (
    "f12,f14,f3,f4,f5,f6,f20,f62,f66,f69,f72,f75,f78,f81,f84,f87,"
    "f104,f105,f128,f136,f140,f141,f152"
)
STOCK_FIELDS = (
    "f12,f14,f2,f3,f4,f5,f6,f8,f10,f20,f21,f62,f66,f69,f72,f75,f152"
)
INDEX_FIELDS = "f12,f13,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18,f104,f105,f106"
MAIN_INDEX_SECIDS = "1.000001,0.399001,0.399006,1.000300,1.000688"

DEFAULT_MAINSTREAM_KEYWORDS = [
    "人工智能", "AI", "算力", "大模型", "机器人", "半导体", "芯片", "消费电子",
    "新能源", "储能", "锂电", "光伏", "风电", "电池", "汽车", "新能源车",
    "军工", "低空经济", "商业航天", "卫星", "医药", "创新药", "CRO", "医疗器械",
    "白酒", "食品饮料", "消费", "传媒", "游戏", "证券", "银行", "保险",
    "地产", "有色", "稀土", "黄金", "煤炭", "钢铁", "化工", "电力", "电网",
    "水泥", "玻璃", "农业", "猪肉", "旅游", "跨境电商",
]


def _now_shanghai() -> datetime:
    """返回北京时间当前时间，用于报告中展示的生成时间。"""
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def _request_json(params: dict, timeout: int = 12, retries: int = 3, backoff: float = 2.0) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/",
    }
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(EASTMONEY_CLIST_URL, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise last_exc


def _request_ulist_json(params: dict, timeout: int = 12, retries: int = 3, backoff: float = 2.0) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/",
    }
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(EASTMONEY_ULIST_URL, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise last_exc


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, "-", ""):
            return default
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        if value in (None, "-", ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _money_yi(value) -> float:
    return round(_safe_float(value) / 100000000, 2)


def _pct(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 2)


def fetch_boards(board_type: str = "all", limit: int = 500) -> list[dict]:
    """抓取东方财富行业/概念板块实时行情。"""
    fs_map = {
        "industry": ["m:90+t:2"],
        "concept": ["m:90+t:3"],
        "all": ["m:90+t:2", "m:90+t:3"],
    }
    fs_values = fs_map.get(board_type, fs_map["all"])

    boards = []
    seen = set()
    for fs in fs_values:
        params = {
            "pn": 1,
            "pz": limit,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": fs,
            "fields": BOARD_FIELDS,
        }
        data = _request_json(params)
        for raw in data.get("data", {}).get("diff", []) or []:
            code = raw.get("f12", "")
            if not code or code in seen:
                continue
            seen.add(code)
            boards.append(_normalize_board(raw, "industry" if fs.endswith("t:2") else "concept"))
    return boards


def _normalize_board(raw: dict, board_type: str) -> dict:
    up_count = _safe_int(raw.get("f104"))
    down_count = _safe_int(raw.get("f105"))
    total_count = up_count + down_count
    amount_yi = _money_yi(raw.get("f6"))
    main_net_yi = _money_yi(raw.get("f62"))
    return {
        "code": raw.get("f12", ""),
        "name": raw.get("f14", ""),
        "type": board_type,
        "change_pct": _safe_float(raw.get("f3")),
        "amount_yi": amount_yi,
        "market_cap_yi": _money_yi(raw.get("f20")),
        "main_net_yi": main_net_yi,
        "super_net_yi": _money_yi(raw.get("f66")),
        "super_net_ratio": _safe_float(raw.get("f69")),
        "large_net_yi": _money_yi(raw.get("f72")),
        "large_net_ratio": _safe_float(raw.get("f75")),
        "mid_net_yi": _money_yi(raw.get("f78")),
        "small_net_yi": _money_yi(raw.get("f84")),
        "up_count": up_count,
        "down_count": down_count,
        "up_ratio": _pct(up_count, total_count),
        "leader_name": raw.get("f128", ""),
        "leader_code": raw.get("f140", ""),
        "leader_change_pct": _safe_float(raw.get("f136")),
        "main_net_ratio": _pct(main_net_yi, amount_yi),
    }


def fetch_board_stocks(board_code: str, limit: int = 30) -> list[dict]:
    """抓取指定板块内涨幅靠前成分股。"""
    params = {
        "pn": 1,
        "pz": limit,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": f"b:{board_code}+f:!50",
        "fields": STOCK_FIELDS,
    }
    data = _request_json(params)
    stocks = []
    for raw in data.get("data", {}).get("diff", []) or []:
        amount_yi = _money_yi(raw.get("f6"))
        main_net_yi = _money_yi(raw.get("f62"))
        stocks.append({
            "code": raw.get("f12", ""),
            "name": raw.get("f14", ""),
            "price": _safe_float(raw.get("f2")),
            "change_pct": _safe_float(raw.get("f3")),
            "amount_yi": amount_yi,
            "turnover_rate": _safe_float(raw.get("f8")),
            "volume_ratio": _safe_float(raw.get("f10")),
            "market_cap_yi": _money_yi(raw.get("f20")),
            "free_market_cap_yi": _money_yi(raw.get("f21")),
            "main_net_yi": main_net_yi,
            "super_net_yi": _money_yi(raw.get("f66")),
            "super_net_ratio": _safe_float(raw.get("f69")),
            "large_net_yi": _money_yi(raw.get("f72")),
            "main_net_ratio": _pct(main_net_yi, amount_yi),
        })
    return stocks


def fetch_market_indices() -> list[dict]:
    """抓取主要指数行情和上涨/下跌家数。"""
    params = {
        "fltt": 2,
        "invt": 2,
        "fields": INDEX_FIELDS,
        "secids": MAIN_INDEX_SECIDS,
    }
    data = _request_ulist_json(params)
    indices = []
    for raw in data.get("data", {}).get("diff", []) or []:
        up_count = _safe_int(raw.get("f104"))
        down_count = _safe_int(raw.get("f105"))
        flat_count = _safe_int(raw.get("f106"))
        total_count = up_count + down_count + flat_count
        indices.append({
            "code": raw.get("f12", ""),
            "name": raw.get("f14", ""),
            "price": _safe_float(raw.get("f2")),
            "change_pct": _safe_float(raw.get("f3")),
            "amount_yi": _money_yi(raw.get("f6")),
            "open": _safe_float(raw.get("f17")),
            "prev_close": _safe_float(raw.get("f18")),
            "high": _safe_float(raw.get("f15")),
            "low": _safe_float(raw.get("f16")),
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "up_ratio": _pct(up_count, total_count),
        })
    return indices


def evaluate_market_environment(indices: list[dict]) -> dict:
    """根据主要指数和市场宽度评估大盘环境。"""
    if not indices:
        return {
            "level": "未知",
            "score": 0,
            "position_ceiling": "0%",
            "message": "无法获取大盘指数数据",
            "avg_change_pct": 0,
            "up_ratio": 0,
            "total_amount_yi": 0,
        }

    total_up = sum(i["up_count"] for i in indices)
    total_down = sum(i["down_count"] for i in indices)
    total_flat = sum(i["flat_count"] for i in indices)
    total_amount = round(sum(i["amount_yi"] for i in indices), 2)
    up_ratio = _pct(total_up, total_up + total_down + total_flat)
    avg_change = round(sum(i["change_pct"] for i in indices) / len(indices), 2)

    score = 50
    score += avg_change * 12
    score += (up_ratio - 50) * 0.6
    if total_amount >= 18000:
        score += 8
    elif total_amount >= 12000:
        score += 4
    elif total_amount < 8000:
        score -= 6
    score = round(max(0, min(100, score)), 1)

    if score >= 70 and avg_change >= 0.5 and up_ratio >= 58:
        level = "强势进攻"
        ceiling = "70%-80%"
        message = "大盘环境支持加仓，优先跟随主线强板块。"
    elif score >= 58 and avg_change >= -0.2 and up_ratio >= 48:
        level = "修复可试仓"
        ceiling = "50%-60%"
        message = "大盘处于修复或震荡偏强，可小步建仓，确认后再加仓。"
    elif score >= 42 and up_ratio >= 35:
        level = "震荡观察"
        ceiling = "25%-40%"
        message = "大盘没有明显进攻优势，板块信号以观察和轻仓试错为主。"
    else:
        level = "防守降仓"
        ceiling = "0%-20%"
        message = "大盘环境偏弱，避免追高，只保留高确定性低仓位观察。"

    return {
        "level": level,
        "score": score,
        "position_ceiling": ceiling,
        "message": message,
        "avg_change_pct": avg_change,
        "up_ratio": up_ratio,
        "total_amount_yi": total_amount,
        "total_up": total_up,
        "total_down": total_down,
        "total_flat": total_flat,
    }


def _is_mainstream_board(name: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    return any(k and k in name for k in keywords)


def _signal_score(board: dict, top_stocks: list[dict]) -> float:
    positive_top = sum(1 for s in top_stocks[:8] if s.get("main_net_yi", 0) > 0)
    limit_like = sum(1 for s in top_stocks[:12] if s.get("change_pct", 0) >= 9.5)
    score = 0.0
    score += board["change_pct"] * 1.35
    score += max(board["main_net_ratio"], 0) * 1.15
    score += min(max(board["main_net_yi"], 0), 30) * 0.28
    score += max(board["up_ratio"] - 50, 0) * 0.035
    score += max(board["leader_change_pct"], 0) * 0.18
    score += positive_top * 0.45
    score += limit_like * 0.7
    return round(score, 2)


def _signal_level(score: float, board: dict) -> str:
    if score >= 16 and board["main_net_yi"] >= 3 and board["up_ratio"] >= 60:
        return "强异动/疑似建仓"
    if score >= 10 and board["main_net_yi"] > 0:
        return "异动增强"
    if board["change_pct"] >= 1.2 or board["main_net_yi"] >= 1:
        return "观察"
    return "弱"


def _board_action(board: dict, market: dict) -> str:
    market_level = market.get("level", "")
    positive_top = sum(1 for s in board.get("top_stocks", [])[:8] if s.get("main_net_yi", 0) > 0)
    strong_leaders = sum(1 for s in board.get("leading_stocks", []) if s.get("change_pct", 0) >= 5)

    if market_level == "防守降仓":
        if board["signal_score"] >= 22 and board["main_net_yi"] >= 8 and board["up_ratio"] >= 70:
            return "观察试仓"
        return "规避/不加仓"

    if (
        market_level in ("强势进攻", "修复可试仓")
        and board["signal_score"] >= 20
        and board["main_net_yi"] >= 5
        and board["main_net_ratio"] >= 5
        and positive_top >= 4
        and strong_leaders >= 2
    ):
        return "加仓信号"

    if (
        market_level in ("强势进攻", "修复可试仓", "震荡观察")
        and board["signal_score"] >= 14
        and board["main_net_yi"] >= 2
        and board["up_ratio"] >= 58
        and positive_top >= 3
    ):
        return "建仓信号"

    if board["signal_score"] >= 10 and board["main_net_yi"] > 0:
        return "观察信号"
    return "规避/不加仓"


def _action_reason(board: dict, market: dict) -> str:
    return (
        f"大盘环境「{market.get('level', '未知')}」，板块主力净流入 {board['main_net_yi']:.2f} 亿，"
        f"净占比 {board['main_net_ratio']:.2f}%，上涨家数占比 {board['up_ratio']:.1f}%，"
        f"领涨股 {board['leader_name']} 涨幅 {board['leader_change_pct']:.2f}%。"
    )


def _logic_hint(board_name: str, top_stocks: list[dict]) -> str:
    text = board_name + " " + " ".join(s.get("name", "") for s in top_stocks[:5])
    rules = [
        (["储能", "电池", "锂电", "新能源"], "新能源链景气、政策/订单/价格预期改善，资金偏好业绩弹性和涨价传导。"),
        (["光伏", "风电", "电力", "电网"], "电力设备与新能源基建预期升温，关注装机、招标、容量电价和电网投资催化。"),
        (["人工智能", "AI", "算力", "大模型", "数据"], "AI 应用/算力产业趋势驱动，资金围绕高辨识度龙头和容量弹性标的扩散。"),
        (["机器人", "自动化"], "机器人产业化和设备更新预期驱动，资金偏好核心零部件、整机与自动化设备。"),
        (["半导体", "芯片"], "国产替代、周期复苏和先进制程链条预期改善，关注设备材料和高弹性设计公司。"),
        (["军工", "航天", "卫星", "低空"], "政策催化与订单预期强化，板块容易出现事件驱动型资金回流。"),
        (["证券", "保险", "银行"], "风险偏好修复或政策预期推动金融权重护盘，需观察成交额能否持续放大。"),
        (["白酒", "食品", "消费", "旅游"], "消费复苏和估值修复逻辑，重点看终端需求、价格体系和节假日催化。"),
        (["创新药", "医药", "医疗", "CRO"], "医药政策边际改善、出海/临床进展和估值修复共同驱动。"),
        (["有色", "黄金", "稀土", "煤炭", "钢铁", "化工"], "资源品价格或供需预期变化带动，重点跟踪期货价格与库存变化。"),
    ]
    for keywords, hint in rules:
        if any(k in text for k in keywords):
            return hint
    return "板块上涨主要由资金净流入、成分股扩散和龙头带动共同驱动，需结合后续消息面确认。"


def capture_sector_signals(
    mode: str = "review",
    top_n: int = 12,
    board_type: str = "all",
    with_ai: bool = True,
) -> tuple[str, list[dict]]:
    """生成盘中异动或盘后复盘报告。"""
    config = _load_config()
    monitor_config = config.get("sector_monitor", {})
    keywords = monitor_config.get("mainstream_keywords", DEFAULT_MAINSTREAM_KEYWORDS)
    min_change = float(monitor_config.get("min_change_pct", 0.8))
    min_main_net_yi = float(monitor_config.get("min_main_net_yi", 0.5))

    boards = fetch_boards(board_type=board_type, limit=int(monitor_config.get("board_limit", 500)))
    mainstream = [b for b in boards if _is_mainstream_board(b["name"], keywords)]

    candidates = []
    for board in mainstream:
        if board["change_pct"] < min_change and board["main_net_yi"] < min_main_net_yi:
            continue
        stocks = fetch_board_stocks(board["code"], limit=int(monitor_config.get("stock_limit", 30)))
        score = _signal_score(board, stocks)
        board["signal_score"] = score
        board["signal_level"] = _signal_level(score, board)
        board["top_stocks"] = stocks[:10]
        board["leading_stocks"] = _select_leading_stocks(stocks)
        board["logic_hint"] = _logic_hint(board["name"], stocks)
        if board["signal_level"] != "弱":
            candidates.append(board)

    candidates.sort(
        key=lambda b: (b["signal_level"] == "强异动/疑似建仓", b["signal_score"], b["main_net_yi"]),
        reverse=True,
    )
    selected = _dedupe_boards(candidates)[:top_n]

    report = _build_sector_report(selected, mode=mode, with_ai=with_ai)
    return report, selected


def capture_market_signals(
    mode: str = "intraday",
    top_n: int = 12,
    board_type: str = "all",
    with_ai: bool = True,
) -> tuple[str, dict, list[dict]]:
    """监控大盘和主流板块，输出建仓/加仓信号。"""
    indices = fetch_market_indices()
    market = evaluate_market_environment(indices)

    _, boards = capture_sector_signals(
        mode=mode,
        top_n=max(top_n * 2, top_n),
        board_type=board_type,
        with_ai=False,
    )

    for board in boards:
        board["action"] = _board_action(board, market)
        board["action_reason"] = _action_reason(board, market)

    action_rank = {
        "加仓信号": 4,
        "建仓信号": 3,
        "观察试仓": 2,
        "观察信号": 1,
        "规避/不加仓": 0,
    }
    boards.sort(
        key=lambda b: (action_rank.get(b.get("action", ""), 0), b.get("signal_score", 0), b.get("main_net_yi", 0)),
        reverse=True,
    )
    selected = boards[:top_n]
    report = _build_market_signal_report(indices, market, selected, mode=mode, with_ai=with_ai)
    return report, market, selected


def _select_leading_stocks(stocks: list[dict]) -> list[dict]:
    ranked = sorted(
        stocks,
        key=lambda s: (
            s.get("change_pct", 0),
            s.get("main_net_yi", 0),
            s.get("amount_yi", 0),
        ),
        reverse=True,
    )
    return ranked[:5]


def _dedupe_boards(boards: list[dict]) -> list[dict]:
    """去掉行业分级导致的重复板块，如证券II/证券III。"""
    selected = []
    seen_keys = set()
    for board in boards:
        leader_codes = ",".join(s.get("code", "") for s in board.get("leading_stocks", [])[:3])
        name_key = (
            board["name"]
            .replace("Ⅰ", "")
            .replace("Ⅱ", "")
            .replace("Ⅲ", "")
            .replace("I", "")
        )
        metric_key = (
            round(board.get("change_pct", 0), 2),
            round(board.get("amount_yi", 0), 1),
            round(board.get("main_net_yi", 0), 1),
            leader_codes,
        )
        key = (name_key, metric_key)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        selected.append(board)
    return selected


def _build_sector_report(boards: list[dict], mode: str, with_ai: bool) -> str:
    now = _now_shanghai().strftime("%Y-%m-%d %H:%M:%S 北京时间")
    title = "A股盘后板块主力建仓复盘" if mode == "review" else "A股盘中板块异动信号"
    lines = [
        f"# {title}",
        "",
        f"> 生成时间: {now}",
        f"> 数据源: 东方财富实时板块与成分股行情；主力建仓为规则模型识别，不构成投资建议。",
        "",
    ]
    if not boards:
        lines.append("未捕获到满足阈值的主流板块异动信号。")
        return "\n".join(lines)

    strong_count = sum(1 for b in boards if b.get("signal_level") == "强异动/疑似建仓")
    lines.extend([
        "## 一、信号概览",
        "",
        f"- 捕获板块: {len(boards)} 个",
        f"- 强异动/疑似建仓: {strong_count} 个",
        f"- 资金最强板块: {max(boards, key=lambda b: b['main_net_yi'])['name']}",
        f"- 涨幅最强板块: {max(boards, key=lambda b: b['change_pct'])['name']}",
        "",
        "| 等级 | 板块 | 涨幅 | 主力净流入 | 主力净占比 | 上涨家数占比 | 领涨股 | 信号分 |",
        "|---|---|---:|---:|---:|---:|---|---:|",
    ])
    for b in boards:
        lines.append(
            f"| {b['signal_level']} | {b['name']} | {b['change_pct']:.2f}% | "
            f"{b['main_net_yi']:.2f}亿 | {b['main_net_ratio']:.2f}% | "
            f"{b['up_ratio']:.1f}% | {b['leader_name']}({b['leader_change_pct']:.2f}%) | "
            f"{b['signal_score']:.2f} |"
        )

    lines.extend(["", "## 二、疑似主力建仓板块", ""])
    for idx, b in enumerate(boards, 1):
        lines.append(f"### {idx}. {b['name']}：{b['signal_level']}")
        lines.append("")
        lines.append(
            f"- 板块表现: 涨幅 {b['change_pct']:.2f}%，成交额 {b['amount_yi']:.2f} 亿，"
            f"主力净流入 {b['main_net_yi']:.2f} 亿，主力净占比 {b['main_net_ratio']:.2f}%。"
        )
        lines.append(
            f"- 扩散程度: 上涨 {b['up_count']} 家、下跌 {b['down_count']} 家，上涨家数占比 {b['up_ratio']:.1f}%。"
        )
        lines.append(f"- 初步上涨逻辑: {b['logic_hint']}")
        lines.append("- 领涨个股:")
        for s in b["leading_stocks"]:
            lines.append(
                f"  - {s['name']}({s['code']}): 涨幅 {s['change_pct']:.2f}%，"
                f"主力净流入 {s['main_net_yi']:.2f} 亿，换手 {s['turnover_rate']:.2f}%，"
                f"量比 {s['volume_ratio']:.2f}"
            )
        lines.append("")

    if with_ai:
        ai_text = _ai_sector_review(boards, mode)
        if ai_text:
            lines.extend(["## 三、AI 复盘解读", "", ai_text, ""])

    lines.extend([
        "---",
        "",
        "*说明：规则模型重点观察涨幅、主力净流入、主力净占比、上涨家数占比和领涨股资金配合。"
        "“疑似建仓”仅表示资金行为特征相似，不等于确定性结论。*",
    ])
    return "\n".join(lines)


def _build_market_signal_report(
    indices: list[dict],
    market: dict,
    boards: list[dict],
    mode: str,
    with_ai: bool,
) -> str:
    now = _now_shanghai().strftime("%Y-%m-%d %H:%M:%S 北京时间")
    title = "A股大盘与主流板块建仓/加仓信号"
    lines = [
        f"# {title}",
        "",
        f"> 生成时间: {now}",
        f"> 模式: {'盘中监控' if mode == 'intraday' else '盘后复盘'}",
        f"> 数据源: 东方财富实时指数、板块与成分股行情；信号为规则模型识别，不构成投资建议。",
        "",
        "## 一、大盘环境",
        "",
        f"- 环境等级: **{market['level']}**",
        f"- 大盘评分: **{market['score']} / 100**",
        f"- 建议仓位上限: **{market['position_ceiling']}**",
        f"- 主要指数平均涨跌幅: {market['avg_change_pct']:.2f}%",
        f"- 指数成分上涨家数占比: {market['up_ratio']:.1f}% "
        f"({market.get('total_up', 0)}涨 / {market.get('total_down', 0)}跌 / {market.get('total_flat', 0)}平)",
        f"- 主要指数合计成交额: {market['total_amount_yi']:.2f} 亿",
        f"- 判断: {market['message']}",
        "",
        "| 指数 | 点位 | 涨跌幅 | 成交额 | 上涨家数占比 |",
        "|---|---:|---:|---:|---:|",
    ]
    for idx in indices:
        lines.append(
            f"| {idx['name']} | {idx['price']:.2f} | {idx['change_pct']:.2f}% | "
            f"{idx['amount_yi']:.2f}亿 | {idx['up_ratio']:.1f}% |"
        )

    lines.extend(["", "## 二、建仓/加仓信号", ""])
    if not boards:
        lines.append("未捕获到满足阈值的主流板块信号。")
    else:
        action_counts = {}
        for b in boards:
            action_counts[b["action"]] = action_counts.get(b["action"], 0) + 1
        counts_text = "，".join(f"{k}: {v}" for k, v in action_counts.items())
        lines.append(f"- 信号分布: {counts_text}")
        lines.append("")
        lines.append("| 动作 | 板块 | 等级 | 涨幅 | 主力净流入 | 主力净占比 | 上涨家数占比 | 领涨股 | 信号分 |")
        lines.append("|---|---|---|---:|---:|---:|---:|---|---:|")
        for b in boards:
            lines.append(
                f"| **{b['action']}** | {b['name']} | {b['signal_level']} | "
                f"{b['change_pct']:.2f}% | {b['main_net_yi']:.2f}亿 | "
                f"{b['main_net_ratio']:.2f}% | {b['up_ratio']:.1f}% | "
                f"{b['leader_name']}({b['leader_change_pct']:.2f}%) | {b['signal_score']:.2f} |"
            )

        lines.extend(["", "## 三、执行清单", ""])
        for idx, b in enumerate(boards, 1):
            lines.append(f"### {idx}. {b['name']}：{b['action']}")
            lines.append("")
            lines.append(f"- 触发原因: {b['action_reason']}")
            lines.append(f"- 上涨逻辑: {b['logic_hint']}")
            lines.append("- 领涨/观察股:")
            for s in b["leading_stocks"]:
                lines.append(
                    f"  - {s['name']}({s['code']}): 涨幅 {s['change_pct']:.2f}%，"
                    f"主力净流入 {s['main_net_yi']:.2f} 亿，换手 {s['turnover_rate']:.2f}%，"
                    f"量比 {s['volume_ratio']:.2f}"
                )
            lines.append("")

    lines.extend([
        "## 四、信号规则",
        "",
        "- **加仓信号**: 大盘强势/修复，板块信号分高，主力净流入和净占比强，领涨股与核心成分股资金配合。",
        "- **建仓信号**: 大盘不处于防守，板块主力净流入、上涨家数扩散、领涨股表现共同达标。",
        "- **观察信号/观察试仓**: 数据有亮点但大盘或扩散度不足，只适合轻仓跟踪。",
        "- **规避/不加仓**: 大盘防守或板块资金/扩散不足。",
        "",
    ])

    if with_ai and boards:
        ai_text = _ai_market_signal_review(indices, market, boards, mode)
        if ai_text:
            lines.extend(["## 五、AI 信号解读", "", ai_text, ""])

    lines.extend([
        "---",
        "",
        "*说明：本系统用于把盘面数据转成交易观察信号，不保证收益。实盘应结合个股位置、业绩、风险承受能力和止损纪律。*",
    ])
    return "\n".join(lines)


def _ai_market_signal_review(indices: list[dict], market: dict, boards: list[dict], mode: str) -> str:
    try:
        from summarizer import get_client
        client, model, provider = get_client()
        print(f"AI 后端: {provider} ({model})", flush=True)
        compact = {
            "mode": mode,
            "market": market,
            "indices": [
                {
                    "name": i["name"],
                    "change_pct": i["change_pct"],
                    "amount_yi": i["amount_yi"],
                    "up_ratio": i["up_ratio"],
                }
                for i in indices
            ],
            "boards": [
                {
                    "board": b["name"],
                    "action": b["action"],
                    "level": b["signal_level"],
                    "change_pct": b["change_pct"],
                    "main_net_yi": b["main_net_yi"],
                    "main_net_ratio": b["main_net_ratio"],
                    "up_ratio": b["up_ratio"],
                    "signal_score": b["signal_score"],
                    "leader": b["leader_name"],
                    "leader_change_pct": b["leader_change_pct"],
                    "logic_hint": b["logic_hint"],
                }
                for b in boards[:10]
            ],
        }
        system = (
            "你是A股交易计划助手。基于输入数据解释建仓/加仓信号。"
            "不要编造新闻；金额单位均为亿元；引用数字必须逐字照抄输入数据。"
            "输出要克制，强调条件和失效信号，不给确定性收益承诺。"
        )
        prompt = f"""请基于以下大盘与板块信号，输出简短交易观察计划。

数据：
{json.dumps(compact, ensure_ascii=False, indent=2)}

请输出：
1. 当前大盘是否允许建仓/加仓，以及仓位纪律。
2. 最优先的 3 条板块主线，区分“可加仓”“可建仓”“仅观察”。
3. 每条主线的确认信号和失效信号。
4. 如果明日/下一时段继续跟踪，应该重点看哪些数据。

硬性要求：
- 不要虚构新闻和政策。
- 金额单位均为亿元，数字必须照抄。
- 不要写“必涨”“确定上涨”等确定性措辞。
"""
        return client.create(system=system, prompt=prompt, max_tokens=2200)
    except Exception as e:
        print(f"[大盘信号] AI 解读失败: {e}", flush=True)
        return ""


def _ai_sector_review(boards: list[dict], mode: str) -> str:
    try:
        from summarizer import get_client
        client, model, provider = get_client()
        print(f"AI 后端: {provider} ({model})", flush=True)
        compact = []
        for b in boards[:12]:
            compact.append({
                "board": b["name"],
                "level": b["signal_level"],
                "change_pct": b["change_pct"],
                "main_net_yi": b["main_net_yi"],
                "main_net_ratio": b["main_net_ratio"],
                "up_ratio": b["up_ratio"],
                "leader": b["leader_name"],
                "leader_change_pct": b["leader_change_pct"],
                "leading_stocks": [
                    {
                        "name": s["name"],
                        "code": s["code"],
                        "change_pct": s["change_pct"],
                        "main_net_yi": s["main_net_yi"],
                    }
                    for s in b["leading_stocks"]
                ],
                "logic_hint": b["logic_hint"],
            })

        system = (
            "你是A股盘面复盘分析师。基于结构化行情数据，判断板块资金行为和上涨逻辑。"
            "不要编造未给出的新闻；需要区分数据事实、合理推断和待验证催化。"
            "所有金额字段单位都是亿元；如果引用具体数值，必须逐字照抄输入数据，禁止四舍五入、改写或漏位。"
            "不确定时只描述强弱，不复述具体数值。"
        )
        prompt = f"""请基于以下板块异动数据，输出盘面复盘。

模式：{"盘后复盘" if mode == "review" else "盘中异动"}

数据：
{json.dumps(compact, ensure_ascii=False, indent=2)}

请输出：
1. 今日/当前主线排序：列出 3-5 条主线，并说明强弱。
2. 哪些板块最像主力建仓：必须结合主力净流入、净占比、上涨家数占比、领涨股资金配合。
3. 领涨个股角色：区分龙头、容量中军、补涨/弹性股。
4. 上涨逻辑：只基于数据和常识性产业逻辑推断，明确“待验证催化”。
5. 明日/后续跟踪：列出确认信号和失效信号。

硬性要求：
- 金额单位均为“亿元”。
- 引用任何数字时必须与输入 JSON 完全一致；不能把 44.45 写成 4.45，不能自行换算。
- 不要虚构新闻、政策或会议，只能写“待验证催化”。
"""
        return client.create(system=system, prompt=prompt, max_tokens=3000)
    except Exception as e:
        print(f"[板块复盘] AI 解读失败: {e}", flush=True)
        return ""
