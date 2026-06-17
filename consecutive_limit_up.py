#!/usr/bin/env python3
"""A股连板股票扫描模块。

扫描涨停股票，计算连板天数，按题材/板块分类分组，
使用 AI 总结连板原因，输出报告。

数据源：东方财富实时行情 + 腾讯前复权日K线。

市场状态与日期逻辑：
  - 盘前（< 9:30）/ 盘后（>= 15:00）：使用上一交易日数据，分组日期为上一交易日
  - 盘中（9:30 ~ 15:00）：使用当日实时数据，分组日期为当日
"""

import json
import math
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# 绕过本地代理直连东方财富（CI 环境无需此操作）
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)

import requests
import yaml


# ── 常量 ──

EASTMONEY_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"

LIMIT_UP_FIELDS = (
    "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
    "f20,f21,f22,f23,f24,f25,f62,f100,f115,f152"
)


# ── 基础工具 ──

def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, "-", ""):
            return default
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _money_yi(value) -> float:
    return round(_safe_float(value) / 100000000, 2)


# ── HTTP Session ──

_em_session = None


def _get_session() -> requests.Session:
    global _em_session
    if _em_session is None:
        _em_session = requests.Session()
        _em_session.trust_env = False
        _em_session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        })
    return _em_session


# ── 市场状态检测 ──

def get_market_status() -> dict:
    """检测当前 A 股市场状态。

    Returns:
        {
            "is_open": bool,         # 是否在盘中交易时段
            "data_label": str,       # "今日" / "昨日"
            "data_date": date,       # 数据对应的交易日
            "group_date": str,       # 分组名日期 MM-DD
            "report_label": str,     # 报告中的日期描述
        }
    """
    now = _now_shanghai()
    today = now.date()
    hour, minute = now.hour, now.minute
    time_minutes = hour * 60 + minute

    # 判断是否在交易时段: 9:30 ~ 11:30, 13:00 ~ 15:00
    is_trading_time = (
        (570 <= time_minutes <= 690) or   # 9:30 ~ 11:30
        (780 <= time_minutes <= 900)       # 13:00 ~ 15:00
    )

    # 判断是否为交易日
    is_trading_day = _is_trading_day(today)

    if is_trading_day and is_trading_time:
        return {
            "is_open": True,
            "data_label": "今日",
            "data_date": today,
            "group_date": today.strftime("%m-%d"),
            "report_label": f"{today.strftime('%Y-%m-%d')}（盘中实时）",
        }

    # 盘前或盘后：使用上一个交易日
    last_trading_day = _prev_trading_day(today)
    label = "盘前" if time_minutes < 570 else "盘后"
    return {
        "is_open": False,
        "data_label": "昨日",
        "data_date": last_trading_day,
        "group_date": last_trading_day.strftime("%m-%d"),
        "report_label": f"{last_trading_day.strftime('%Y-%m-%d')}（{label}扫描）",
    }


def _is_trading_day(d) -> bool:
    """判断是否为交易日（周末排除）。"""
    try:
        from chinese_calendar import is_workday, is_holiday
        return is_workday(d) and not is_holiday(d)
    except ImportError:
        return d.weekday() < 5


def _prev_trading_day(d) -> "date":
    """获取上一个交易日。"""
    d = d - timedelta(days=1)
    for _ in range(10):
        if _is_trading_day(d):
            return d
        d -= timedelta(days=1)
    return d


# ── 涨停池抓取 ──

def fetch_limit_up_stocks() -> list[dict]:
    """抓取涨停股票列表。

    优先东方财富 clist 接口；不可用时降级到腾讯 API。
    """
    stocks = _fetch_from_eastmoney()
    if stocks is not None:
        return stocks
    print("[连板扫描] 东方财富不可用，降级到腾讯 API...", flush=True)
    return _fetch_from_tencent()


def _fetch_from_eastmoney() -> Optional[list[dict]]:
    """东方财富涨停池。失败返回 None 触发降级。"""
    all_stocks = []
    seen = set()
    for pn in range(1, 6):
        params = {
            "pn": pn, "pz": 100, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": LIMIT_UP_FIELDS,
        }
        try:
            resp = _get_session().get(EASTMONEY_CLIST_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[连板扫描] 东方财富失败(pn={pn}): {e}", flush=True)
            return None
        rows = data.get("data", {}).get("diff") or []
        if not rows:
            break
        for raw in rows:
            code = str(raw.get("f12", ""))
            if not code or code in seen:
                continue
            change_pct = _safe_float(raw.get("f3"))
            if change_pct < 9.5:
                continue
            seen.add(code)
            all_stocks.append({
                "code": code, "name": raw.get("f14", ""),
                "price": _safe_float(raw.get("f2")), "change_pct": change_pct,
                "market_cap_yi": _money_yi(raw.get("f20")),
                "free_market_cap_yi": _money_yi(raw.get("f21")),
                "amount_yi": _money_yi(raw.get("f6")),
                "turnover_rate": _safe_float(raw.get("f8")),
                "volume_ratio": _safe_float(raw.get("f10")),
                "main_net_yi": _money_yi(raw.get("f62")),
                "sector": raw.get("f100", ""), "pe": _safe_float(raw.get("f9")),
            })
    print(f"[连板扫描] 涨停池候选: {len(all_stocks)} 只", flush=True)
    return all_stocks


# ── 腾讯降级方案 ──

def _fetch_from_tencent() -> list[dict]:
    """腾讯 API 降级：批量查询全 A 股代码池，筛选涨幅 >= 9.5%。"""
    codes = _get_code_pool()
    if not codes:
        return []
    print(f"[连板扫描] 腾讯代码池: {len(codes)} 只", flush=True)
    all_stocks = []
    session = _get_session()
    for i in range(0, len(codes), 130):
        batch = codes[i:i + 130]
        tc_list = []
        for c in batch:
            if c.startswith("6"):
                tc_list.append(f"sh{c}")
            elif c.startswith(("0", "3")):
                tc_list.append(f"sz{c}")
        if not tc_list:
            continue
        try:
            resp = session.get(f"http://qt.gtimg.cn/q={','.join(tc_list)}", timeout=15)
            text = resp.text
            try:
                text = text.encode("latin-1").decode("gbk")
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
            for line in text.strip().split("\n"):
                if not line.strip() or "=" not in line:
                    continue
                try:
                    _, raw = line.split("=", 1)
                    parts = raw.strip('";').split("~")
                    if len(parts) < 46:
                        continue
                    code = parts[2]
                    change_pct = _safe_float(parts[32])
                    if change_pct < 9.5:
                        continue
                    amount_str = parts[37] if len(parts) > 37 else "0"
                    all_stocks.append({
                        "code": code, "name": parts[1],
                        "price": _safe_float(parts[3]), "change_pct": change_pct,
                        "market_cap_yi": _safe_float(parts[45]) if len(parts) > 45 else 0,
                        "free_market_cap_yi": 0,
                        "amount_yi": _safe_float(amount_str) / 10000 if amount_str else 0,
                        "turnover_rate": _safe_float(parts[38]) if len(parts) > 38 else 0,
                        "volume_ratio": 0, "main_net_yi": 0,
                        "sector": "", "pe": _safe_float(parts[39]) if len(parts) > 39 else 0,
                    })
                except (IndexError, ValueError):
                    continue
        except Exception as e:
            print(f"[连板扫描] 腾讯批次失败: {e}", flush=True)
    print(f"[连板扫描] 腾讯涨停池: {len(all_stocks)} 只", flush=True)
    return all_stocks


def _get_code_pool() -> list[str]:
    """获取全 A 股代码池。

    通过已知代码范围生成全量代码，用腾讯 API 批量验证。
    这比依赖第三方 API 更可靠。
    """
    codes = set()

    # 上证主板: 600000-601999, 603000-603999, 605000-605999
    codes.update(f"{i:06d}" for i in range(600000, 602000))
    codes.update(f"{i:06d}" for i in range(603000, 604000))
    codes.update(f"{i:06d}" for i in range(605000, 606000))
    # 科创板: 688000-689999
    codes.update(f"{i:06d}" for i in range(688000, 690000))
    # 深证主板: 000001-001999
    codes.update(f"{i:06d}" for i in range(1, 2000))
    # 中小板: 002000-004999
    codes.update(f"{i:06d}" for i in range(2000, 5000))
    # 创业板: 300000-301999
    codes.update(f"{i:06d}" for i in range(300000, 302000))

    return sorted(codes)


# ── 连板天数计算 ──

def _code_to_tencent(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return ""


def _fetch_kline(tc: str, code: str, days: int = 15, timeout: int = 10) -> list[dict]:
    """获取单只股票最近 N 天日 K 线（前复权）。"""
    try:
        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={tc},day,,,{days},qfq"
        )
        resp = _get_session().get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return []
        stock_data = (data.get("data") or {}).get(tc, {})
        return (stock_data.get("qfqday") or []) or (stock_data.get("day") or [])
    except Exception as e:
        print(f"[连板K线] {code} 获取失败: {e}", flush=True)
        return []


def _calc_limit_up_price(prev_close: float, code: str) -> float:
    code = str(code).zfill(6)
    if code.startswith("688") or code.startswith("30"):
        rate = 0.20
    elif code.startswith(("8", "4")):
        rate = 0.30
    else:
        rate = 0.10
    return round(prev_close * (1 + rate), 2)


def _count_consecutive_limit_up(klines: list[dict], code: str) -> int:
    """从 K 线数据计算连板天数（从最新一天往回数）。"""
    if len(klines) < 2:
        return 0
    count = 0
    for i in range(len(klines) - 1, 0, -1):
        today_k = klines[i]
        yesterday_k = klines[i - 1]
        if len(today_k) < 3 or len(yesterday_k) < 3:
            continue
        close = float(today_k[2])
        prev_close = float(yesterday_k[2])
        limit_price = _calc_limit_up_price(prev_close, code)
        if abs(close - limit_price) < 0.02:
            count += 1
        else:
            break
    return count


def _calc_one_consecutive(stock: dict, timeout: int = 10) -> dict:
    code = stock["code"]
    tc = _code_to_tencent(code)
    if not tc:
        stock["consecutive_days"] = 0
        return stock
    klines = _fetch_kline(tc, code, days=15, timeout=timeout)
    stock["consecutive_days"] = _count_consecutive_limit_up(klines, code)
    return stock


def calc_consecutive_days(stocks: list[dict], max_workers: int = 8) -> list[dict]:
    """并发计算连板天数，过滤 >= 2 天。"""
    if not stocks:
        return []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(stocks))) as executor:
        futures = {executor.submit(_calc_one_consecutive, s): s for s in stocks}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                s = futures[future]
                s["consecutive_days"] = 0
                print(f"[连板K线] {s['code']} 异常: {e}", flush=True)
    consecutive = [s for s in stocks if s.get("consecutive_days", 0) >= 2]
    consecutive.sort(key=lambda s: (-s["consecutive_days"], -s["amount_yi"]))
    print(f"[连板扫描] 连板 >= 2 天: {len(consecutive)} 只", flush=True)
    return consecutive


# ── AI 分类分组 ──

def classify_and_summarize(stocks: list[dict], date_label: str = "今日") -> str:
    """AI 分类分组 + 原因总结。"""
    if not stocks:
        return "无连板股票。"
    try:
        from summarizer import get_client
        client, model, provider = get_client()
        print(f"[连板AI] 后端: {provider} ({model})", flush=True)
    except Exception as e:
        print(f"[连板AI] 初始化失败: {e}", flush=True)
        return _fallback_classify(stocks)

    compact = [{
        "code": s["code"], "name": s["name"],
        "consecutive_days": s["consecutive_days"],
        "change_pct": s["change_pct"], "amount_yi": s["amount_yi"],
        "turnover_rate": s["turnover_rate"],
        "main_net_yi": s["main_net_yi"],
        "market_cap_yi": s["market_cap_yi"],
        "sector": s.get("sector", ""),
    } for s in stocks]

    system = (
        "你是A股短线复盘分析师，擅长连板股题材分类和涨停原因分析。"
        "基于提供的涨停数据和板块信息进行分析。"
        "不要编造未给出的信息；金额单位为亿元；不确定的用「可能」「或」表达。"
    )
    prompt = f"""请对以下连板（连续涨停 >= 2 天）股票进行分类分组，并总结每组的连板原因。

数据（{date_label}）：
{json.dumps(compact, ensure_ascii=False, indent=2)}

请输出：
1. **总览**：{date_label}连板概况（总只数、最高连板天数、连板梯队分布）
2. **题材分组**：按题材/板块将连板股分组，每组给出：
   - 组名（如"AI算力"、"机器人"、"新能源"等）
   - 组内股票列表（代码、名称、连板天数、涨停原因简述）
   - 该题材的整体连板逻辑（一句话总结）
3. **梯队梳理**：按连板天数分为高位板（>=5天）、中位板（3-4天）、低位板（2天）
4. **明日关注**：需要重点跟踪的连板股及理由

硬性要求：
- 金额单位为亿元。
- 引用具体数字时必须与输入数据完全一致。
- 不要虚构新闻、政策或消息。
"""
    return client.create(system=system, prompt=prompt, max_tokens=4000)


def _fallback_classify(stocks: list[dict]) -> str:
    groups = defaultdict(list)
    for s in stocks:
        groups[s.get("sector", "") or "其他"].append(s)
    lines = ["## 连板股票分组（规则分类兜底）\n"]
    for sector, group in sorted(groups.items(), key=lambda x: -len(x[1])):
        lines.append(f"### {sector}（{len(group)} 只）\n")
        for s in group:
            lines.append(
                f"- {s['name']}({s['code']}): {s['consecutive_days']}连板，"
                f"涨幅 {s['change_pct']:.2f}%，成交 {s['amount_yi']:.2f} 亿"
            )
        lines.append("")
    lines.append("\n> 注：AI 服务不可用，此为基于板块字段的规则分类。")
    return "\n".join(lines)


# ── 报告生成 ──

def build_report(stocks: list[dict], ai_text: str = "", market: dict = None) -> str:
    market = market or get_market_status()
    now = _now_shanghai()
    time_str = now.strftime("%Y-%m-%d %H:%M:%S 北京时间")
    data_label = market["data_label"]
    report_label = market["report_label"]

    lines = [
        f"# A股连板股票扫描报告",
        "",
        f"> 生成时间: {time_str}",
        f"> 数据日期: {report_label}",
        f"> 数据源: 东方财富实时行情 + 腾讯日K线",
        "",
    ]

    if not stocks:
        lines.append(f"{data_label}无连板（>= 2 天）股票。\n")
        return "\n".join(lines)

    max_days = max(s["consecutive_days"] for s in stocks)
    total_amount = sum(s["amount_yi"] for s in stocks)
    lines.extend([
        "## 一、连板概况",
        "",
        f"- 数据日期: **{data_label}**",
        f"- 连板股总数: **{len(stocks)}** 只",
        f"- 最高连板: **{max_days}** 天",
        f"- 合计成交额: **{total_amount:.2f}** 亿",
        "",
    ])

    high = [s for s in stocks if s["consecutive_days"] >= 5]
    mid = [s for s in stocks if 3 <= s["consecutive_days"] <= 4]
    low = [s for s in stocks if s["consecutive_days"] == 2]
    lines.extend([
        f"- 高位板（>= 5天）: {len(high)} 只",
        f"- 中位板（3-4天）: {len(mid)} 只",
        f"- 低位板（2天）: {len(low)} 只",
        "",
    ])

    lines.extend([
        "## 二、连板股一览",
        "",
        "| 连板天数 | 股票 | 代码 | 涨幅 | 成交额(亿) | 换手率 | 主力净流入(亿) | 市值(亿) | 板块 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    for s in stocks:
        lines.append(
            f"| {s['consecutive_days']} | {s['name']} | {s['code']} | "
            f"{s['change_pct']:.2f}% | {s['amount_yi']:.2f} | "
            f"{s['turnover_rate']:.2f}% | {s['main_net_yi']:.2f} | "
            f"{s['market_cap_yi']:.2f} | {s.get('sector', '')} |"
        )
    lines.append("")

    if ai_text:
        lines.extend(["## 三、题材分组与连板原因", "", ai_text, ""])

    lines.extend([
        "---",
        "",
        "*说明：连板天数基于前复权日K线涨停价计算，连板 >= 2 天纳入统计。"
        "分组和原因分析由 AI 生成，仅供参考，不构成投资建议。*",
    ])
    return "\n".join(lines)


# ── 同步分组名 ──

def make_consecutive_group_name(market: dict = None) -> str:
    """生成连板分组名：连板-MM-DD。

    盘前/盘后使用上一交易日日期，盘中使用当日日期。
    """
    market = market or get_market_status()
    return f"连板-{market['group_date']}"


# ── 主入口 ──

def scan_consecutive_limit_up(with_ai: bool = True) -> tuple[str, list[dict]]:
    """扫描连板股票，返回 (报告文本, 连板股票列表)。"""
    market = get_market_status()
    print(f"[连板扫描] 市场状态: {'盘中' if market['is_open'] else '盘前/盘后'}，"
          f"数据日期: {market['data_label']}（{market['data_date']}）", flush=True)

    # 1. 抓取涨停池
    limit_up = fetch_limit_up_stocks()
    if not limit_up:
        print("[连板扫描] 未抓到涨停股票", flush=True)
        return build_report([], "", market), []

    # 2. 计算连板天数
    consecutive = calc_consecutive_days(limit_up)

    # 3. AI 分类分组
    ai_text = ""
    if with_ai and consecutive:
        ai_text = classify_and_summarize(consecutive, market["data_label"])

    # 4. 生成报告
    report = build_report(consecutive, ai_text, market)

    if consecutive:
        print(f"[连板扫描] 完成: {len(consecutive)} 只连板股，"
              f"最高 {consecutive[0]['consecutive_days']} 连板", flush=True)
    else:
        print("[连板扫描] 完成", flush=True)

    return report, consecutive
