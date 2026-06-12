"""A 股盘后复盘任务。

按大盘、板块、资金、个股、策略、仓位、新闻和明日计划生成结构化复盘。
"""

import json
import math
import os
import time
import uuid
import html
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import markdown as md_lib

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
THS_INDEXFLASH_URL = "https://q.10jqka.com.cn/api.php?t=indexflash"
EASTMONEY_LIMIT_UP_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
EASTMONEY_LIMIT_DOWN_URL = "https://push2ex.eastmoney.com/getTopicDTPool"
EASTMONEY_NOTICE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
EASTMONEY_FAST_NEWS_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
PORTFOLIO_FILE = Path(__file__).parent / "data" / "holdings.json"
TRADING_JOURNAL_FILE = Path(__file__).parent / "data" / "trading_journal.json"


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


def _fmt_money(value) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _short_text(value: str, limit: int = 36) -> str:
    value = (value or "").strip().replace("|", "/")
    return value if len(value) <= limit else f"{value[:limit]}..."


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


def _cookie_header_from_json_text(text: str) -> str:
    if not text:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    if "=" in stripped and not stripped.startswith(("[", "{")):
        return stripped
    try:
        data = json.loads(stripped)
    except Exception:
        return ""
    if isinstance(data, list):
        pairs = []
        for item in data:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            value = item.get("value")
            if name and value is not None:
                pairs.append(f"{name}={value}")
        return "; ".join(pairs)
    if isinstance(data, dict):
        if "cookies" in data:
            return _cookie_header_from_json_text(json.dumps(data.get("cookies")))
        return "; ".join(f"{key}={value}" for key, value in data.items() if value is not None)
    return ""


def _load_ths_cookie_header() -> str:
    for env_name in ("THS_MARKET_COOKIE", "THS_COOKIES"):
        header = _cookie_header_from_json_text(os.environ.get(env_name, ""))
        if header:
            return header
    cookies_path = Path(__file__).parent / "cookies_ths.json"
    if cookies_path.exists():
        return _cookie_header_from_json_text(cookies_path.read_text(encoding="utf-8"))
    return ""


def _extract_cookie_value(cookie_header: str, name: str) -> str:
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        key, value = part.strip().split("=", 1)
        if key == name:
            return value
    return ""


def fetch_ths_market_breadth() -> dict:
    """从同花顺 indexflash 获取市场上涨/下跌家数。"""
    cookie_header = _load_ths_cookie_header()
    if not cookie_header:
        return {"valid": False, "data_status": "同花顺市场宽度 Cookie 未配置，无法获取同花顺涨跌家数。"}
    hexin_v = _extract_cookie_value(cookie_header, "v") or _extract_cookie_value(cookie_header, "hexin-v")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Host": "q.10jqka.com.cn",
        "Referer": "https://q.10jqka.com.cn/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cookie": cookie_header,
    }
    if hexin_v:
        headers["Hexin-V"] = hexin_v
    try:
        resp = requests.get(THS_INDEXFLASH_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "gbk"
        data = resp.json()
        zdfb = data.get("zdfb_data") or {}
        up = int(zdfb.get("znum") or 0)
        down = int(zdfb.get("dnum") or 0)
        distribution = zdfb.get("zdfb") or []
        if up <= 0 and down <= 0:
            return {"valid": False, "data_status": "同花顺市场宽度返回空数据。"}
        return {
            "valid": True,
            "up": up,
            "down": down,
            "flat": 0,
            "distribution": distribution,
            "source": "同花顺 indexflash",
            "data_status": "正常",
        }
    except Exception as exc:
        return {"valid": False, "data_status": f"同花顺市场宽度获取失败：{exc}"}


def _request_limit_pool(url: str, trade_date: str, sort: str) -> dict:
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": "0",
        "pagesize": "10000",
        "sort": sort,
        "date": trade_date,
        "_": str(int(time.time() * 1000)),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/ztb/ztb.html",
        "Accept": "application/json, text/plain, */*",
    }
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_limit_pool_stats(max_days: int = 10) -> dict:
    """获取真实涨停/跌停池数量。"""
    last_error = ""
    today = _now_shanghai().date()
    for offset in range(max_days):
        trade_date = (today - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            zt_data = _request_limit_pool(EASTMONEY_LIMIT_UP_URL, trade_date, "fbt:asc")
            dt_data = _request_limit_pool(EASTMONEY_LIMIT_DOWN_URL, trade_date, "fund:asc")
            zt = zt_data.get("data") or {}
            dt = dt_data.get("data") or {}
            limit_up = int(zt.get("tc") or 0)
            limit_down = int(dt.get("tc") or 0)
            if limit_up <= 0 and limit_down <= 0:
                continue
            return {
                "valid": True,
                "date": str(zt.get("qdate") or dt.get("qdate") or trade_date),
                "limit_up": limit_up,
                "limit_down": limit_down,
                "limit_up_pool": zt.get("pool") or [],
                "limit_down_pool": dt.get("pool") or [],
                "source": "东方财富涨跌停池",
                "data_status": "正常",
            }
        except Exception as exc:
            last_error = str(exc)
            print(f"[复盘] 涨跌停池 {trade_date} 获取失败: {exc}", flush=True)
            continue
    return {
        "valid": False,
        "date": "",
        "limit_up": 0,
        "limit_down": 0,
        "limit_up_pool": [],
        "limit_down_pool": [],
        "source": "东方财富涨跌停池",
        "data_status": f"涨跌停池数据暂不可用{f'：{last_error}' if last_error else ''}",
    }


def summarize_breadth(
    stocks: list[dict],
    market: Optional[dict] = None,
    ths_breadth: Optional[dict] = None,
    limit_stats: Optional[dict] = None,
) -> dict:
    market = market or {}
    ths_breadth = ths_breadth or {}
    limit_stats = limit_stats or {}
    statuses = []

    if ths_breadth.get("valid"):
        up = int(ths_breadth.get("up") or 0)
        down = int(ths_breadth.get("down") or 0)
        flat = int(ths_breadth.get("flat") or 0)
        breadth_source = ths_breadth.get("source", "同花顺 indexflash")
    elif stocks:
        up = sum(1 for s in stocks if s.get("change_pct", 0) > 0)
        down = sum(1 for s in stocks if s.get("change_pct", 0) < 0)
        flat = max(0, len(stocks) - up - down)
        breadth_source = "东方财富全A快照"
        statuses.append(ths_breadth.get("data_status", "同花顺市场宽度不可用，已使用东方财富全A快照涨跌数。"))
    else:
        up = int(market.get("total_up") or 0)
        down = int(market.get("total_down") or 0)
        flat = int(market.get("total_flat") or 0)
        breadth_source = market.get("breadth_source") or "上证指数+深证成指"
        statuses.append(ths_breadth.get("data_status", "同花顺市场宽度不可用。"))
        statuses.append(f"全A快照不可用，已使用{breadth_source}涨跌家数近似。")

    if limit_stats.get("valid"):
        limit_up = int(limit_stats.get("limit_up") or 0)
        limit_down = int(limit_stats.get("limit_down") or 0)
        limit_source = limit_stats.get("source", "东方财富涨跌停池")
    else:
        limit_up = 0
        limit_down = 0
        limit_source = limit_stats.get("source", "东方财富涨跌停池")
        statuses.append(limit_stats.get("data_status", "涨跌停池数据暂不可用。"))

    total_amount = (
        round(sum(s.get("amount_yi", 0) for s in stocks), 2)
        if stocks else market.get("total_amount_yi", 0)
    )
    amount_source = "东方财富全A快照" if stocks else (market.get("breadth_source") or "主要指数合计")
    avg_turnover = 0.0
    turnover_values = [s.get("turnover_rate", 0) for s in stocks if s.get("turnover_rate")]
    if turnover_values:
        avg_turnover = round(sum(turnover_values) / len(turnover_values), 2)
    money_effect = "强" if up > down * 1.5 else "弱" if down > up * 1.3 else "中性"
    if stocks and len(stocks) < 3000:
        statuses.append(f"全A快照仅获取 {len(stocks)} 只样本，仅用于成交额/换手率等辅助字段。")
    return {
        "total": up + down + flat,
        "up": up,
        "down": down,
        "flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "total_amount_yi": total_amount,
        "avg_turnover_rate": avg_turnover,
        "money_effect": money_effect,
        "source": breadth_source,
        "amount_source": amount_source,
        "limit_source": limit_source,
        "limit_date": limit_stats.get("date", ""),
        "data_status": "；".join(status for status in statuses if status) or "正常",
    }


def complete_market_environment(market: dict, breadth: dict, limit_summary: dict) -> dict:
    """指数主源不可用时，用市场宽度和涨跌停池兜底补全大盘环境。"""
    if market.get("level") and market.get("level") != "未知":
        return market

    up = breadth.get("up", 0) or 0
    down = breadth.get("down", 0) or 0
    limit_up = breadth.get("limit_up", 0) or 0
    limit_down = breadth.get("limit_down", 0) or 0
    consecutive = limit_summary.get("consecutive_count", 0) or 0

    score = 45.0
    if up or down:
        score += max(-18.0, min(18.0, math.log((up + 1) / (down + 1)) * 12))
    score += min(12.0, limit_up / 8)
    score -= min(14.0, limit_down / 3)
    score += min(8.0, consecutive / 3)
    score = round(max(0, min(100, score)), 1)

    if score >= 68 and limit_up >= max(40, limit_down * 2):
        level = "强势进攻"
        ceiling = "70%-80%"
        message = "指数源缺失，按涨跌停池和市场宽度推断情绪偏强。"
    elif score >= 55:
        level = "修复可试仓"
        ceiling = "50%-60%"
        message = "指数源缺失，按多源兜底推断有修复机会，等待主线确认。"
    elif score >= 38:
        level = "震荡观察"
        ceiling = "25%-40%"
        message = "指数源缺失，按多源兜底推断市场偏轮动，控制仓位。"
    else:
        level = "防守降仓"
        ceiling = "0%-20%"
        message = "指数源缺失，按涨跌停和宽度推断环境偏弱。"

    completed = dict(market)
    completed.update({
        "level": level,
        "score": score,
        "position_ceiling": ceiling,
        "message": message,
        "data_status": f"{market.get('data_status', '主要指数行情不可用')}；已用市场宽度/涨跌停池/连板数据补全大盘环境。",
    })
    return completed


def _limit_board_count(row: dict) -> int:
    zttj = row.get("zttj") or {}
    candidates = [
        zttj.get("ct"),
        zttj.get("days"),
        row.get("lbc"),
        row.get("days"),
    ]
    for value in candidates:
        try:
            count = int(value)
            if count > 0:
                return count
        except (TypeError, ValueError):
            continue
    return 1


def _infer_market_driver(name: str) -> str:
    text = name or ""
    rules = [
        (("半导体", "芯片", "集成电路"), "国产替代、AI 算力链和电子周期修复"),
        (("IT服务", "软件", "互联网", "AI", "人工智能", "算力", "数据"), "AI 应用、算力基础设施和数字化预期"),
        (("机器人", "专用设备", "自动化", "工业母机"), "机器人、高端制造和设备更新预期"),
        (("化学", "塑料", "化工", "农药", "化肥"), "涨价预期、供需改善和周期品修复"),
        (("房地产", "建筑", "建材", "水泥"), "地产政策预期和产业链修复交易"),
        (("有色", "稀土", "金属", "锂", "黄金"), "资源价格、供给约束和避险/通胀交易"),
        (("银行", "保险", "证券"), "权重护盘、估值修复和金融稳定预期"),
        (("医药", "创新药", "医疗"), "政策边际改善、创新药和业绩修复预期"),
        (("消费", "食品", "白酒", "旅游", "零售"), "内需修复和消费政策预期"),
        (("电力", "公用事业", "环保"), "防守属性、分红预期和能源改革主题"),
    ]
    for keywords, driver in rules:
        if any(keyword in text for keyword in keywords):
            return driver
    return "涨停资金集中，短线情绪和题材扩散驱动"


def summarize_limit_pool(limit_stats: dict) -> dict:
    """从真实涨停池明细统计连板和涨停集中方向。"""
    pool = limit_stats.get("limit_up_pool") or []
    if not pool:
        return {
            "consecutive_count": 0,
            "max_consecutive": 0,
            "top_consecutive": [],
            "hot_industries": [],
            "hot_topics": [],
            "data_status": limit_stats.get("data_status", "涨停池明细不可用"),
        }

    rows = []
    industries = Counter()
    industry_leaders = {}
    industry_consecutive = Counter()
    for raw in pool:
        board_count = _limit_board_count(raw)
        industry = raw.get("hybk") or raw.get("industry") or "未分类"
        if industry:
            industries[industry] += 1
        row = {
            "code": raw.get("c") or raw.get("code", ""),
            "name": raw.get("n") or raw.get("name", ""),
            "industry": industry,
            "board_count": board_count,
            "change_pct": _safe_float(raw.get("zdp")),
            "amount_yi": _money_yi(raw.get("amount")),
            "seal_fund_yi": _money_yi(raw.get("fund")),
            "driver": _infer_market_driver(industry),
        }
        rows.append(row)
        if board_count >= 2:
            industry_consecutive[industry] += 1
        leaders = industry_leaders.setdefault(industry, [])
        leaders.append(row)

    consecutive_rows = [row for row in rows if row["board_count"] >= 2]
    hot_topics = []
    for industry, count in industries.most_common(8):
        leaders = sorted(
            industry_leaders.get(industry, []),
            key=lambda item: (item.get("board_count", 0), item.get("seal_fund_yi", 0), item.get("amount_yi", 0)),
            reverse=True,
        )[:3]
        hot_topics.append({
            "name": industry,
            "limit_count": count,
            "consecutive_count": industry_consecutive.get(industry, 0),
            "leaders": "、".join(row.get("name", "") for row in leaders if row.get("name")),
            "driver": _infer_market_driver(industry),
        })
    return {
        "consecutive_count": len(consecutive_rows),
        "max_consecutive": max((row["board_count"] for row in rows), default=0),
        "top_consecutive": sorted(
            consecutive_rows,
            key=lambda row: (row.get("board_count", 0), row.get("seal_fund_yi", 0), row.get("amount_yi", 0)),
            reverse=True,
        )[:5],
        "hot_industries": industries.most_common(5),
        "hot_topics": hot_topics,
        "data_status": "正常",
    }


def _data_file_path(env_name: str, default: Path) -> Path:
    value = os.environ.get(env_name, "").strip()
    return Path(value).expanduser() if value else default


def _load_json_file(path: Path) -> Optional[object]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_holding(raw: dict, quote: Optional[dict] = None) -> dict:
    quote = quote or {}
    code = str(raw.get("code") or quote.get("code") or "").zfill(6)
    name = raw.get("name") or quote.get("name") or code
    shares = _safe_float(raw.get("shares") or raw.get("volume") or raw.get("quantity")) or 0.0
    cost = _safe_float(raw.get("cost") or raw.get("cost_price") or raw.get("avg_cost"))
    price = _safe_float(raw.get("price") or raw.get("current_price")) or quote.get("price")
    market_value = _safe_float(raw.get("market_value"))
    if market_value is None and price is not None and shares:
        market_value = round(price * shares, 2)
    pnl = _safe_float(raw.get("pnl") or raw.get("profit"))
    if pnl is None and market_value is not None and cost is not None and shares:
        pnl = round(market_value - cost * shares, 2)
    pnl_pct = _safe_float(raw.get("pnl_pct") or raw.get("profit_pct"))
    if pnl_pct is None and pnl is not None and cost and shares:
        base = cost * shares
        if base:
            pnl_pct = round(pnl / base * 100, 2)
    return {
        "code": code,
        "name": name,
        "shares": shares,
        "cost": cost,
        "price": price,
        "market_value": market_value or 0.0,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "change_pct": quote.get("change_pct"),
    }


def load_portfolio_snapshot() -> dict:
    """加载真实持仓快照；CI 可通过 PORTFOLIO_JSON 写入 data/holdings.json。"""
    path = _data_file_path("PORTFOLIO_FILE", PORTFOLIO_FILE)
    if not path.exists():
        return {
            "holdings": [],
            "cash": None,
            "total_assets": None,
            "position_ratio": None,
            "total_market_value": 0.0,
            "total_pnl": None,
            "data_status": "未配置持仓文件 data/holdings.json，持仓和现金按空数据展示。",
        }
    try:
        data = _load_json_file(path)
        if isinstance(data, list):
            holdings_raw = data
            cash = None
            total_assets = None
        else:
            data = data or {}
            holdings_raw = data.get("holdings") or data.get("positions") or []
            cash = _safe_float(data.get("cash"))
            total_assets = _safe_float(data.get("total_assets") or data.get("asset"))
        codes = [str(item.get("code", "")).zfill(6) for item in holdings_raw if item.get("code")]
        try:
            from price_fetcher import fetch_prices
            quotes = fetch_prices(codes) if codes else {}
        except Exception as exc:
            print(f"[复盘] 持仓行情补齐失败: {exc}", flush=True)
            quotes = {}
        holdings = [
            _normalize_holding(item, quotes.get(str(item.get("code", "")).zfill(6)))
            for item in holdings_raw
            if isinstance(item, dict)
        ]
        total_market_value = round(sum(item.get("market_value", 0) for item in holdings), 2)
        if total_assets is None and cash is not None:
            total_assets = round(total_market_value + cash, 2)
        position_ratio = round(total_market_value / total_assets * 100, 2) if total_assets else None
        pnl_values = [item.get("pnl") for item in holdings if item.get("pnl") is not None]
        total_pnl = round(sum(pnl_values), 2) if pnl_values else None
        return {
            "holdings": holdings,
            "cash": cash,
            "total_assets": total_assets,
            "position_ratio": position_ratio,
            "total_market_value": total_market_value,
            "total_pnl": total_pnl,
            "data_status": "正常",
        }
    except Exception as exc:
        return {
            "holdings": [],
            "cash": None,
            "total_assets": None,
            "position_ratio": None,
            "total_market_value": 0.0,
            "total_pnl": None,
            "data_status": f"持仓文件解析失败：{exc}",
        }


def _announcement_category(title: str, columns: str) -> str:
    text = f"{title} {columns}"
    if any(keyword in text for keyword in ("立案", "处罚", "风险", "退市", "ST", "减持", "诉讼", "仲裁", "亏损")):
        return "风险"
    if any(keyword in text for keyword in ("增持", "回购", "中标", "合同", "订单", "重组", "并购", "预增", "分红")):
        return "机会"
    if any(keyword in text for keyword in ("业绩", "年报", "季报", "快报", "预告")):
        return "业绩"
    if any(keyword in text for keyword in ("定增", "债券", "募集", "融资")):
        return "融资"
    return "常规"


def fetch_market_announcements(page_size: int = 30) -> dict:
    """抓取 A 股公告列表，用于信息与新闻复盘。"""
    params = {
        "ann_type": "A",
        "client_source": "web",
        "page_index": 1,
        "page_size": page_size,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": "https://data.eastmoney.com/notices/",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        resp = requests.get(EASTMONEY_NOTICE_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = (data.get("data") or {}).get("list") or []
    except Exception as exc:
        return {"rows": [], "category_counts": [], "data_status": f"公告数据获取失败：{exc}"}

    rows = []
    counter = Counter()
    for item in items:
        codes = item.get("codes") or []
        first_code = codes[0] if codes else {}
        columns = "、".join(
            column.get("column_name", "")
            for column in (item.get("columns") or [])
            if column.get("column_name")
        )
        title = item.get("title") or ""
        category = _announcement_category(title, columns)
        counter[category] += 1
        rows.append({
            "title": title,
            "company": first_code.get("short_name", ""),
            "code": first_code.get("stock_code", ""),
            "columns": columns,
            "category": category,
            "time": (item.get("display_time") or item.get("notice_date") or "")[:16],
        })
    return {
        "rows": rows,
        "category_counts": counter.most_common(),
        "data_status": "正常" if rows else "公告列表为空",
        "source": "东方财富公告",
    }


def _after_close_start(now: Optional[datetime] = None) -> datetime:
    now = now or _now_shanghai()
    today_close = now.replace(hour=15, minute=0, second=0, microsecond=0)
    if now >= today_close:
        return today_close
    previous_day = now.date() - timedelta(days=1)
    while previous_day.weekday() >= 5:
        previous_day -= timedelta(days=1)
    return datetime.combine(previous_day, datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai")).replace(hour=15)


def _news_importance(title: str, summary: str, title_color=None) -> tuple[str, int]:
    text = f"{title} {summary}"
    score = 1
    label = "关注"
    if str(title_color) in ("3", "4", "5"):
        score += 1
    if any(keyword in text for keyword in ("证监会", "国务院", "央行", "发改委", "财政部", "交易所", "政策", "降准", "降息")):
        score += 2
        label = "政策"
    if any(keyword in text for keyword in ("半导体", "芯片", "AI", "人工智能", "算力", "机器人", "新能源", "地产", "医药")):
        score += 1
        label = "题材"
    if any(keyword in text for keyword in ("美联储", "美元", "黄金", "原油", "商品", "期货", "人民币", "外围")):
        score += 1
        label = "外围"
    if any(keyword in text for keyword in ("涨停", "大涨", "跌停", "大跌", "风险", "处罚", "立案")):
        score += 1
        label = "风险/异动" if any(k in text for k in ("跌停", "大跌", "风险", "处罚", "立案")) else label
    return label, score


def fetch_market_news_since_close(page_size: int = 80) -> dict:
    """抓取东方财富 7x24 快讯，过滤收盘后到当前的重要消息。"""
    start = _after_close_start()
    params = {
        "client": "web",
        "biz": "web_724",
        "fastColumn": "102",
        "sortEnd": "",
        "pageSize": page_size,
        "req_trace": str(uuid.uuid4()),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": "https://kuaixun.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        resp = requests.get(EASTMONEY_FAST_NEWS_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = (data.get("data") or {}).get("fastNewsList") or []
    except Exception as exc:
        return {
            "rows": [],
            "start_time": start.strftime("%Y-%m-%d %H:%M"),
            "data_status": f"快讯数据获取失败：{exc}",
        }

    rows = []
    for item in items:
        show_time = item.get("showTime") or item.get("showtime") or ""
        try:
            news_time = datetime.strptime(show_time[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except Exception:
            continue
        if news_time < start:
            continue
        title = item.get("title") or ""
        summary = item.get("summary") or ""
        category, score = _news_importance(title, summary, item.get("titleColor"))
        if score < 2 and len(rows) >= 8:
            continue
        rows.append({
            "time": news_time.strftime("%H:%M"),
            "title": title,
            "summary": summary,
            "category": category,
            "importance": score,
        })
    rows.sort(key=lambda row: (row.get("importance", 0), row.get("time", "")), reverse=True)
    return {
        "rows": rows[:10],
        "start_time": start.strftime("%Y-%m-%d %H:%M"),
        "data_status": "正常" if rows else f"收盘后暂无重要快讯（起点 {start.strftime('%Y-%m-%d %H:%M')}）",
        "source": "东方财富 7x24 快讯",
    }


def load_trading_journal() -> dict:
    """加载交易日志；CI 可通过 TRADING_JOURNAL_JSON 写入 data/trading_journal.json。"""
    path = _data_file_path("TRADING_JOURNAL_FILE", TRADING_JOURNAL_FILE)
    if not path.exists():
        return {
            "data_status": "未配置交易日志 data/trading_journal.json，心理复盘按空记录展示。",
        }
    try:
        data = _load_json_file(path)
        if isinstance(data, list):
            entries = data
        else:
            entries = (data or {}).get("entries") if isinstance(data, dict) else []
            if not entries and isinstance(data, dict):
                entries = [data]
        entries = [entry for entry in entries if isinstance(entry, dict)]
        latest = sorted(entries, key=lambda item: str(item.get("date") or item.get("time") or ""), reverse=True)[0] if entries else {}
        latest["data_status"] = "正常" if latest else "交易日志为空"
        return latest
    except Exception as exc:
        return {"data_status": f"交易日志解析失败：{exc}"}


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
            "strong_industries": [],
            "strong_concepts": [],
            "data_status": "板块数据不可用",
        }

    up = sum(1 for board in boards if board.get("change_pct", 0) > 0)
    down = sum(1 for board in boards if board.get("change_pct", 0) < 0)
    flat = max(0, len(boards) - up - down)
    top_amount = sorted(boards, key=lambda b: b.get("amount_yi", 0), reverse=True)[:5]
    top_inflow = sorted(boards, key=lambda b: b.get("main_net_yi", 0), reverse=True)[:5]
    top_outflow = sorted(boards, key=lambda b: b.get("main_net_yi", 0))[:5]
    strong_boards = [
        board for board in boards
        if board.get("change_pct", 0) >= 1.5 and board.get("up_ratio", 0) >= 55
    ]
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
        "strong_industries": [
            board for board in sorted(
                [b for b in strong_boards if b.get("type") == "industry"],
                key=lambda b: (b.get("change_pct", 0), b.get("main_net_yi", 0)),
                reverse=True,
            )[:5]
        ],
        "strong_concepts": [
            board for board in sorted(
                [b for b in strong_boards if b.get("type") == "concept"],
                key=lambda b: (b.get("change_pct", 0), b.get("main_net_yi", 0)),
                reverse=True,
            )[:5]
        ],
        "data_status": "正常",
    }


def _append_table(lines: list[str], headers: list[str], rows: list[list[str]]) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(v) for v in row) + " |")


def build_review_markdown_report(
    indices: list[dict],
    market: dict,
    breadth: dict,
    limit_summary: dict,
    all_boards: list[dict],
    board_stats: dict,
    lhb_summary: dict,
    portfolio: dict,
    announcements: dict,
    market_news: dict,
    journal: dict,
    signal_boards: list[dict],
    watchlist: list[dict],
    previous: dict,
) -> str:
    now = _now_shanghai().strftime("%Y-%m-%d %H:%M:%S 北京时间")
    volume_desc = compare_volume(breadth.get("total_amount_yi", 0), previous)
    style = infer_market_style(all_boards, indices, breadth, limit_summary)
    emotion = emotion_score(market, breadth, signal_boards)
    strongest, weakest = _strongest_and_weakest_boards(all_boards)
    top_signal = signal_boards[0] if signal_boards else (strongest[0] if strongest else {})
    breadth_source = breadth.get("source", "东方财富全A快照")
    breadth_status = breadth.get("data_status", "正常")
    market_status = market.get("data_status", "正常")
    board_status = board_stats.get("data_status", "正常")
    lhb_status = lhb_summary.get("data_status", "正常")
    limit_status = limit_summary.get("data_status", "正常")
    portfolio_status = portfolio.get("data_status", "正常")
    announcement_status = announcements.get("data_status", "正常")
    news_status = market_news.get("data_status", "正常")
    journal_status = journal.get("data_status", "正常")
    data_status = "；".join(
        status for status in (
            market_status,
            breadth_status,
            limit_status,
            board_status,
            lhb_status,
            portfolio_status,
            announcement_status,
            news_status,
            journal_status,
        )
        if status and status != "正常"
    ) or "正常"
    amount_source = breadth.get("amount_source", breadth_source)
    amount_label = "全A约" if amount_source == "东方财富全A快照" else f"{amount_source}约"
    consecutive_count = limit_summary.get("consecutive_count", 0)
    max_consecutive = limit_summary.get("max_consecutive", 0)
    hot_limit_industries = "、".join(
        f"{name}({count})" for name, count in limit_summary.get("hot_industries", [])[:5]
    ) or "暂无集中方向"

    lines = [
        "# A股盘后复盘报告",
        "",
        f"> 生成时间: {now}",
        "> 数据源: 同花顺市场宽度；东方财富指数/板块/个股快照/涨跌停池/龙虎榜/公告；本地持仓与交易日志。",
        f"> 数据完整性: {data_status}",
        "",
        "## 一、大盘行情与市场情绪",
        "",
        f"- 指数环境: **{market.get('level', '未知')}**，大盘评分 {market.get('score', 0)} / 100。",
        f"- 今日成交额: {amount_label} **{_fmt_yi(breadth.get('total_amount_yi'))}**；{volume_desc}。",
        f"- 市场宽度: {breadth.get('up', 0)} 涨 / {breadth.get('down', 0)} 跌 / {breadth.get('flat', 0)} 平（{breadth_source}），赚钱效应 **{breadth.get('money_effect', '未知')}**。",
        f"- 市场风格: **{style.get('label')}**；{style.get('reason')}。",
        f"- 涨停/跌停: 涨停 {breadth.get('limit_up', 0)} 只，跌停 {breadth.get('limit_down', 0)} 只（{breadth.get('limit_source', '实际涨跌停池')} {breadth.get('limit_date', '')}）；连板 {consecutive_count} 只，最高 {max_consecutive} 连板。",
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
    if limit_summary.get("top_consecutive"):
        lines.extend(["", "连板股观察："])
        _append_table(
            lines,
            ["股票", "行业", "连板", "涨幅", "驱动因素"],
            [
                [
                    row.get("name", "-"),
                    row.get("industry", "-"),
                    row.get("board_count", "-"),
                    _fmt_pct(row.get("change_pct")),
                    row.get("driver", "-"),
                ]
                for row in limit_summary.get("top_consecutive", [])
            ],
        )

    topic_rows = build_topic_rows(signal_boards, board_stats, limit_summary)
    lines.extend([
        "",
        "## 二、板块与题材",
        "",
        f"- **主线判断**: {format_topic_conclusion(topic_rows, board_stats)}",
        f"- **涨停集中**: {hot_limit_industries}。",
        f"- **板块统计**: {format_board_stat_line(board_stats)}",
        "",
    ])
    _append_table(
        lines,
        ["方向", "强度", "代表个股", "驱动因素", "操作观察"],
        [
            [
                row.get("name", "-"),
                row.get("strength", "-"),
                row.get("leaders", "-"),
                row.get("driver", "-"),
                row.get("watch", "-"),
            ]
            for row in topic_rows[:6]
        ],
    )
    if weakest:
        lines.extend(["", "弱势/回避方向："])
        _append_table(
            lines,
            ["板块", "跌幅/弱势", "原因"],
            [
                [b["name"], _fmt_pct(b.get("change_pct")), "资金流出或缺少领涨扩散"]
                for b in weakest[:3]
            ],
        )

    lhb_view = format_lhb_readable_view(lhb_summary, topic_rows)
    lines.extend([
        "",
        "## 三、龙虎榜与短线资金",
        "",
        f"- **资金结论**: {lhb_view['conclusion']}",
        f"- **上榜概览**: {lhb_summary.get('date') or '暂无日期'}，上榜 {lhb_summary.get('count', 0)} 只，净买入 {lhb_summary.get('net_buy_count', 0)} / 净卖出 {lhb_summary.get('net_sell_count', 0)}，整体净额 **{_fmt_yi(lhb_summary.get('total_net_yi'))}**。",
        f"- **热点方向**: {lhb_view['hot_direction']}",
        f"- **上榜原因**: {_format_lhb_reasons(lhb_summary.get('reason_counts', []))}。",
        "",
        "买入焦点 Top3：",
    ])
    _append_table(
        lines,
        ["股票", "涨跌幅", "净额", "看点"],
        [
            [
                row.get("name", "-"),
                _fmt_pct(row.get("change_pct")),
                _fmt_yi(row.get("net_yi")),
                _format_lhb_row_focus(row),
            ]
            for row in lhb_summary.get("top_buy", [])[:3]
        ],
    )
    lines.extend(["", "卖出风险 Top3："])
    _append_table(
        lines,
        ["股票", "涨跌幅", "净额", "风险信号"],
        [
            [
                row.get("name", "-"),
                _fmt_pct(row.get("change_pct")),
                _fmt_yi(row.get("net_yi")),
                _format_lhb_row_focus(row),
            ]
            for row in lhb_summary.get("top_sell", [])[:3]
        ],
    )

    lines.extend([
        "",
        "## 四、个股复盘",
        "",
        f"- 持仓数据: {portfolio_status}",
    ])
    if portfolio.get("holdings"):
        _append_table(
            lines,
            ["股票", "持仓股数", "现价", "涨跌幅", "市值", "浮盈亏", "收益率"],
            [
                [
                    row.get("name", "-"),
                    f"{row.get('shares', 0):.0f}",
                    _fmt_money(row.get("price")),
                    _fmt_pct(row.get("change_pct")),
                    _fmt_money(row.get("market_value")),
                    _fmt_money(row.get("pnl")),
                    _fmt_pct(row.get("pnl_pct")),
                ]
                for row in sorted(
                    portfolio.get("holdings", []),
                    key=lambda item: item.get("market_value", 0),
                    reverse=True,
                )[:12]
            ],
        )
    else:
        lines.append("- 当前未读取到真实持仓；若当日有交易，请用交易日志补充决策复盘。")
    lines.extend(["", "推荐池观察："])
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
        lines.append("- 暂无推荐池行情数据。")

    lines.extend([
        "",
        "## 五、主线与策略分析",
        "",
        f"- 今日市场主线: **{_format_topic_names(topic_rows, 3)}**。",
        f"- 主线状态: {_mainline_state(signal_boards, emotion)}。",
        f"- 明日资金可能偏向: {_format_topic_names(topic_rows, 3)}。",
        f"- 策略改进: 大盘评分低于 58 或主线情绪低于 5 时，减少追高，等待回踩或二次确认。",
        "",
        "## 六、持仓与仓位管理",
        "",
        f"- 建议仓位上限: **{market.get('position_ceiling', '未知')}**。",
        f"- 今日总资产: {_fmt_money(portfolio.get('total_assets'))}，股票市值 {_fmt_money(portfolio.get('total_market_value'))}，现金 {_fmt_money(portfolio.get('cash'))}，仓位 {portfolio.get('position_ratio') if portfolio.get('position_ratio') is not None else '-'}%。",
        f"- 今日持仓浮盈亏: {_fmt_money(portfolio.get('total_pnl'))}。",
        "- 止损止盈: 对推荐池个股按各自买点/风控执行；跌破关键均线且板块弱化时优先减仓。",
        "",
        "## 七、信息与新闻",
        "",
        f"- **收盘后快讯**: {news_status}；统计起点 {market_news.get('start_time', '-')}。",
        f"- **公告数据**: {announcement_status}；{_format_announcement_counts(announcements.get('category_counts', []))}。",
        "- **解读原则**: 政策/产业消息优先看是否能强化主线，个股公告优先识别风险、回购、订单和业绩变化。",
        "",
    ])
    if market_news.get("rows"):
        lines.extend(["收盘后重要新闻："])
        _append_table(
            lines,
            ["时间", "类别", "标题", "影响方向"],
            [
                [
                    row.get("time", "-"),
                    row.get("category", "-"),
                    _short_text(row.get("title") or row.get("summary"), 44),
                    _format_news_impact(row),
                ]
                for row in market_news.get("rows", [])[:8]
            ],
        )
        lines.append("")
    if announcements.get("rows"):
        lines.extend(["重要公告："])
        _append_table(
            lines,
            ["公司", "类别", "公告", "时间"],
            [
                [
                    row.get("company") or row.get("code") or "-",
                    row.get("category", "-"),
                    _short_text(row.get("title", "-"), 44),
                    row.get("time", "-"),
                ]
                for row in announcements.get("rows", [])[:6]
            ],
        )
        lines.append("")
    else:
        lines.extend(["- 今日未获取到公告列表。", ""])
    lines.extend([
        "## 八、明日交易计划",
        "",
        f"- 重点指数: 观察主要指数能否维持 {market.get('level', '未知')} 对应的成交额与上涨家数扩散。",
        f"- 重点板块: {_format_topic_names(topic_rows, 5)}。",
        "- 买入条件: 板块继续放量、龙头不破位、二线跟风扩散；避免单日大涨后无承接追高。",
        "- 风险点: 成交额缩量、涨停数量下降、跌停增多、主线龙头高位放量滞涨。",
        "- 策略目标: 趋势跟随为主，强主线龙头确认后再找补涨；弱市只观察不追高。",
        "",
        "## 九、心理与复盘总结",
        "",
        f"- 情绪评分: {journal.get('emotion_score', '-')}/10；{journal_status}",
        f"- 纪律检查: {journal.get('discipline') or '未记录；默认检查是否只在计划内买卖、是否按止损/止盈执行、是否追高。'}",
        f"- 成功交易: {journal.get('success_trade') or journal.get('best_trade') or '未记录'}",
        f"- 失败交易: {journal.get('failed_trade') or journal.get('worst_trade') or '未记录'}",
        f"- 今日经验: {journal.get('lessons') or '优先相信数据共振，不因单只股波动改变整体策略。'}",
        "- 明日复盘重点: 成交额、上涨家数、涨停数量、主线板块持续性、龙头与跟风梯队。",
        "",
        "---",
        "",
        "*免责声明：本复盘由规则模型自动生成，仅用于交易复盘和计划管理，不构成投资建议。*",
    ])
    return "\n".join(lines)


def render_market_review_html(markdown_text: str) -> str:
    """将复盘内容渲染为适合邮件/浏览器阅读的 HTML，不暴露 Markdown 符号。"""
    markdown_text = _normalize_markdown_tables(markdown_text)
    body = md_lib.markdown(markdown_text, extensions=["tables"])
    replacements = {
        "<h1>": '<h1 class="title">',
        "<h2>": '<h2 class="section-title">',
        "<table>": '<div class="table-wrap"><table>',
        "</table>": "</table></div>",
        "<th>": "<th>",
        "<td>": "<td>",
    }
    for old, new in replacements.items():
        body = body.replace(old, new)

    now = _now_shanghai().strftime("%Y-%m-%d %H:%M:%S 北京时间")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>A股盘后复盘报告</title>
  <style>
    body {{ margin:0; background:#f3f6fb; color:#172033; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif; }}
    .page {{ max-width:1080px; margin:0 auto; padding:20px 14px 32px; }}
    .report {{ background:#fff; border:1px solid #e5e7eb; border-radius:14px; overflow:hidden; box-shadow:0 8px 24px rgba(15,23,42,.06); }}
    .hero {{ padding:24px 28px; background:#123b79; color:#fff; }}
    .hero h1 {{ margin:0; font-size:24px; line-height:1.35; }}
    .hero p {{ margin:8px 0 0; font-size:13px; opacity:.9; }}
    .content {{ padding:20px 26px 28px; }}
    .title {{ display:none; }}
    .section-title {{ margin:24px 0 12px; padding:10px 14px; border-left:5px solid #2563eb; background:#eff6ff; color:#123b79; border-radius:8px; font-size:18px; }}
    p {{ margin:8px 0; line-height:1.75; }}
    ul {{ margin:8px 0 14px; padding:0; list-style:none; }}
    li {{ margin:8px 0; padding:10px 12px; background:#f8fafc; border:1px solid #edf2f7; border-radius:8px; line-height:1.7; }}
    strong {{ color:#b91c1c; }}
    .table-wrap {{ width:100%; overflow-x:auto; margin:10px 0 18px; border:1px solid #e5e7eb; border-radius:10px; }}
    table {{ width:100%; border-collapse:collapse; min-width:680px; background:#fff; }}
    th {{ background:#f1f5f9; color:#334155; text-align:left; font-weight:700; padding:10px 12px; font-size:13px; border-bottom:1px solid #e5e7eb; }}
    td {{ padding:10px 12px; font-size:13px; line-height:1.55; border-bottom:1px solid #edf2f7; vertical-align:top; }}
    tr:last-child td {{ border-bottom:none; }}
    tr:nth-child(even) td {{ background:#fbfdff; }}
    blockquote {{ margin:10px 0 16px; padding:12px 14px; background:#fff7ed; border-left:5px solid #f97316; color:#7c2d12; border-radius:8px; }}
    hr {{ border:0; border-top:1px solid #e5e7eb; margin:24px 0; }}
    em {{ color:#64748b; font-size:12px; }}
    .footer {{ padding:14px 26px; background:#f8fafc; color:#64748b; border-top:1px solid #e5e7eb; font-size:12px; line-height:1.7; }}
  </style>
</head>
<body>
  <div class="page">
    <div class="report">
      <div class="hero">
        <h1>A股盘后复盘报告</h1>
        <p>{html.escape(now)} · 多渠道数据补全：同花顺 / 东方财富 / 腾讯行情 / 涨跌停池 / 快讯公告</p>
      </div>
      <div class="content">
        {body}
      </div>
      <div class="footer">本报告由自动化复盘系统生成，仅用于交易复盘和计划管理，不构成投资建议。</div>
    </div>
  </div>
</body>
</html>"""


def _normalize_markdown_tables(markdown_text: str) -> str:
    """确保紧跟在普通文本后的管道表格能被 Markdown tables 扩展识别。"""
    normalized = []
    previous = ""
    for line in markdown_text.splitlines():
        if line.startswith("|") and previous and not previous.startswith("|") and previous.strip():
            normalized.append("")
        normalized.append(line)
        previous = line
    return "\n".join(normalized)


def build_review_report(*args, **kwargs) -> str:
    markdown_text = build_review_markdown_report(*args, **kwargs)
    return render_market_review_html(markdown_text)


def infer_market_style(
    boards: list[dict],
    indices: list[dict],
    breadth: dict,
    limit_summary: dict,
) -> dict:
    """给出明确市场风格，即使板块源降级也不返回未知。"""
    board_style = style_bias_from_boards(boards)
    if board_style:
        return board_style

    index_map = {item.get("name", ""): item for item in indices}
    sh = index_map.get("上证指数", {})
    cyb = index_map.get("创业板指", {})
    kc50 = index_map.get("科创50", {})
    hs300 = index_map.get("沪深300", {})
    sh_change = sh.get("change_pct")
    growth_changes = [
        item.get("change_pct")
        for item in (cyb, kc50)
        if item.get("change_pct") is not None
    ]
    growth_change = sum(growth_changes) / len(growth_changes) if growth_changes else None
    hs300_change = hs300.get("change_pct")
    hot_industries = limit_summary.get("hot_industries", [])
    hot_names = "、".join(name for name, _ in hot_industries)
    up = breadth.get("up", 0) or 0
    down = breadth.get("down", 0) or 0

    growth_keywords = ("半导体", "芯片", "IT服务", "软件", "机器人", "专用设备", "AI", "算力", "创新药")
    cyclical_keywords = ("化学", "有色", "煤炭", "钢铁", "石油", "房地产")
    defensive_keywords = ("银行", "保险", "证券", "公用事业", "电力", "白酒")

    def hot_score(keywords: tuple[str, ...]) -> int:
        return sum(
            count
            for name, count in hot_industries
            if any(keyword in name for keyword in keywords)
        )

    style_scores = {
        "growth": hot_score(growth_keywords),
        "cyclical": hot_score(cyclical_keywords),
        "defensive": hot_score(defensive_keywords),
    }
    top_style, top_score = max(style_scores.items(), key=lambda item: item[1])

    if top_style == "growth" and top_score > 0:
        return {
            "label": "成长题材占优",
            "reason": f"涨停集中在 {hot_names or '成长方向'}，偏科技成长与主题扩散",
        }
    if top_style == "defensive" and top_score > 0:
        return {
            "label": "大盘蓝筹/防守占优",
            "reason": f"涨停集中在 {hot_names}，偏权重或防守资产",
        }
    if top_style == "cyclical" and top_score > 0:
        return {
            "label": "周期/资源方向占优",
            "reason": f"涨停集中在 {hot_names}，偏顺周期和资源品",
        }
    if growth_change is not None and sh_change is not None and growth_change > sh_change + 0.3:
        return {
            "label": "成长股相对占优",
            "reason": f"创业板/科创平均涨跌幅 {_fmt_pct(growth_change)} 强于上证 {_fmt_pct(sh_change)}",
        }
    if hs300_change is not None and growth_change is not None and hs300_change > growth_change + 0.3:
        return {
            "label": "大盘蓝筹相对占优",
            "reason": f"沪深300 {_fmt_pct(hs300_change)} 强于成长指数均值 {_fmt_pct(growth_change)}",
        }
    if up and down and up > down * 1.2:
        return {
            "label": "普涨轮动",
            "reason": f"上涨家数 {up} 多于下跌家数 {down}，市场扩散较均衡",
        }
    if up and down and down > up * 1.2:
        return {
            "label": "防守避险",
            "reason": f"下跌家数 {down} 多于上涨家数 {up}，资金偏谨慎",
        }
    return {
        "label": "主题轮动",
        "reason": "指数与市场宽度未形成单一风格优势，按轮动市处理",
    }


def style_bias_from_boards(boards: list[dict]) -> Optional[dict]:
    if not boards:
        return None
    top = sorted(boards, key=lambda b: (b.get("change_pct", 0), b.get("main_net_yi", 0)), reverse=True)[:20]
    names = " ".join(b.get("name", "") for b in top)
    if any(k in names for k in ("银行", "证券", "保险", "白酒", "中字头", "煤炭", "石油")):
        return {"label": "大盘蓝筹占优", "reason": "强势板块集中在权重、金融或资源方向"}
    if any(k in names for k in ("AI", "人工智能", "算力", "半导体", "芯片", "机器人", "创新药")):
        return {"label": "成长题材占优", "reason": "强势板块集中在科技成长或新兴产业方向"}
    if any(k in names for k in ("小盘", "专精特新", "次新", "微盘")):
        return {"label": "中小盘情绪占优", "reason": "强势板块偏小盘、次新或专精特新方向"}
    leaders = "、".join(b.get("name", "") for b in top[:3] if b.get("name"))
    return {"label": "主题轮动", "reason": f"强势方向分散，领涨板块为 {leaders or '暂无明确板块'}"}


def _format_board_names(boards: list[dict], limit: int) -> str:
    names = [b.get("name", "") for b in boards[:limit] if b.get("name")]
    return "、".join(names) if names else "暂无明确方向"


def _format_topic_names(topic_rows: list[dict], limit: int) -> str:
    names = [
        row.get("name", "")
        for row in topic_rows[:limit]
        if row.get("name") and row.get("name") != "暂无明确主线"
    ]
    return "、".join(names) if names else "暂无明确方向"


def _format_lhb_reasons(reason_counts: list[tuple[str, int]]) -> str:
    if not reason_counts:
        return "暂无龙虎榜原因分布"
    return "、".join(f"{reason}({count})" for reason, count in reason_counts[:5])


def build_topic_rows(signal_boards: list[dict], board_stats: dict, limit_summary: dict) -> list[dict]:
    rows = []
    seen = set()
    for board in signal_boards[:6]:
        name = board.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        strength_parts = []
        if board.get("change_pct") is not None:
            strength_parts.append(_fmt_pct(board.get("change_pct")))
        if board.get("main_net_yi") is not None:
            strength_parts.append(f"主力{_fmt_yi(board.get('main_net_yi'))}")
        if board.get("up_ratio") is not None:
            strength_parts.append(f"上涨{board.get('up_ratio', 0):.0f}%")
        rows.append({
            "name": name,
            "strength": " / ".join(strength_parts) or board.get("signal_level", "强势"),
            "leaders": board.get("leader_name") or "-",
            "driver": board.get("logic_hint") or _infer_market_driver(name),
            "watch": "看龙头承接与跟风扩散",
        })
    for topic in limit_summary.get("hot_topics", []):
        name = topic.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        limit_count = topic.get("limit_count", 0)
        consecutive = topic.get("consecutive_count", 0)
        rows.append({
            "name": name,
            "strength": f"涨停{limit_count}只 / 连板{consecutive}只",
            "leaders": topic.get("leaders") or "-",
            "driver": topic.get("driver") or _infer_market_driver(name),
            "watch": "板块数据降级时以涨停池验证强度",
        })
    if not rows:
        rows.append({
            "name": "暂无明确主线",
            "strength": "弱",
            "leaders": "-",
            "driver": "板块、涨停和资金未形成共振",
            "watch": "降低追高，等待新方向确认",
        })
    return rows


def format_topic_conclusion(topic_rows: list[dict], board_stats: dict) -> str:
    leader = topic_rows[0] if topic_rows else {}
    if not leader or leader.get("name") == "暂无明确主线":
        return "暂无明确主线，市场更偏轮动或防守。"
    source_note = "板块行情源正常" if board_stats.get("total", 0) else "板块行情源降级，使用涨停池/连板池兜底"
    return f"{leader.get('name')} 最强，{leader.get('strength')}；{source_note}。"


def format_board_stat_line(board_stats: dict) -> str:
    if not board_stats.get("total"):
        return "板块快照暂不可用，已用涨停池和连板股补充题材强度。"
    return (
        f"共 {board_stats.get('total', 0)} 个板块，"
        f"{board_stats.get('up', 0)} 涨 / {board_stats.get('down', 0)} 跌；"
        f"强势 {board_stats.get('strong_count', 0)} 个，弱势 {board_stats.get('weak_count', 0)} 个；"
        f"主力净流入 {board_stats.get('main_in_count', 0)} 个 / 净流出 {board_stats.get('main_out_count', 0)} 个。"
    )


def format_lhb_readable_view(lhb_summary: dict, topic_rows: list[dict]) -> dict:
    net = lhb_summary.get("total_net_yi", 0) or 0
    buy_count = lhb_summary.get("net_buy_count", 0) or 0
    sell_count = lhb_summary.get("net_sell_count", 0) or 0
    direction = _format_topic_names(topic_rows, 3)

    if not lhb_summary.get("count"):
        conclusion = "暂无龙虎榜明细，短线资金方向不明确。"
    elif net > 0 and buy_count >= sell_count:
        conclusion = f"短线资金偏进攻，龙虎榜净流入 {_fmt_yi(net)}，买方席位占优。"
    elif net < 0 and sell_count > buy_count:
        conclusion = f"短线资金偏兑现，龙虎榜净流出 {_fmt_yi(abs(net))}，卖方压力更明显。"
    elif net > 0:
        conclusion = f"金额净流入 {_fmt_yi(net)}，但买卖家数分化，适合看结构不追总量。"
    elif net < 0:
        conclusion = f"金额净流出 {_fmt_yi(abs(net))}，但个股仍有分化，重点避开放量净卖出票。"
    else:
        conclusion = "龙虎榜买卖大体均衡，短线资金更偏轮动。"

    if direction == "暂无明确方向":
        direction = "暂无明确板块共振，优先看个股独立逻辑。"
    else:
        direction = f"{direction}；若买入榜与这些方向重合，说明热点承接更强。"
    return {"conclusion": conclusion, "hot_direction": direction}


def _format_lhb_row_focus(row: dict) -> str:
    reason = (row.get("explain") or row.get("reason") or "").strip()
    if reason:
        reason = reason.split("，")[0][:22]
    else:
        reason = "席位异动"
    buy = row.get("buy_yi", 0) or 0
    sell = row.get("sell_yi", 0) or 0
    net = row.get("net_yi", 0) or 0
    if net > 0:
        return f"{reason}；买入 {_fmt_yi(buy)} > 卖出 {_fmt_yi(sell)}"
    if net < 0:
        return f"{reason}；卖出 {_fmt_yi(sell)} > 买入 {_fmt_yi(buy)}"
    return f"{reason}；买卖接近平衡"


def _format_news_impact(row: dict) -> str:
    text = f"{row.get('title', '')} {row.get('summary', '')}"
    if any(keyword in text for keyword in ("证监会", "国务院", "央行", "财政部", "政策", "降准", "降息")):
        return "影响市场风险偏好"
    if any(keyword in text for keyword in ("半导体", "芯片", "AI", "算力", "机器人")):
        return "关注科技成长主线"
    if any(keyword in text for keyword in ("地产", "房地产", "消费", "内需")):
        return "关注顺周期/内需链"
    if any(keyword in text for keyword in ("美联储", "美元", "原油", "黄金", "人民币")):
        return "关注外围和商品扰动"
    if any(keyword in text for keyword in ("风险", "处罚", "立案", "大跌", "跌停")):
        return "偏风险提示"
    return "关注明日资金反馈"


def _format_announcement_counts(category_counts: list[tuple[str, int]]) -> str:
    if not category_counts:
        return "暂无公告分类"
    return "、".join(f"{category} {count} 条" for category, count in category_counts[:5])


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
    ths_breadth = fetch_ths_market_breadth()
    limit_stats = fetch_limit_pool_stats()
    limit_summary = summarize_limit_pool(limit_stats)
    breadth = summarize_breadth(
        all_stocks,
        market=market,
        ths_breadth=ths_breadth,
        limit_stats=limit_stats,
    )
    market = complete_market_environment(market, breadth, limit_summary)
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
    portfolio = load_portfolio_snapshot()
    announcements = fetch_market_announcements()
    market_news = fetch_market_news_since_close()
    journal = load_trading_journal()
    report = build_review_report(
        indices=indices,
        market=market,
        breadth=breadth,
        limit_summary=limit_summary,
        all_boards=all_boards,
        board_stats=board_stats,
        lhb_summary=lhb_summary,
        portfolio=portfolio,
        announcements=announcements,
        market_news=market_news,
        journal=journal,
        signal_boards=signal_boards,
        watchlist=watchlist,
        previous=previous,
    )
    snapshot = {
        "generated_at": _now_shanghai().isoformat(),
        "total_amount_yi": breadth.get("total_amount_yi", 0),
        "market": market,
        "breadth": breadth,
        "ths_breadth": ths_breadth,
        "limit_stats": {
            key: value
            for key, value in limit_stats.items()
            if key not in ("limit_up_pool", "limit_down_pool")
        },
        "limit_summary": limit_summary,
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
        "portfolio": {
            key: value
            for key, value in portfolio.items()
            if key != "holdings"
        },
        "announcements": {
            "count": len(announcements.get("rows", [])),
            "category_counts": announcements.get("category_counts", []),
            "data_status": announcements.get("data_status", ""),
        },
        "market_news": {
            "count": len(market_news.get("rows", [])),
            "start_time": market_news.get("start_time", ""),
            "data_status": market_news.get("data_status", ""),
        },
        "journal_status": journal.get("data_status", ""),
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
