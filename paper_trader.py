"""Paper Trading 模拟交易框架。

记录虚拟交易、计算虚拟组合净值、与实际推荐回测交叉验证。
支持多种入场/出场策略、滑点模拟、佣金扣除。
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from price_fetcher import fetch_prices, fetch_single_price


DATA_DIR = Path(__file__).parent / "data"
PAPER_DIR = DATA_DIR / "paper_trading"
PORTFOLIO_FILE = PAPER_DIR / "portfolio.json"
TRADES_FILE = PAPER_DIR / "trades.jsonl"
NAV_HISTORY_FILE = PAPER_DIR / "nav_history.json"

# 默认交易参数
DEFAULT_COMMISSION = 0.0003  # 佣金 0.03%（单边）
DEFAULT_STAMP_TAX = 0.0005  # 印花税 0.05%（卖出）
DEFAULT_SLIPPAGE = 0.001  # 滑点 0.1%


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _load_portfolio() -> dict:
    """加载当前模拟持仓。"""
    if PORTFOLIO_FILE.exists():
        try:
            return json.loads(PORTFOLIO_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "cash": 1_000_000,
        "positions": {},
        "initial_capital": 1_000_000,
        "created_at": _now_shanghai().isoformat(),
    }


def _save_portfolio(portfolio: dict) -> None:
    """保存模拟持仓。"""
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_FILE.write_text(
        json.dumps(portfolio, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _append_trade(trade: dict) -> None:
    """追加一条交易记录。"""
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRADES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(trade, ensure_ascii=False) + "\n")


def _load_trades() -> list[dict]:
    """加载所有交易记录。"""
    if not TRADES_FILE.exists():
        return []
    trades = []
    with open(TRADES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return trades


def _load_nav_history() -> list[dict]:
    """加载净值历史。"""
    if NAV_HISTORY_FILE.exists():
        try:
            return json.loads(NAV_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_nav_history(history: list[dict]) -> None:
    """保存净值历史。"""
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    NAV_HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def calculate_transaction_cost(
    price: float,
    shares: int,
    side: str = "buy",
    commission: float = DEFAULT_COMMISSION,
    stamp_tax: float = DEFAULT_STAMP_TAX,
    slippage: float = DEFAULT_SLIPPAGE,
    avg_daily_volume: float = 0,
    market_cap_yi: float = 0,
) -> dict:
    """计算交易成本（含市场冲击成本）。

    包含：
    - 佣金（最低 5 元，单边 0.025%）
    - 印花税（卖出 0.05%）
    - 固定滑点（默认 0.1%）
    - 市场冲击成本（大单对价格的冲击，与参与率成正比）

    Args:
        price: 交易价格。
        shares: 交易股数。
        side: "buy" 或 "sell"。
        commission: 佣金率。
        stamp_tax: 印花税率。
        slippage: 固定滑点率。
        avg_daily_volume: 日均成交量（股），用于计算市场冲击。
        market_cap_yi: 市值（亿），用于估算冲击系数。

    Returns:
        {"commission": float, "stamp_tax": float, "slippage_cost": float,
         "market_impact": float, "total": float, "cost_pct": float}
    """
    amount = price * shares
    comm = max(5, amount * commission)  # 最低 5 元
    tax = amount * stamp_tax if side == "sell" else 0
    slip = amount * slippage

    # 市场冲击成本：基于参与率的平方根模型
    # impact = sigma * sqrt(participation_rate)
    market_impact = 0.0
    if avg_daily_volume > 0 and shares > 0:
        participation = shares / avg_daily_volume
        # 波动率估算：小市值股票冲击更大
        base_vol = 0.02  # 基础日波动 2%
        if market_cap_yi > 0 and market_cap_yi < 50:
            base_vol *= 1.5  # 小市值冲击更大
        # 平方根冲击模型
        market_impact = amount * base_vol * (participation ** 0.5) * 0.5

    total = comm + tax + slip + market_impact
    cost_pct = (total / amount * 100) if amount > 0 else 0

    return {
        "commission": round(comm, 2),
        "stamp_tax": round(tax, 2),
        "slippage_cost": round(slip, 2),
        "market_impact": round(market_impact, 2),
        "total": round(total, 2),
        "cost_pct": round(cost_pct, 4),
    }


def buy_stock(
    code: str,
    name: str,
    target_pct: float,
    price: Optional[float] = None,
    score: float = 0,
    reason: str = "",
) -> dict:
    """模拟买入（含风控熔断检查）。

    Args:
        code: 股票代码。
        name: 股票名称。
        target_pct: 目标仓位占比（0-100）。
        price: 指定价格（None 则取实时价）。
        score: 推荐评分。
        reason: 买入原因。

    Returns:
        {"success": bool, "message": str, "trade": dict}
    """
    portfolio = _load_portfolio()

    # 风控熔断检查
    breaker = check_circuit_breakers(portfolio)
    if breaker["actions"]["block_new_buy"]:
        return {
            "success": False,
            "message": f"风控熔断（{breaker['level']}）：暂停新开仓。{'；'.join(breaker['messages'])}",
        }

    if code in portfolio["positions"]:
        return {"success": False, "message": f"{name}({code}) 已在持仓中"}

    if price is None:
        price_info = fetch_single_price(code)
        if not price_info or not price_info.get("price"):
            return {"success": False, "message": f"{name}({code}) 无法获取实时价格"}
        price = price_info["price"]

    # 计算买入金额和股数（A 股最少 100 股）
    total_value = _calculate_total_value(portfolio)
    target_amount = total_value * target_pct / 100
    shares = int(target_amount / price / 100) * 100
    if shares < 100:
        return {"success": False, "message": f"资金不足，无法买入至少 100 股 {name}"}

    # 获取成交量和市值数据用于计算市场冲击
    avg_volume = 0
    market_cap = 0
    try:
        from price_fetcher import fetch_single_price
        price_data = fetch_single_price(code)
        if price_data:
            avg_volume = price_data.get("volume", 0) or 0
            market_cap = price_data.get("market_cap_yi", 0) or 0
    except Exception:
        pass

    cost = calculate_transaction_cost(
        price, shares, "buy",
        avg_daily_volume=avg_volume,
        market_cap_yi=market_cap,
    )
    total_cost = price * shares + cost["total"]

    if total_cost > portfolio["cash"]:
        # 减少股数
        shares = int(portfolio["cash"] / price / 100) * 100
        if shares < 100:
            return {"success": False, "message": f"现金不足（{portfolio['cash']:.0f}元）"}
        cost = calculate_transaction_cost(
            price, shares, "buy",
            avg_daily_volume=avg_volume,
            market_cap_yi=market_cap,
        )
        total_cost = price * shares + cost["total"]

    # 执行买入
    portfolio["cash"] -= total_cost
    portfolio["positions"][code] = {
        "name": name,
        "shares": shares,
        "avg_cost": price,
        "buy_price": price,
        "buy_date": _now_shanghai().isoformat(),
        "score": score,
        "reason": reason,
        "cost": cost["total"],
    }
    _save_portfolio(portfolio)

    trade = {
        "action": "buy",
        "code": code,
        "name": name,
        "price": price,
        "shares": shares,
        "amount": round(price * shares, 2),
        "cost": cost,
        "score": score,
        "reason": reason,
        "timestamp": _now_shanghai().isoformat(),
    }
    _append_trade(trade)

    return {
        "success": True,
        "message": f"买入 {name}({code}) {shares}股 @ {price:.2f}元，成本 {cost['total']:.2f}元",
        "trade": trade,
    }


def sell_stock(
    code: str,
    price: Optional[float] = None,
    shares: Optional[int] = None,
    reason: str = "",
) -> dict:
    """模拟卖出。

    Args:
        code: 股票代码。
        price: 指定价格（None 则取实时价）。
        shares: 卖出股数（None 则全部卖出）。
        reason: 卖出原因。

    Returns:
        {"success": bool, "message": str, "trade": dict}
    """
    portfolio = _load_portfolio()

    if code not in portfolio["positions"]:
        return {"success": False, "message": f"{code} 不在持仓中"}

    pos = portfolio["positions"][code]
    if price is None:
        price_info = fetch_single_price(code)
        if not price_info or not price_info.get("price"):
            return {"success": False, "message": f"无法获取 {code} 实时价格"}
        price = price_info["price"]

    if shares is None:
        shares = pos["shares"]
    shares = min(shares, pos["shares"])

    # 获取成交量和市值数据
    avg_volume = 0
    market_cap = 0
    try:
        from price_fetcher import fetch_single_price
        price_data = fetch_single_price(code)
        if price_data:
            avg_volume = price_data.get("volume", 0) or 0
            market_cap = price_data.get("market_cap_yi", 0) or 0
    except Exception:
        pass

    cost = calculate_transaction_cost(
        price, shares, "sell",
        avg_daily_volume=avg_volume,
        market_cap_yi=market_cap,
    )
    proceeds = price * shares - cost["total"]

    # 计算盈亏
    pnl = (price - pos["avg_cost"]) * shares - cost["total"] - pos.get("cost", 0) * shares / pos["shares"]
    pnl_pct = (price / pos["avg_cost"] - 1) * 100 if pos["avg_cost"] > 0 else 0

    portfolio["cash"] += proceeds

    if shares >= pos["shares"]:
        del portfolio["positions"][code]
    else:
        pos["shares"] -= shares

    _save_portfolio(portfolio)

    trade = {
        "action": "sell",
        "code": code,
        "name": pos["name"],
        "price": price,
        "shares": shares,
        "amount": round(price * shares, 2),
        "cost": cost,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "reason": reason,
        "hold_days": (_now_shanghai() - datetime.fromisoformat(pos["buy_date"])).days,
        "timestamp": _now_shanghai().isoformat(),
    }
    _append_trade(trade)

    return {
        "success": True,
        "message": f"卖出 {pos['name']}({code}) {shares}股 @ {price:.2f}元，盈亏 {pnl:+.2f}元({pnl_pct:+.1f}%)",
        "trade": trade,
    }


def _calculate_total_value(portfolio: dict) -> float:
    """计算组合总市值（现金 + 持仓市值）。"""
    total = portfolio["cash"]
    codes = list(portfolio["positions"].keys())
    if codes:
        prices = fetch_prices(codes)
        for code, pos in portfolio["positions"].items():
            price_info = prices.get(code)
            if price_info and price_info.get("price"):
                total += price_info["price"] * pos["shares"]
            else:
                total += pos["avg_cost"] * pos["shares"]
    return total


def record_nav() -> dict:
    """记录当前净值到历史。"""
    portfolio = _load_portfolio()
    total_value = _calculate_total_value(portfolio)
    nav = total_value / portfolio["initial_capital"]

    nav_point = {
        "date": _now_shanghai().strftime("%Y-%m-%d"),
        "timestamp": _now_shanghai().isoformat(),
        "total_value": round(total_value, 2),
        "nav": round(nav, 4),
        "cash": round(portfolio["cash"], 2),
        "position_count": len(portfolio["positions"]),
    }

    history = _load_nav_history()
    # 去重（同一天只保留最新）
    history = [h for h in history if h["date"] != nav_point["date"]]
    history.append(nav_point)
    history.sort(key=lambda x: x["date"])
    _save_nav_history(history)

    return nav_point


def check_circuit_breakers(portfolio: dict = None) -> dict:
    """检查风控熔断条件。

    熔断规则：
    - 单日亏损 > 2%：暂停新开仓
    - 单只股票亏损 > 8%：强制止损信号
    - 组合回撤 > 10%：减半仓位
    - 组合回撤 > 15%：全部清仓

    Returns:
        {
            "circuit_breaker": bool,
            "level": "none" | "caution" | "half" | "liquidate",
            "messages": [str],
            "actions": {
                "block_new_buy": bool,
                "force_reduce": bool,
                "force_liquidate": bool,
                "stop_loss_codes": [code],
            }
        }
    """
    if portfolio is None:
        portfolio = _load_portfolio()

    initial = portfolio.get("initial_capital", 1_000_000)
    cash = portfolio.get("cash", 0)
    positions = portfolio.get("positions", {})

    # 计算组合净值
    total_value = _calculate_total_value(portfolio)
    nav = total_value / initial if initial > 0 else 1.0
    max_nav = nav

    # 获取历史最高净值
    nav_history = _load_nav_history()
    if nav_history:
        max_nav = max(max_nav, max(h.get("nav", 1.0) for h in nav_history))

    # 最大回撤
    max_drawdown = (1 - nav / max_nav) * 100 if max_nav > 0 else 0

    # 单日亏损
    daily_loss_pct = 0
    if nav_history and len(nav_history) >= 2:
        prev_nav = nav_history[-1].get("nav", 1.0)
        daily_loss_pct = (1 - nav / prev_nav) * 100 if prev_nav > 0 else 0

    messages = []
    actions = {
        "block_new_buy": False,
        "force_reduce": False,
        "force_liquidate": False,
        "stop_loss_codes": [],
    }
    level = "none"

    # 检查个股止损
    codes = list(positions.keys())
    prices = fetch_prices(codes) if codes else {}
    for code, pos in positions.items():
        price_info = prices.get(code)
        current_price = price_info["price"] if price_info else None
        if current_price and pos.get("avg_cost", 0) > 0:
            pnl_pct = (current_price / pos["avg_cost"] - 1) * 100
            if pnl_pct < -8:
                actions["stop_loss_codes"].append(code)
                messages.append(f"⚠️ {pos.get('name', code)} 亏损 {pnl_pct:.1f}%，触发止损")

    # 组合级熔断
    if max_drawdown > 15:
        level = "liquidate"
        actions["force_liquidate"] = True
        actions["block_new_buy"] = True
        messages.append(f"🔴 组合回撤 {max_drawdown:.1f}% 超过 15%，全部清仓")
    elif max_drawdown > 10:
        level = "half"
        actions["force_reduce"] = True
        actions["block_new_buy"] = True
        messages.append(f"🟠 组合回撤 {max_drawdown:.1f}% 超过 10%，减半仓位")
    elif daily_loss_pct > 2:
        level = "caution"
        actions["block_new_buy"] = True
        messages.append(f"🟡 单日亏损 {daily_loss_pct:.1f}%，暂停新开仓")

    circuit_breaker = level != "none"

    return {
        "circuit_breaker": circuit_breaker,
        "level": level,
        "messages": messages,
        "actions": actions,
        "metrics": {
            "nav": round(nav, 4),
            "max_nav": round(max_nav, 4),
            "max_drawdown_pct": round(max_drawdown, 2),
            "daily_loss_pct": round(daily_loss_pct, 2),
            "total_value": round(total_value, 2),
            "cash_pct": round(cash / total_value * 100, 1) if total_value > 0 else 0,
        },
    }


def get_portfolio_summary() -> dict:
    """获取当前组合摘要（含风控状态）。"""
    portfolio = _load_portfolio()
    total_value = _calculate_total_value(portfolio)
    nav = total_value / portfolio["initial_capital"]
    total_pnl = total_value - portfolio["initial_capital"]
    total_pnl_pct = (nav - 1) * 100

    # 风控状态
    breaker = check_circuit_breakers(portfolio)

    # 获取实时价格
    codes = list(portfolio["positions"].keys())
    prices = fetch_prices(codes) if codes else {}

    holdings = []
    for code, pos in portfolio["positions"].items():
        price_info = prices.get(code)
        current_price = price_info["price"] if price_info else pos["avg_cost"]
        market_value = current_price * pos["shares"]
        unrealized_pnl = (current_price - pos["avg_cost"]) * pos["shares"]
        unrealized_pct = (current_price / pos["avg_cost"] - 1) * 100 if pos["avg_cost"] > 0 else 0

        holdings.append({
            "code": code,
            "name": pos["name"],
            "shares": pos["shares"],
            "avg_cost": pos["avg_cost"],
            "current_price": current_price,
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pct": round(unrealized_pct, 2),
            "buy_date": pos["buy_date"],
            "score": pos.get("score", 0),
            "weight": round(market_value / total_value * 100, 1) if total_value > 0 else 0,
        })

    # 交易统计
    trades = _load_trades()
    buy_trades = [t for t in trades if t["action"] == "buy"]
    sell_trades = [t for t in trades if t["action"] == "sell"]
    win_trades = [t for t in sell_trades if t.get("pnl", 0) > 0]

    return {
        "total_value": round(total_value, 2),
        "nav": round(nav, 4),
        "cash": round(portfolio["cash"], 2),
        "cash_pct": round(portfolio["cash"] / total_value * 100, 1) if total_value > 0 else 100,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "holdings": holdings,
        "total_trades": len(trades),
        "total_buys": len(buy_trades),
        "total_sells": len(sell_trades),
        "win_rate": round(len(win_trades) / len(sell_trades) * 100, 1) if sell_trades else 0,
    }


def format_portfolio_summary(summary: dict) -> str:
    """格式化组合摘要为 Markdown。"""
    lines = [
        "# Paper Trading 组合摘要",
        "",
        f"> 生成时间: {_now_shanghai().strftime('%Y-%m-%d %H:%M:%S 北京时间')}",
        "",
        "## 核心指标\n",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总市值 | ¥{summary['total_value']:,.0f} |",
        f"| 净值 | {summary['nav']:.4f} |",
        f"| 累计盈亏 | ¥{summary['total_pnl']:+,.0f} ({summary['total_pnl_pct']:+.2f}%) |",
        f"| 现金 | ¥{summary['cash']:,.0f} ({summary['cash_pct']:.1f}%) |",
        f"| 持仓数 | {len(summary['holdings'])} |",
        f"| 总交易次数 | {summary['total_trades']} |",
        f"| 已平仓胜率 | {summary['win_rate']:.1f}% |",
        "",
    ]

    if summary["holdings"]:
        lines.extend([
            "## 当前持仓\n",
            "| 股票 | 代码 | 股数 | 成本 | 现价 | 市值 | 浮盈 | 浮盈% | 仓位 |",
            "|------|------|------|------|------|------|------|-------|------|",
        ])
        for h in sorted(summary["holdings"], key=lambda x: -x["weight"]):
            lines.append(
                f"| {h['name']} | {h['code']} | {h['shares']} | "
                f"{h['avg_cost']:.2f} | {h['current_price']:.2f} | "
                f"¥{h['market_value']:,.0f} | ¥{h['unrealized_pnl']:+,.0f} | "
                f"{h['unrealized_pct']:+.1f}% | {h['weight']:.1f}% |"
            )
        lines.append("")

    lines.append("---\n*本报告由 Paper Trading 系统自动生成。*")
    return "\n".join(lines)


def auto_trade_from_recommendations(
    enriched_stocks: list[dict],
    max_positions: int = 5,
    max_per_sector: int = 2,
    verbose: bool = True,
) -> list[dict]:
    """根据推荐结果自动执行模拟交易。

    规则：
    - 只买入 decision_tier == "可执行清单" 的股票
    - 每个板块最多 max_per_sector 只
    - 总持仓不超过 max_positions 只
    - 卖出不在最新推荐中的持仓

    Returns:
        执行的交易列表。
    """
    portfolio = _load_portfolio()
    trades = []

    # 卖出不在推荐中的持仓
    recommended_codes = set()
    for stock in enriched_stocks:
        if stock.get("decision_tier") == "可执行清单":
            recommended_codes.add(stock.get("code", ""))

    for code in list(portfolio["positions"].keys()):
        if code not in recommended_codes:
            result = sell_stock(code, reason="不在最新可执行清单中")
            if result["success"]:
                trades.append(result["trade"])
                if verbose:
                    print(f"  [模拟卖出] {result['message']}", flush=True)

    # 买入可执行清单中的股票
    sector_count = {}
    buy_candidates = [
        s for s in enriched_stocks
        if s.get("decision_tier") == "可执行清单"
    ]
    buy_candidates.sort(
        key=lambda s: (s.get("buy_score", 0), s.get("score", 0)),
        reverse=True,
    )

    for stock in buy_candidates:
        if len(portfolio["positions"]) >= max_positions:
            break

        code = stock.get("code", "")
        if not code or code in portfolio["positions"]:
            continue

        sector = stock.get("sector") or "未分类"
        sector_count[sector] = sector_count.get(sector, 0) + 1
        if sector_count[sector] > max_per_sector:
            continue

        # 仓位 = 总资产的 1/max_positions
        target_pct = 100 / max_positions

        result = buy_stock(
            code=code,
            name=stock.get("name", ""),
            target_pct=target_pct,
            score=stock.get("score", 0),
            reason=f"评分{stock.get('score', 0):.1f}，{stock.get('opportunity_type', '')}",
        )
        if result["success"]:
            trades.append(result["trade"])
            if verbose:
                print(f"  [模拟买入] {result['message']}", flush=True)

    # 记录净值
    nav_point = record_nav()
    if verbose:
        print(f"  [净值] {nav_point['nav']:.4f} (总市值 ¥{nav_point['total_value']:,.0f})", flush=True)

    return trades
