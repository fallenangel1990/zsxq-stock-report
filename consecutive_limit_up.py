#!/usr/bin/env python3
"""A股连板股票扫描模块。

扫描当日涨停股票，计算连板天数，按题材/板块分类分组，
使用 AI 总结连板原因，输出报告。

数据源：东方财富实时行情 + 腾讯前复权日K线。
"""

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import yaml


# ── 东方财富 API ──

EASTMONEY_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"

# 涨停池字段
LIMIT_UP_FIELDS = (
    "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
    "f20,f21,f22,f23,f24,f25,f62,f100,f115,f152"
)


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


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


# ── 涨停判断 ──

def _is_limit_up(change_pct: float, code: str) -> bool:
    """判断股票是否涨停。

    主板/中小板（6/0/3 开头）涨停幅度约 10%；
    创业板（30 开头）和科创板（688 开头）约 20%；
    北交所（8/4 开头）约 30%。
    允许 0.2% 误差。
    """
    code = str(code).zfill(6)
    if code.startswith("688") or code.startswith("30"):
        return change_pct >= 19.5
    if code.startswith(("8", "4")):
        return change_pct >= 29.0
    return change_pct >= 9.5


def _code_to_tencent(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    if code.startswith(("8", "4")):
        return f"bj{code}"
    return ""


def _code_to_eastmoney(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith("6"):
        return f"1.{code}"
    if code.startswith(("0", "3")):
        return f"0.{code}"
    if code.startswith(("8", "4")):
        return f"0.{code}"
    return ""


# ── 东方财富涨停池 ──

def fetch_limit_up_stocks() -> list[dict]:
    """从东方财富抓取当日涨停股票列表。

    使用 clist 接口，按涨跌幅排序，筛选涨幅 >= 9.5% 的股票。
    """
    all_stocks = []
    seen = set()

    for pn in range(1, 6):
        params = {
            "pn": pn,
            "pz": 100,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": LIMIT_UP_FIELDS,
        }
        try:
            resp = requests.get(EASTMONEY_CLIST_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[连板扫描] 东方财富请求失败 (pn={pn}): {e}", flush=True)
            break

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
                "code": code,
                "name": raw.get("f14", ""),
                "price": _safe_float(raw.get("f2")),
                "change_pct": change_pct,
                "market_cap_yi": _money_yi(raw.get("f20")),
                "free_market_cap_yi": _money_yi(raw.get("f21")),
                "amount_yi": _money_yi(raw.get("f6")),
                "turnover_rate": _safe_float(raw.get("f8")),
                "volume_ratio": _safe_float(raw.get("f10")),
                "main_net_yi": _money_yi(raw.get("f62")),
                "sector": raw.get("f100", ""),
                "pe": _safe_float(raw.get("f9")),
            })

    print(f"[连板扫描] 涨停池候选: {len(all_stocks)} 只", flush=True)
    return all_stocks


# ── 连板天数计算 ──

def _fetch_kline(tc: str, code: str, days: int = 15, timeout: int = 10) -> list[dict]:
    """获取单只股票最近 N 天日 K 线（前复权）。"""
    try:
        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={tc},day,,,{days},qfq"
        )
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return []
        stock_data = (data.get("data") or {}).get(tc, {})
        klines = (stock_data.get("qfqday") or []) or (stock_data.get("day") or [])
        return klines
    except Exception as e:
        print(f"[连板K线] {code} 获取失败: {e}", flush=True)
        return []


def _calc_limit_up_price(prev_close: float, code: str) -> float:
    """根据前收盘价计算涨停价。"""
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
        today = klines[i]
        yesterday = klines[i - 1]
        if len(today) < 3 or len(yesterday) < 3:
            continue
        close = float(today[2])
        prev_close = float(yesterday[2])
        limit_price = _calc_limit_up_price(prev_close, code)
        if abs(close - limit_price) < 0.02:
            count += 1
        else:
            break
    return count


def _calc_one_consecutive(stock: dict, timeout: int = 10) -> dict:
    """计算单只股票的连板天数。"""
    code = stock["code"]
    tc = _code_to_tencent(code)
    if not tc:
        stock["consecutive_days"] = 0
        return stock

    klines = _fetch_kline(tc, code, days=15, timeout=timeout)
    days = _count_consecutive_limit_up(klines, code)
    stock["consecutive_days"] = days
    return stock


def calc_consecutive_days(stocks: list[dict], max_workers: int = 8) -> list[dict]:
    """并发计算所有涨停股的连板天数，过滤出连板 >= 2 的股票。"""
    if not stocks:
        return []

    with ThreadPoolExecutor(max_workers=min(max_workers, len(stocks))) as executor:
        futures = {
            executor.submit(_calc_one_consecutive, s): s
            for s in stocks
        }
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


# ── AI 分类分组 + 原因总结 ──

def classify_and_summarize(stocks: list[dict]) -> str:
    """使用 AI 对连板股票进行分类分组并总结连板原因。

    Returns:
        AI 生成的分类总结文本（Markdown）。
    """
    if not stocks:
        return "无连板股票。"

    try:
        from summarizer import get_client
        client, model, provider = get_client()
        print(f"[连板AI] 后端: {provider} ({model})", flush=True)
    except Exception as e:
        print(f"[连板AI] 初始化失败: {e}", flush=True)
        return _fallback_classify(stocks)

    compact = []
    for s in stocks:
        compact.append({
            "code": s["code"],
            "name": s["name"],
            "consecutive_days": s["consecutive_days"],
            "change_pct": s["change_pct"],
            "amount_yi": s["amount_yi"],
            "turnover_rate": s["turnover_rate"],
            "main_net_yi": s["main_net_yi"],
            "market_cap_yi": s["market_cap_yi"],
            "sector": s.get("sector", ""),
        })

    system = (
        "你是A股短线复盘分析师，擅长连板股题材分类和涨停原因分析。"
        "基于提供的涨停数据和板块信息进行分析。"
        "不要编造未给出的信息；金额单位为亿元；不确定的用「可能」「或」表达。"
    )
    prompt = f"""请对以下连板（连续涨停 >= 2 天）股票进行分类分组，并总结每组的连板原因。

数据：
{json.dumps(compact, ensure_ascii=False, indent=2)}

请输出：
1. **总览**：今日连板概况（总只数、最高连板天数、连板梯队分布）
2. **题材分组**：按题材/板块将连板股分组，每组给出：
   - 组名（如"AI算力"、"机器人"、"新能源"等）
   - 组内股票列表（代码、名称、连板天数、涨停原因简述）
   - 该题材的整体连板逻辑（一句话总结）
3. **梯队梳理**：按连板天数分为高位板（>=5天）、中位板（3-4天）、低位板（2天），分析各梯队特征
4. **明日关注**：需要重点跟踪的连板股及理由

硬性要求：
- 金额单位为亿元。
- 引用具体数字时必须与输入数据完全一致。
- 不要虚构新闻、政策或消息。
- 涨停原因以数据中的 sector 字段和常识性产业逻辑为依据。
"""
    return client.create(system=system, prompt=prompt, max_tokens=4000)


def _fallback_classify(stocks: list[dict]) -> str:
    """AI 不可用时的规则分类兜底。"""
    from collections import defaultdict
    groups = defaultdict(list)
    for s in stocks:
        sector = s.get("sector", "") or "其他"
        groups[sector].append(s)

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

def build_report(stocks: list[dict], ai_text: str = "") -> str:
    """生成连板股票扫描报告（Markdown 格式）。"""
    now = _now_shanghai()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%Y-%m-%d %H:%M:%S 北京时间")

    lines = [
        f"# A股连板股票扫描报告",
        "",
        f"> 生成时间: {time_str}",
        f"> 数据源: 东方财富实时行情 + 腾讯日K线",
        "",
    ]

    if not stocks:
        lines.append("今日无连板（>= 2 天）股票。\n")
        return "\n".join(lines)

    # 总览
    max_days = max(s["consecutive_days"] for s in stocks)
    total_amount = sum(s["amount_yi"] for s in stocks)
    lines.extend([
        "## 一、连板概况",
        "",
        f"- 连板股总数: **{len(stocks)}** 只",
        f"- 最高连板: **{max_days}** 天",
        f"- 合计成交额: **{total_amount:.2f}** 亿",
        "",
    ])

    # 梯队分布
    high = [s for s in stocks if s["consecutive_days"] >= 5]
    mid = [s for s in stocks if 3 <= s["consecutive_days"] <= 4]
    low = [s for s in stocks if s["consecutive_days"] == 2]
    lines.extend([
        f"- 高位板（>= 5天）: {len(high)} 只",
        f"- 中位板（3-4天）: {len(mid)} 只",
        f"- 低位板（2天）: {len(low)} 只",
        "",
    ])

    # 全部连板股一览表
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

    # AI 分类分组
    if ai_text:
        lines.extend([
            "## 三、题材分组与连板原因",
            "",
            ai_text,
            "",
        ])

    lines.extend([
        "---",
        "",
        "*说明：连板天数基于前复权日K线涨停价计算，连板 >= 2 天纳入统计。"
        "分组和原因分析由 AI 生成，仅供参考，不构成投资建议。*",
    ])

    return "\n".join(lines)


# ── 同步分组名 ──

def make_consecutive_group_name() -> str:
    """生成连板分组名：连板-MM-DD。"""
    date_part = _now_shanghai().strftime("%m-%d")
    return f"连板-{date_part}"


# ── 主入口 ──

def scan_consecutive_limit_up(with_ai: bool = True) -> tuple[str, list[dict]]:
    """扫描连板股票，返回 (报告文本, 连板股票列表)。

    Args:
        with_ai: 是否使用 AI 进行分类分组。

    Returns:
        (report, stocks) 元组。
    """
    print("[连板扫描] 开始扫描...", flush=True)

    # 1. 抓取涨停池
    limit_up = fetch_limit_up_stocks()
    if not limit_up:
        print("[连板扫描] 未抓到涨停股票", flush=True)
        report = build_report([], "")
        return report, []

    # 2. 计算连板天数
    consecutive = calc_consecutive_days(limit_up)

    # 3. AI 分类分组
    ai_text = ""
    if with_ai and consecutive:
        ai_text = classify_and_summarize(consecutive)

    # 4. 生成报告
    report = build_report(consecutive, ai_text)

    print(
        f"[连板扫描] 完成: {len(consecutive)} 只连板股，"
        f"最高 {consecutive[0]['consecutive_days']} 连板" if consecutive else "[连板扫描] 完成",
        flush=True,
    )
    return report, consecutive
