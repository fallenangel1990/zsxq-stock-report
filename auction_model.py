"""开盘竞价量化选股模型。

基于集合竞价数据的多因子评分模型，从竞价角度筛选优质标的。

评分维度（总分 10 分）：
  1. 竞价量能 (0-2.0): 量比 + 竞价量占比
  2. 竞价涨幅 (0-2.0): 竞价涨幅（温和上涨最优）
  3. 竞价换手 (0-1.5): 竞价换手率
  4. 竞价趋势 (0-1.5): 竞价期间价格走势
  5. 竞价金额 (0-1.0): 竞价绝对金额
  6. 竞价匹配 (0-1.0): 竞价量/昨日总量（参与度）
  7. 板块共振 (0-1.0): 板块内竞价联动

买卖信号：
  - 买入信号：总分 >= 7.0，各项均达标
  - 观察信号：总分 5.0-7.0
  - 排除：总分 < 5.0 或触发否决项

否决项（任一触发即排除）：
  - ST / 退市风险
  - 竞价涨幅 > 5%（过高，追高风险）
  - 竞价涨幅 < -3%（弱势）
  - 竞价金额 < 500 万（流动性不足）
  - 市值 < 30 亿（小盘风险）
  - 无量空涨（量比 < 0.5 且涨幅 > 2%）
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from auction_fetcher import (
    fetch_auction_data,
    fetch_call_auction_trend,
    fetch_pre_market_volume_top,
    is_auction_time,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
AUCTION_DIR = DATA_DIR / "auction"
AUCTION_RESULT_FILE = AUCTION_DIR / "latest_auction_signals.json"
AUCTION_HISTORY_DIR = AUCTION_DIR / "history"

MAX_AUCTION_CHANGE_PCT = 5.0
MIN_AUCTION_CHANGE_PCT = -3.0
MIN_AUCTION_AMOUNT_YI = 0.05
MIN_MARKET_CAP_YI = 30.0
MIN_VOLUME_RATIO = 0.5

BLACKLIST = ["ST", "*ST", "退", "退市"]


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _ensure_dirs() -> None:
    AUCTION_DIR.mkdir(parents=True, exist_ok=True)
    AUCTION_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _check_veto(stock: dict) -> tuple:
    """否决过滤器。"""
    name = stock.get("name", "")
    for kw in BLACKLIST:
        if kw in name:
            return True, "黑名单(" + kw + ")"

    change_pct = stock.get("auction_change_pct") or stock.get("change_pct", 0)
    if change_pct > MAX_AUCTION_CHANGE_PCT:
        return True, "竞价涨幅过高({:.1f}%>{})".format(change_pct, MAX_AUCTION_CHANGE_PCT)
    if change_pct < MIN_AUCTION_CHANGE_PCT:
        return True, "竞价涨幅过低({:.1f}%<{})".format(change_pct, MIN_AUCTION_CHANGE_PCT)

    amount = stock.get("auction_amount", 0)
    amount_yi = amount / 1e8 if amount else 0
    if amount_yi < MIN_AUCTION_AMOUNT_YI:
        return True, "竞价金额不足({:.2f}亿<{}亿)".format(amount_yi, MIN_AUCTION_AMOUNT_YI)

    market_cap = stock.get("float_market_cap_yi") or stock.get("market_cap_yi", 0)
    if market_cap and market_cap < MIN_MARKET_CAP_YI:
        return True, "市值过小({:.0f}亿<{}亿)".format(market_cap, MIN_MARKET_CAP_YI)

    volume_ratio = stock.get("volume_ratio", 0)
    if volume_ratio and volume_ratio < MIN_VOLUME_RATIO and change_pct and change_pct > 2.0:
        return True, "无量空涨(量比{:.1f}, 涨幅{:.1f}%)".format(volume_ratio, change_pct)

    return False, ""


def _score_auction_volume(stock: dict) -> float:
    """竞价量能评分（0-2.0）。"""
    vr = stock.get("volume_ratio", 0)
    if not vr:
        return 0.0
    if vr >= 5.0:
        return 2.0
    if vr >= 3.0:
        return 1.5 + (vr - 3.0) / 2.0 * 0.5
    if vr >= 2.0:
        return 1.0 + (vr - 2.0) * 0.5
    if vr >= 1.5:
        return 0.5 + (vr - 1.5) * 1.0
    if vr >= 1.0:
        return (vr - 1.0) * 1.0
    return max(0, vr * 0.5)


def _score_auction_change(stock: dict) -> float:
    """竞价涨幅评分（0-2.0）。"""
    cp = stock.get("auction_change_pct") or stock.get("change_pct", 0)
    if cp is None:
        return 0.0
    if 1.0 <= cp <= 3.0:
        return 2.0
    if 0.0 <= cp < 1.0:
        return 1.0 + cp
    if 3.0 < cp <= 4.0:
        return 1.5 - (cp - 3.0) * 0.5
    if 4.0 < cp <= 5.0:
        return 0.5 - (cp - 4.0) * 0.5
    if -1.0 <= cp < 0.0:
        return 0.5 + cp * 0.3
    return 0.0


def _score_auction_turnover(stock: dict) -> float:
    """竞价换手率评分（0-1.5）。"""
    atr = stock.get("auction_turnover_rate", 0)
    if not atr:
        return 0.0
    if atr >= 2.0:
        return 1.5
    if atr >= 1.5:
        return 1.25 + (atr - 1.5) * 0.5
    if atr >= 1.0:
        return 0.75 + (atr - 1.0) * 0.5
    if atr >= 0.5:
        return 0.25 + (atr - 0.5) * 0.5
    return max(0, atr * 0.5)


def _score_auction_trend(stock: dict, trend_data: list) -> float:
    """竞价趋势评分（0-1.5）。"""
    if not trend_data or len(trend_data) < 3:
        return 0.5

    auction_points = [
        p for p in trend_data
        if "09:15" <= p.get("time", "") <= "09:25"
    ]
    if len(auction_points) < 2:
        return 0.5

    prices = [p.get("price", 0) for p in auction_points if p.get("price", 0) > 0]
    volumes = [p.get("volume", 0) for p in auction_points]

    if len(prices) < 2:
        return 0.5

    price_change = prices[-1] - prices[0]
    price_change_pct = (price_change / prices[0] * 100) if prices[0] > 0 else 0

    up_count = sum(1 for i in range(1, len(prices)) if prices[i] >= prices[i-1])
    consistency = up_count / (len(prices) - 1)

    if len(volumes) >= 2:
        early_vol = sum(volumes[:len(volumes)//2])
        late_vol = sum(volumes[len(volumes)//2:])
        volume_increase = (late_vol > early_vol * 1.1)
    else:
        volume_increase = False

    score = 0.0

    if price_change_pct > 0.5:
        score += 0.6 + min(0.2, price_change_pct * 0.05)
    elif price_change_pct > 0:
        score += 0.3 + price_change_pct * 0.3
    elif price_change_pct > -0.3:
        score += 0.15
    else:
        score += 0.05

    if consistency >= 0.8:
        score += 0.4
    elif consistency >= 0.6:
        score += 0.25
    elif consistency >= 0.4:
        score += 0.1

    if volume_increase and price_change_pct > 0:
        score += 0.3
    elif price_change_pct > 0:
        score += 0.1

    return min(1.5, score)


def _score_auction_amount(stock: dict) -> float:
    """竞价金额评分（0-1.0）。"""
    amount_yi = (stock.get("auction_amount", 0) or 0) / 1e8
    if amount_yi >= 2.0:
        return 1.0
    if amount_yi >= 1.0:
        return 0.7 + (amount_yi - 1.0) * 0.3
    if amount_yi >= 0.5:
        return 0.4 + (amount_yi - 0.5) * 0.6
    return max(0, amount_yi / 0.5 * 0.4)


def _score_auction_participation(stock: dict) -> float:
    """竞价参与度评分（0-1.0）。"""
    ratio = stock.get("auction_volume_ratio", 0)
    if not ratio:
        return 0.0
    if ratio >= 30:
        return 1.0
    if ratio >= 20:
        return 0.7 + (ratio - 20) / 10 * 0.3
    if ratio >= 10:
        return 0.4 + (ratio - 10) / 10 * 0.3
    return max(0, ratio / 10 * 0.4)


def _score_sector_resonance(stock: dict, sector_auctions: dict) -> float:
    """板块共振评分（0-1.0）。"""
    sector = stock.get("sector", "")
    if not sector or sector not in sector_auctions:
        return 0.3
    sector_stocks = sector_auctions[sector]
    if len(sector_stocks) <= 1:
        return 0.3
    resonant_count = len([s for s in sector_stocks if s.get("code") != stock.get("code")])
    if resonant_count >= 5:
        return 1.0
    if resonant_count >= 3:
        return 0.8
    if resonant_count >= 2:
        return 0.6
    return 0.4


def score_auction_stock(stock: dict, trend_data: Optional[list] = None,
                        sector_auctions: Optional[dict] = None) -> dict:
    """对单只股票进行竞价评分。"""
    is_vetoed, veto_reason = _check_veto(stock)

    volume_score = _score_auction_volume(stock)
    change_score = _score_auction_change(stock)
    turnover_score = _score_auction_turnover(stock)
    trend_score = _score_auction_trend(stock, trend_data or [])
    amount_score = _score_auction_amount(stock)
    participation_score = _score_auction_participation(stock)
    sector_score = _score_sector_resonance(stock, sector_auctions or {})

    total_score = round(
        volume_score + change_score + turnover_score + trend_score +
        amount_score + participation_score + sector_score, 2
    )

    if is_vetoed:
        total_score = min(total_score, 4.0)

    if is_vetoed:
        signal = "排除"
    elif total_score >= 7.0:
        signal = "强烈买入"
    elif total_score >= 5.5:
        signal = "买入关注"
    elif total_score >= 4.0:
        signal = "观察"
    else:
        signal = "排除"

    return {
        "code": stock.get("code", ""),
        "name": stock.get("name", ""),
        "total_score": total_score,
        "signal": signal,
        "is_vetoed": is_vetoed,
        "veto_reason": veto_reason,
        "scores": {
            "volume": round(volume_score, 2),
            "change": round(change_score, 2),
            "turnover": round(turnover_score, 2),
            "trend": round(trend_score, 2),
            "amount": round(amount_score, 2),
            "participation": round(participation_score, 2),
            "sector": round(sector_score, 2),
        },
        "raw_data": {
            "auction_volume": stock.get("auction_volume"),
            "auction_amount_yi": round((stock.get("auction_amount", 0) or 0) / 1e8, 2),
            "auction_change_pct": stock.get("auction_change_pct") or stock.get("change_pct"),
            "volume_ratio": stock.get("volume_ratio"),
            "auction_turnover_rate": stock.get("auction_turnover_rate"),
            "float_market_cap_yi": stock.get("float_market_cap_yi"),
        },
    }


def select_auction_stocks(stocks: list, with_trend: bool = False,
                          sector_map: Optional[dict] = None,
                          verbose: bool = True) -> list:
    """批量筛选竞价股票。"""
    results = []
    sector_auctions = {}

    if sector_map:
        for code, sector in sector_map.items():
            sector_auctions.setdefault(sector, []).append({"code": code})

    for i, stock in enumerate(stocks):
        code = stock.get("code", "")
        if not code:
            continue

        trend = None
        if with_trend:
            try:
                trend = fetch_call_auction_trend(code)
                time.sleep(0.1)
            except Exception:
                trend = None

        result = score_auction_stock(stock, trend, sector_auctions)
        results.append(result)

        if verbose:
            status = result["signal"]
            is_v = result.get("veto_reason", "")
            v_str = " [否决:" + is_v + "]" if is_v else ""
            print(
                "  [{}/{}] {}({}) 评分={:.1f} {}{}".format(
                    i + 1, len(stocks), result["name"], code,
                    result["total_score"], status, v_str
                ),
                flush=True,
            )

    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results


def build_auction_candidates(count: int = 50, verbose: bool = True) -> list:
    """从量比排行榜构建竞价候选池。"""
    if verbose:
        print("获取竞价量比排行榜 (前 {} 只)...".format(count), flush=True)

    stocks = fetch_pre_market_volume_top(count=count)
    if not stocks:
        if verbose:
            print("未获取到量比数据，可能不在竞价时间段", flush=True)
        return []

    if verbose:
        print("获取到 {} 只股票量比数据".format(len(stocks)), flush=True)
    return stocks


def run_auction_scan(candidate_count: int = 50, with_trend: bool = False,
                     min_score: float = 5.0, verbose: bool = True) -> dict:
    """运行一次完整的竞价扫描。"""
    if verbose:
        print("\n" + "=" * 60, flush=True)
        print("📊 开盘竞价选股 — {}".format(_now_shanghai().strftime("%Y-%m-%d %H:%M:%S")), flush=True)
        print("=" * 60, flush=True)

    candidates = build_auction_candidates(count=candidate_count, verbose=verbose)
    if not candidates:
        return {"error": "无候选数据", "results": []}

    if verbose:
        print("\n获取详细竞价数据 ({} 只)...".format(len(candidates)), flush=True)

    detailed = []
    for c in candidates:
        code = c.get("code", "")
        if not code:
            continue
        data = fetch_auction_data(code)
        if data:
            if c.get("volume_ratio"):
                data.setdefault("volume_ratio", c["volume_ratio"])
            detailed.append(data)
        time.sleep(0.08)

    if not detailed:
        return {"error": "无详细竞价数据", "results": []}

    if verbose:
        print("获取到 {} 只股票详细数据".format(len(detailed)), flush=True)

    if verbose:
        print("\n竞价评分...", flush=True)

    results = select_auction_stocks(detailed, with_trend=with_trend, verbose=verbose)

    buy_signals = [r for r in results if r["signal"] in ("强烈买入", "买入关注")]
    watch_signals = [r for r in results if r["signal"] == "观察"]
    excluded = [r for r in results if r["signal"] == "排除"]

    _ensure_dirs()
    output = {
        "scan_time": _now_shanghai().isoformat(),
        "candidate_count": len(candidates),
        "detailed_count": len(detailed),
        "results": results,
        "buy_signals": buy_signals,
        "watch_signals": watch_signals,
        "excluded": excluded,
    }
    AUCTION_RESULT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    hist_file = AUCTION_HISTORY_DIR / ("auction_" + _now_shanghai().strftime("%Y%m%d_%H%M%S") + ".json")
    hist_file.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if verbose:
        print("\n" + "=" * 60, flush=True)
        print("🎯 买入信号 ({} 只):".format(len(buy_signals)), flush=True)
        for r in buy_signals[:10]:
            scores = r.get("scores", {})
            print(
                "   ⭐ {}({}) 评分={:.1f} | 量能{:.1f} 涨幅{:.1f} 换手{:.1f} 趋势{:.1f}".format(
                    r["name"], r["code"], r["total_score"],
                    scores.get("volume", 0), scores.get("change", 0),
                    scores.get("turnover", 0), scores.get("trend", 0),
                ),
                flush=True,
            )

        print("\n👀 观察信号 ({} 只):".format(len(watch_signals)), flush=True)
        for r in watch_signals[:5]:
            print("   • {}({}) 评分={:.1f}".format(r["name"], r["code"], r["total_score"]), flush=True)

        print("\n❌ 排除 ({} 只):".format(len(excluded)), flush=True)
        for r in excluded[:5]:
            print("   ✗ {}({}) 评分={:.1f} [{}]".format(r["name"], r["code"], r["total_score"], r.get("veto_reason", "")), flush=True)

        print("\n结果已保存: {}".format(AUCTION_RESULT_FILE), flush=True)

    return output


def format_auction_report(results: list) -> str:
    """格式化为 Markdown 报告。"""
    buy = [r for r in results if r["signal"] in ("强烈买入", "买入关注")]
    watch = [r for r in results if r["signal"] == "观察"]

    lines = [
        "# 开盘竞价选股报告",
        "",
        "> 生成时间: " + _now_shanghai().strftime("%Y-%m-%d %H:%M:%S 北京时间"),
        "",
        "## 买入信号 ({} 只)".format(len(buy)),
        "",
    ]

    if buy:
        lines.extend([
            "| 排名 | 股票 | 代码 | 总分 | 量能 | 涨幅 | 换手 | 趋势 | 竞价涨幅 | 量比 | 竞价额(亿) |",
            "|------|------|------|------|------|------|------|------|----------|------|------------|",
        ])
        for i, r in enumerate(buy, 1):
            s = r.get("scores", {})
            rd = r.get("raw_data", {})
            lines.append(
                "| {} | {} | {} | {:.1f} | {:.1f} | {:.1f} | {:.1f} | {:.1f} | {} | {} | {} |".format(
                    i, r["name"], r["code"], r["total_score"],
                    s.get("volume", 0), s.get("change", 0),
                    s.get("turnover", 0), s.get("trend", 0),
                    rd.get("auction_change_pct", "-"), rd.get("volume_ratio", "-"),
                    rd.get("auction_amount_yi", "-"),
                )
            )
    else:
        lines.append("今日无买入信号。")

    lines.extend([
        "",
        "## 观察清单 ({} 只)".format(len(watch)),
        "",
    ])

    if watch:
        lines.extend([
            "| 排名 | 股票 | 代码 | 总分 | 信号 |",
            "|------|------|------|------|------|",
        ])
        for i, r in enumerate(watch, 1):
            lines.append("| {} | {} | {} | {:.1f} | {} |".format(i, r["name"], r["code"], r["total_score"], r["signal"]))
    else:
        lines.append("今日无观察信号。")

    lines.extend([
        "",
        "---",
        "*评分模型：量能(2.0) + 涨幅(2.0) + 换手(1.5) + 趋势(1.5) + 金额(1.0) + 参与度(1.0) + 板块共振(1.0) = 10.0*",
        "*买入条件：总分 >= 5.5，排除条件：ST/涨幅过高/金额不足/无量空涨*",
    ])

    return "\n".join(lines)
