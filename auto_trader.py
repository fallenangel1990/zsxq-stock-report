"""自动程序化交易模块 — 基于 EasyTrader 对接券商独立交易客户端。

支持券商：东方财富 (eb)、华泰 (ht)、中信 (zx) 等。
支持模式：半自动（人工确认）/ 全自动（风控兜底）。

风控机制：
  - 日内交易限额（默认最多 3 笔）
  - 单日最大回撤熔断（默认 -5% 停止开新仓）
  - 黑名单过滤（ST、退市预警、上市不满 60 天）
  - ATR 动态止损
  - 单票仓位上限（默认 20%）
  - 板块集中度上限（默认 40%）

使用方法：
  python3 main.py auto --mode semi     # 半自动：生成信号 + 推送通知
  python3 main.py auto --mode full     # 全自动：信号触发 + 风控校验 + 自动下单
  python3 main.py auto --status        # 查看交易状态和风控指标
  python3 main.py auto --connect       # 仅连接测试
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from price_fetcher import fetch_single_price

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 路径与常量
# ──────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent / "data"
TRADE_DIR = DATA_DIR / "auto_trading"
TRADE_LOG_FILE = TRADE_DIR / "trades.jsonl"
DAILY_STATE_FILE = TRADE_DIR / "daily_state.json"
POSITIONS_FILE = TRADE_DIR / "positions.json"
ACCOUNT_STATE_FILE = TRADE_DIR / "account_state.json"

# 默认风控参数
DEFAULT_MAX_DAILY_TRADES = 3
DEFAULT_MAX_DAILY_LOSS_PCT = 5.0
DEFAULT_MAX_SINGLE_POSITION_PCT = 20.0
DEFAULT_MAX_SECTOR_PCT = 40.0
DEFAULT_MIN_LISTING_DAYS = 60
DEFAULT_BUY_SCORE_THRESHOLD = 7.4
DEFAULT_BUY_TOTAL_SCORE = 7.0
DEFAULT_SELL_SCORE_THRESHOLD = 4.0
DEFAULT_SELECTIVITY_MIN = 3.0

# 黑名单关键词（股票名称含这些字自动排除）
BLACKLIST_KEYWORDS = ["ST", "退", "退市", "风险警示", "*ST"]

# 支持的券商映射
BROKER_MAP = {
    "eb": "东方财富",
    "ht": "华泰证券",
    "zx": "中信证券",
    "gf": "广发证券",
    "pa": "平安证券",
    "hs": "华泰证券",
    "ct": "财通证券",
    "gt": "国泰君安",
    "zs": "招商证券",
    "sw": "申万宏源",
}


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _today_str() -> str:
    return _now_shanghai().strftime("%Y-%m-%d")


def _ensure_dirs() -> None:
    TRADE_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────
# 风控状态管理
# ──────────────────────────────────────────────────────────────
class RiskController:
    """交易风控控制器。"""

    def __init__(self, config: dict):
        self.max_daily_trades = config.get("max_daily_trades", DEFAULT_MAX_DAILY_TRADES)
        self.max_daily_loss_pct = config.get("max_daily_loss_pct", DEFAULT_MAX_DAILY_LOSS_PCT)
        self.max_single_position_pct = config.get("max_single_position_pct", DEFAULT_MAX_SINGLE_POSITION_PCT)
        self.max_sector_pct = config.get("max_sector_pct", DEFAULT_MAX_SECTOR_PCT)
        self.min_listing_days = config.get("min_listing_days", DEFAULT_MIN_LISTING_DAYS)
        self.buy_score_threshold = config.get("buy_score_threshold", DEFAULT_BUY_SCORE_THRESHOLD)
        self.buy_total_score = config.get("buy_total_score", DEFAULT_BUY_TOTAL_SCORE)
        self.sell_score_threshold = config.get("sell_score_threshold", DEFAULT_SELL_SCORE_THRESHOLD)
        self.selectivity_min = config.get("selectivity_min", DEFAULT_SELECTIVITY_MIN)
        self.liquidity_gate = config.get("liquidity_gate", True)
        self._load_daily_state()

    def _load_daily_state(self) -> None:
        """加载或重置每日交易状态。"""
        today = _today_str()
        if DAILY_STATE_FILE.exists():
            try:
                state = json.loads(DAILY_STATE_FILE.read_text(encoding="utf-8"))
                if state.get("date") == today:
                    self.daily_state = state
                    return
            except Exception:
                pass
        # 新的一天，重置状态
        self.daily_state = {
            "date": today,
            "trade_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "daily_pnl": 0.0,
            "circuit_breaker_triggered": False,
            "circuit_breaker_reason": "",
            "trades": [],
        }
        self._save_daily_state()

    def _save_daily_state(self) -> None:
        _ensure_dirs()
        DAILY_STATE_FILE.write_text(
            json.dumps(self.daily_state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def is_blacklisted(self, name: str) -> bool:
        """检查股票是否在黑名单中。"""
        if not name:
            return False
        return any(kw in name for kw in BLACKLIST_KEYWORDS)

    def can_trade(self) -> tuple[bool, str]:
        """检查是否允许交易。"""
        if self.daily_state.get("circuit_breaker_triggered"):
            return False, f"熔断已触发: {self.daily_state.get('circuit_breaker_reason', '未知原因')}"
        if self.daily_state.get("trade_count", 0) >= self.max_daily_trades:
            return False, f"日内交易已达上限 ({self.max_daily_trades} 笔)"
        return True, "允许交易"

    def check_drawdown(self, current_nav: float, initial_capital: float) -> bool:
        """检查是否触发回撤熔断。"""
        if initial_capital <= 0:
            return False
        loss_pct = (initial_capital - current_nav) / initial_capital * 100
        if loss_pct >= self.max_daily_loss_pct:
            self.daily_state["circuit_breaker_triggered"] = True
            self.daily_state["circuit_breaker_reason"] = (
                f"单日回撤 {loss_pct:.1f}% ≥ 阈值 {self.max_daily_loss_pct}%"
            )
            self._save_daily_state()
            logger.warning("⚠️ 熔断触发: %s", self.daily_state["circuit_breaker_reason"])
            return True
        return False

    def record_trade(self, trade: dict) -> None:
        """记录一笔交易。"""
        self.daily_state["trade_count"] = self.daily_state.get("trade_count", 0) + 1
        if trade.get("action") == "buy":
            self.daily_state["buy_count"] = self.daily_state.get("buy_count", 0) + 1
        elif trade.get("action") == "sell":
            self.daily_state["sell_count"] = self.daily_state.get("sell_count", 0) + 1
        self.daily_state.setdefault("trades", []).append(trade)
        self._save_daily_state()

    def update_daily_pnl(self, pnl: float) -> None:
        """更新当日盈亏。"""
        self.daily_state["daily_pnl"] = pnl
        self._save_daily_state()

    def get_status(self) -> dict:
        """获取风控状态摘要。"""
        return {
            "date": self.daily_state.get("date", _today_str()),
            "trade_count": self.daily_state.get("trade_count", 0),
            "max_trades": self.max_daily_trades,
            "buy_count": self.daily_state.get("buy_count", 0),
            "sell_count": self.daily_state.get("sell_count", 0),
            "circuit_breaker": self.daily_state.get("circuit_breaker_triggered", False),
            "circuit_breaker_reason": self.daily_state.get("circuit_breaker_reason", ""),
            "daily_pnl": self.daily_state.get("daily_pnl", 0.0),
        }


# ──────────────────────────────────────────────────────────────
# 券商交易客户端封装
# ──────────────────────────────────────────────────────────────
class BrokerClient:
    """券商交易客户端封装（基于 EasyTrader）。"""

    def __init__(self, broker: str = "eb", config: Optional[dict] = None):
        self.broker = broker
        self.config = config or {}
        self.client = None
        self.connected = False

    def connect(self) -> bool:
        """连接券商交易客户端。"""
        try:
            import easytrader
        except ImportError:
            logger.error("未安装 easytrader。请运行: pip install easytrader")
            return False

        try:
            self.client = easytrader.use(self.broker)

            # 从配置或环境变量获取连接参数
            user = self.config.get("user", os.environ.get("BROKER_USER", ""))
            password = self.config.get("password", os.environ.get("BROKER_PASSWORD", ""))
            comm_password = self.config.get("comm_password", os.environ.get("BROKER_COMM_PASSWORD", ""))

            if user and password:
                self.client.prepare(user=user, password=password, comm_password=comm_password or None)
            else:
                self.client.prepare()

            self.connected = True
            logger.info("✅ 已连接券商: %s", BROKER_MAP.get(self.broker, self.broker))
            return True
        except Exception as e:
            logger.error("❌ 连接券商失败: %s", e)
            self.connected = False
            return False

    def balance(self) -> Optional[dict]:
        """查询账户资金。"""
        if not self.connected or not self.client:
            return None
        try:
            return self.client.balance
        except Exception as e:
            logger.error("查询资金失败: %s", e)
            return None

    def position(self) -> list[dict]:
        """查询当前持仓。"""
        if not self.connected or not self.client:
            return []
        try:
            return self.client.position or []
        except Exception as e:
            logger.error("查询持仓失败: %s", e)
            return []

    def buy(self, code: str, price: float, amount: int) -> dict:
        """买入股票。

        Args:
            code: 6 位股票代码
            price: 委托价格（0 = 市价）
            amount: 买入数量（股）

        Returns:
            {"success": bool, "order_no": str, "message": str}
        """
        if not self.connected or not self.client:
            return {"success": False, "message": "未连接券商"}
        try:
            # EasyTrader 的 buy 接口
            result = self.client.buy(
                code,
                price=price if price > 0 else None,
                amount=amount,
            )
            logger.info("买入委托: %s @ %.2f × %d → %s", code, price, amount, result)
            return {
                "success": True,
                "order_no": str(result.get("entrust_no", result.get("order_no", ""))),
                "message": str(result),
            }
        except Exception as e:
            logger.error("买入失败 %s: %s", code, e)
            return {"success": False, "message": str(e)}

    def sell(self, code: str, price: float, amount: int) -> dict:
        """卖出股票。"""
        if not self.connected or not self.client:
            return {"success": False, "message": "未连接券商"}
        try:
            result = self.client.sell(
                code,
                price=price if price > 0 else None,
                amount=amount,
            )
            logger.info("卖出委托: %s @ %.2f × %d → %s", code, price, amount, result)
            return {
                "success": True,
                "order_no": str(result.get("entrust_no", result.get("order_no", ""))),
                "message": str(result),
            }
        except Exception as e:
            logger.error("卖出失败 %s: %s", code, e)
            return {"success": False, "message": str(e)}

    def cancel_all(self) -> dict:
        """撤销所有未成交委托。"""
        if not self.connected or not self.client:
            return {"success": False, "message": "未连接券商"}
        try:
            result = self.client.cancel_all()
            return {"success": True, "message": str(result)}
        except Exception as e:
            logger.error("撤单失败: %s", e)
            return {"success": False, "message": str(e)}


# ──────────────────────────────────────────────────────────────
# 信号生成器
# ──────────────────────────────────────────────────────────────
class SignalGenerator:
    """根据评分结果生成买卖信号。"""

    def __init__(self, risk: RiskController):
        self.risk = risk

    def generate_signals(
        self,
        enriched_stocks: list[dict],
        current_positions: list[dict],
    ) -> dict:
        """生成交易信号。

        Returns:
            {
                "buy": [stock, ...],
                "sell": [stock, ...],
                "hold": [stock, ...],
                "skip": [stock, ...],
            }
        """
        # 函数级导入 stock_extractor 的精选/流动性工具（避免模块级循环依赖）
        from stock_extractor import _liquidity_eligible, _selectivity_score

        signals = {"buy": [], "sell": [], "hold": [], "skip": []}
        position_codes = {p.get("code", p.get("stock_code", "")) for p in current_positions}

        for stock in enriched_stocks:
            code = stock.get("code", "")
            name = stock.get("name", "")
            score = stock.get("score", 0)
            buy_score = stock.get("buy_score", 0)
            tier = stock.get("decision_tier", "")

            # 黑名单过滤
            if self.risk.is_blacklisted(name):
                signals["skip"].append({**stock, "skip_reason": "黑名单"})
                continue

            # 卖出信号：持仓中但评分低于阈值或决策层级下降
            if code in position_codes:
                if score < self.risk.sell_score_threshold:
                    signals["sell"].append({**stock, "signal_reason": f"评分{score:.1f} < {self.risk.sell_score_threshold}"})
                elif tier == "剔除/暂不买入":
                    signals["sell"].append({**stock, "signal_reason": "决策层级降为剔除"})
                else:
                    signals["hold"].append(stock)
                continue

            # 买入信号
            if tier == "可执行清单" and buy_score >= self.risk.buy_score_threshold and score >= self.risk.buy_total_score:
                # 流动性门槛：低流动性票不进可执行清单
                if self.risk.liquidity_gate and not _liquidity_eligible(stock):
                    signals["skip"].append({**stock, "skip_reason": f"流动性不足(市值{stock.get('market_cap_yi')}亿<50亿)"})
                    continue
                sel = stock.get("selectivity_score")
                # 精选分门槛：有精选分但低于门槛的票不进买入
                if sel is not None and sel < self.risk.selectivity_min:
                    signals["skip"].append({**stock, "skip_reason": f"精选分{sel:.1f}<{self.risk.selectivity_min}"})
                    continue
                ltv = stock.get("long_term_value")
                liq = stock.get("liquidity_score")
                reason = f"buy_score={buy_score:.1f}, score={score:.1f}"
                if sel is not None:
                    reason += f", 精选分={sel:.1f}"
                if ltv is not None:
                    reason += f", 长期价值={ltv:.1f}"
                if liq is not None:
                    reason += f", 流动性={liq:.1f}"
                signals["buy"].append({**stock, "signal_reason": reason})
            elif tier == "观察清单":
                signals["skip"].append({**stock, "skip_reason": "观察清单，等待触发"})
            else:
                signals["skip"].append({**stock, "skip_reason": f"tier={tier}, buy_score={buy_score:.1f}"})

        # 排序：买入按 (精选分, buy_score) 降序；无精选分回退 buy_score
        def _buy_sort_key(x):
            sel = x.get("selectivity_score")
            return (sel if sel is not None else -1.0, x.get("buy_score", 0))
        signals["buy"].sort(key=_buy_sort_key, reverse=True)
        signals["sell"].sort(key=lambda x: x.get("score", 0))

        return signals


# ──────────────────────────────────────────────────────────────
# 交易执行器
# ──────────────────────────────────────────────────────────────
class AutoTrader:
    """自动交易主控制器。"""

    def __init__(self, config: dict):
        self.config = config
        self.risk = RiskController(config.get("risk", {}))
        self.broker = BrokerClient(
            broker=config.get("broker", "eb"),
            config=config.get("broker_config", {}),
        )
        self.signal_gen = SignalGenerator(self.risk)
        self.mode = config.get("mode", "semi")  # semi / full
        self.initial_capital = config.get("initial_capital", 1_000_000)

    def connect(self) -> bool:
        """连接券商。"""
        return self.broker.connect()

    def get_account_info(self) -> dict:
        """获取账户信息。"""
        balance = self.broker.balance()
        positions = self.broker.position()
        if not balance:
            return {"error": "无法获取账户信息"}

        total_asset = balance.get("总资产", balance.get("total_asset", 0))
        available = balance.get("可用金额", balance.get("available", 0))
        market_value = balance.get("股票市值", balance.get("market_value", 0))

        return {
            "total_asset": total_asset,
            "available": available,
            "market_value": market_value,
            "positions": positions,
            "position_count": len(positions),
        }

    def execute_signals(self, signals: dict, account_info: dict) -> list[dict]:
        """执行交易信号。"""
        executed = []
        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            logger.warning("风控拦截: %s", reason)
            return executed

        total_asset = account_info.get("total_asset", 0)
        if total_asset <= 0:
            logger.error("无法获取总资产，跳过交易")
            return executed

        # 先执行卖出（释放资金）
        for stock in signals.get("sell", []):
            can_trade, reason = self.risk.can_trade()
            if not can_trade:
                break
            result = self._execute_sell(stock, account_info)
            if result:
                executed.append(result)

        # 再执行买入
        for stock in signals.get("buy", []):
            can_trade, reason = self.risk.can_trade()
            if not can_trade:
                break
            result = self._execute_buy(stock, account_info, total_asset)
            if result:
                executed.append(result)

        return executed

    def _execute_buy(self, stock: dict, account_info: dict, total_asset: float) -> Optional[dict]:
        """执行买入。"""
        code = stock.get("code", "")
        name = stock.get("name", "")
        available = account_info.get("available", 0)

        if not code or available <= 0:
            return None

        # 计算仓位
        max_position_value = total_asset * self.risk.max_single_position_pct / 100
        # 获取当前价格
        price = stock.get("current_price", 0)
        if not price or price <= 0:
            price_info = fetch_single_price(code)
            price = price_info.get("price", 0) if price_info else 0
        if price <= 0:
            logger.warning("无法获取 %s(%s) 行情，跳过买入", name, code)
            return None

        # 计算可买数量（100 股整数倍）
        shares = int(max_position_value / price / 100) * 100
        if shares < 100:
            logger.warning("资金不足买入 %s(%s)，跳过", name, code)
            return None

        # 检查可用资金
        cost = price * shares
        if cost > available * 0.95:  # 留 5% 缓冲
            shares = int(available * 0.95 / price / 100) * 100
            if shares < 100:
                return None
            cost = price * shares

        # 半自动模式：只生成信号，不下单
        if self.mode == "semi":
            trade = {
                "action": "buy",
                "code": code,
                "name": name,
                "price": price,
                "shares": shares,
                "amount": round(cost, 2),
                "reason": stock.get("signal_reason", ""),
                "time": _now_shanghai().isoformat(),
                "mode": "semi",
                "status": "pending_confirm",
            }
            self._log_trade(trade)
            logger.info(
                "📊 [买入信号] %s(%s) @ %.2f × %d = ¥%.0f | %s",
                name, code, price, shares, cost, stock.get("signal_reason", ""),
            )
            return trade

        # 全自动模式：直接下单
        result = self.broker.buy(code, price, shares)
        trade = {
            "action": "buy",
            "code": code,
            "name": name,
            "price": price,
            "shares": shares,
            "amount": round(cost, 2),
            "reason": stock.get("signal_reason", ""),
            "time": _now_shanghai().isoformat(),
            "mode": "full",
            "status": "executed" if result.get("success") else "failed",
            "broker_response": result.get("message", ""),
            "order_no": result.get("order_no", ""),
        }
        self._log_trade(trade)
        if result.get("success"):
            self.risk.record_trade(trade)
            logger.info("✅ 买入成功: %s(%s) × %d", name, code, shares)
        else:
            logger.error("❌ 买入失败: %s(%s) — %s", name, code, result.get("message", ""))
        return trade

    def _execute_sell(self, stock: dict, account_info: dict) -> Optional[dict]:
        """执行卖出。"""
        code = stock.get("code", "")
        name = stock.get("name", "")

        # 查找持仓
        positions = account_info.get("positions", [])
        pos = None
        for p in positions:
            pcode = p.get("code", p.get("stock_code", p.get("证券代码", "")))
            if pcode == code:
                pos = p
                break

        if not pos:
            logger.warning("未找到持仓 %s(%s)，跳过卖出", name, code)
            return None

        # 获取可卖数量
        shares = pos.get("usable", pos.get("可用数量", pos.get("amount", 0)))
        if not shares or shares <= 0:
            return None

        price = stock.get("current_price", 0)
        if not price or price <= 0:
            price_info = fetch_single_price(code)
            price = price_info.get("price", 0) if price_info else 0

        if self.mode == "semi":
            trade = {
                "action": "sell",
                "code": code,
                "name": name,
                "price": price,
                "shares": shares,
                "amount": round(price * shares, 2),
                "reason": stock.get("signal_reason", ""),
                "time": _now_shanghai().isoformat(),
                "mode": "semi",
                "status": "pending_confirm",
            }
            self._log_trade(trade)
            logger.info(
                "📊 [卖出信号] %s(%s) @ %.2f × %d | %s",
                name, code, price, shares, stock.get("signal_reason", ""),
            )
            return trade

        result = self.broker.sell(code, price, shares)
        trade = {
            "action": "sell",
            "code": code,
            "name": name,
            "price": price,
            "shares": shares,
            "amount": round(price * shares, 2),
            "reason": stock.get("signal_reason", ""),
            "time": _now_shanghai().isoformat(),
            "mode": "full",
            "status": "executed" if result.get("success") else "failed",
            "broker_response": result.get("message", ""),
            "order_no": result.get("order_no", ""),
        }
        self._log_trade(trade)
        if result.get("success"):
            self.risk.record_trade(trade)
            logger.info("✅ 卖出成功: %s(%s) × %d", name, code, shares)
        else:
            logger.error("❌ 卖出失败: %s(%s) — %s", name, code, result.get("message", ""))
        return trade

    def _log_trade(self, trade: dict) -> None:
        """记录交易日志。"""
        _ensure_dirs()
        with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(trade, ensure_ascii=False) + "\n")

    def run(self, enriched_stocks: Optional[list[dict]] = None) -> dict:
        """运行一次交易循环。

        Args:
            enriched_stocks: 已评分的股票列表。为 None 时自动加载最新数据。

        Returns:
            运行结果摘要。
        """
        result = {
            "time": _now_shanghai().isoformat(),
            "mode": self.mode,
            "signals": {},
            "executed": [],
            "risk_status": self.risk.get_status(),
        }

        # 1. 连接券商
        if not self.connect():
            result["error"] = "券商连接失败"
            return result

        # 2. 获取账户信息
        account_info = self.get_account_info()
        if "error" in account_info:
            result["error"] = account_info["error"]
            return result
        result["account"] = {
            "total_asset": account_info.get("total_asset", 0),
            "available": account_info.get("available", 0),
            "position_count": account_info.get("position_count", 0),
        }

        # 3. 检查回撤熔断
        total_asset = account_info.get("total_asset", 0)
        if self.risk.check_drawdown(total_asset, self.initial_capital):
            result["circuit_breaker"] = True
            result["circuit_breaker_reason"] = self.risk.daily_state.get("circuit_breaker_reason", "")
            logger.warning("⚠️ 回撤熔断已触发，停止交易")
            return result

        # 4. 加载股票数据
        if enriched_stocks is None:
            from storage import load_latest_stock_data
            enriched_stocks, _ = load_latest_stock_data()

        if not enriched_stocks:
            result["error"] = "无推荐数据"
            return result

        # 5. 生成信号
        signals = self.signal_gen.generate_signals(
            enriched_stocks, account_info.get("positions", [])
        )
        result["signals"] = {
            "buy_count": len(signals["buy"]),
            "sell_count": len(signals["sell"]),
            "hold_count": len(signals["hold"]),
            "skip_count": len(signals["skip"]),
            "buy_list": [
                {"code": s.get("code"), "name": s.get("name"),
                 "score": s.get("score"), "buy_score": s.get("buy_score"),
                 "reason": s.get("signal_reason", "")}
                for s in signals["buy"]
            ],
            "sell_list": [
                {"code": s.get("code"), "name": s.get("name"),
                 "score": s.get("score"),
                 "reason": s.get("signal_reason", "")}
                for s in signals["sell"]
            ],
        }

        # 6. 执行交易
        executed = self.execute_signals(signals, account_info)
        result["executed"] = executed
        result["risk_status"] = self.risk.get_status()

        return result

    def get_status_report(self) -> str:
        """生成状态报告。"""
        risk_status = self.risk.get_status()
        lines = [
            "# 自动交易状态报告",
            "",
            f"> 生成时间: {_now_shanghai().strftime('%Y-%m-%d %H:%M:%S 北京时间')}",
            "",
            "## 风控状态",
            "",
            f"| 指标 | 数值 |",
            f"|------|------|",
            f"| 当日交易 | {risk_status['trade_count']}/{risk_status['max_trades']} 笔 |",
            f"| 买入 | {risk_status['buy_count']} 笔 |",
            f"| 卖出 | {risk_status['sell_count']} 笔 |",
            f"| 熔断 | {'已触发 ⚠️' if risk_status['circuit_breaker'] else '未触发 ✅'} |",
            f"| 当日盈亏 | ¥{risk_status['daily_pnl']:+,.2f} |",
        ]

        if risk_status["circuit_breaker_reason"]:
            lines.append(f"| 熔断原因 | {risk_status['circuit_breaker_reason']} |")

        # 账户信息
        if self.connect():
            account = self.get_account_info()
            if "error" not in account:
                lines.extend([
                    "",
                    "## 账户信息",
                    "",
                    f"| 指标 | 数值 |",
                    f"|------|------|",
                    f"| 总资产 | ¥{account.get('total_asset', 0):,.2f} |",
                    f"| 可用资金 | ¥{account.get('available', 0):,.2f} |",
                    f"| 持仓数 | {account.get('position_count', 0)} |",
                ])

        lines.append("")
        lines.append("---")
        lines.append("*本报告由 Auto Trader 系统自动生成。*")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────────────
def load_trading_config() -> dict:
    """加载交易配置。"""
    config_path = Path(__file__).parent / "config.yaml"
    default_config = {
        "broker": "eb",
        "mode": "semi",
        "initial_capital": 1_000_000,
        "risk": {
            "max_daily_trades": DEFAULT_MAX_DAILY_TRADES,
            "max_daily_loss_pct": DEFAULT_MAX_DAILY_LOSS_PCT,
            "max_single_position_pct": DEFAULT_MAX_SINGLE_POSITION_PCT,
            "max_sector_pct": DEFAULT_MAX_SECTOR_PCT,
            "buy_score_threshold": DEFAULT_BUY_SCORE_THRESHOLD,
            "buy_total_score": DEFAULT_BUY_TOTAL_SCORE,
            "sell_score_threshold": DEFAULT_SELL_SCORE_THRESHOLD,
        },
    }

    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                import yaml
                cfg = yaml.safe_load(f) or {}
            trading = cfg.get("trading", {})
            default_config.update(trading)
            # 合并 risk 子配置
            if "risk" in trading:
                default_config["risk"].update(trading["risk"])
        except Exception:
            pass

    return default_config


def cmd_auto(args) -> None:
    """执行自动交易命令。"""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_trading_config()

    # 命令行参数覆盖配置
    if hasattr(args, "mode") and args.mode:
        config["mode"] = args.mode
    if hasattr(args, "broker") and args.broker:
        config["broker"] = args.broker

    trader = AutoTrader(config)

    if args.action == "status":
        print(trader.get_status_report())
        return

    if args.action == "connect":
        if trader.connect():
            account = trader.get_account_info()
            if "error" not in account:
                print(f"✅ 连接成功")
                print(f"   总资产: ¥{account.get('total_asset', 0):,.2f}")
                print(f"   可用: ¥{account.get('available', 0):,.2f}")
                print(f"   持仓: {account.get('position_count', 0)} 只")
            else:
                print(f"❌ {account['error']}")
        else:
            print("❌ 连接失败")
        return

    # 执行交易
    print(f"🤖 自动交易模式: {config['mode']}")
    print(f"   券商: {BROKER_MAP.get(config['broker'], config['broker'])}")
    print()

    result = trader.run()

    if "error" in result:
        print(f"❌ 错误: {result['error']}")
        return

    if result.get("circuit_breaker"):
        print(f"⚠️ 熔断已触发: {result.get('circuit_breaker_reason', '')}")
        return

    signals = result.get("signals", {})
    print(f"📊 信号统计:")
    print(f"   买入: {signals.get('buy_count', 0)} 只")
    print(f"   卖出: {signals.get('sell_count', 0)} 只")
    print(f"   持有: {signals.get('hold_count', 0)} 只")
    print(f"   跳过: {signals.get('skip_count', 0)} 只")
    print()

    if signals.get("buy_list"):
        print("📈 买入信号:")
        for s in signals["buy_list"]:
            print(f"   {s['name']}({s['code']}) score={s['score']:.1f} buy={s['buy_score']:.1f} | {s['reason']}")
        print()

    if signals.get("sell_list"):
        print("📉 卖出信号:")
        for s in signals["sell_list"]:
            print(f"   {s['name']}({s['code']}) score={s['score']:.1f} | {s['reason']}")
        print()

    executed = result.get("executed", [])
    if executed:
        print(f"✅ 执行了 {len(executed)} 笔交易:")
        for t in executed:
            action_emoji = "🟢" if t["action"] == "buy" else "🔴"
            print(f"   {action_emoji} {t['name']}({t['code']}) {t['action']} × {t['shares']} @ {t['price']:.2f} = ¥{t['amount']:,.0f} [{t['status']}]")

    risk = result.get("risk_status", {})
    print()
    print(f"🛡️ 风控: 当日交易 {risk.get('trade_count', 0)}/{risk.get('max_trades', 3)} | 熔断: {'是' if risk.get('circuit_breaker') else '否'}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="自动程序化交易")
    parser.add_argument("action", choices=["run", "status", "connect"], default="run", nargs="?")
    parser.add_argument("--mode", choices=["semi", "full"], help="交易模式")
    parser.add_argument("--broker", choices=list(BROKER_MAP.keys()), help="券商代码")
    args = parser.parse_args()
    cmd_auto(args)
