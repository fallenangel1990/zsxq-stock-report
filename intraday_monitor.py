"""盘中动态预警模块。

监控已推荐股票的关键信号，触发预警并推送通知。
在交易时段（09:30-11:30, 13:00-15:00）内定时轮询，
检测止损跌破、放量异动、均线破位、涨停打开等信号。
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from price_fetcher import fetch_prices, fetch_technical_indicators, fetch_5day_changes


# ── 配置 ──

DEFAULT_POLL_INTERVAL = 300  # 5 分钟
ALERT_COOLDOWN = 1800  # 同一股票同类预警的冷却时间（秒）
STATE_FILE = Path(__file__).parent / "data" / "state" / "intraday_alerts.json"
ENRICHED_DIR = Path(__file__).parent / "data" / "summary"


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _is_trading_hours(now: Optional[datetime] = None) -> bool:
    """判断当前是否在 A 股交易时段。"""
    now = now or _now_shanghai()
    weekday = now.weekday()
    if weekday >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)


def _next_trading_seconds(now: Optional[datetime] = None) -> int:
    """返回距下次交易时段开始的秒数（用于非交易时段休眠）。"""
    now = now or _now_shanghai()
    weekday = now.weekday()
    t = now.hour * 60 + now.minute

    # 当天还有交易时段
    if weekday < 5:
        morning_start = 9 * 60 + 30
        afternoon_start = 13 * 60
        if t < morning_start:
            return (morning_start - t) * 60
        elif 11 * 60 + 30 <= t < afternoon_start:
            return (afternoon_start - t) * 60

    # 下一个工作日
    days_ahead = 1
    if weekday == 4:
        days_ahead = 3
    elif weekday == 5:
        days_ahead = 2
    return days_ahead * 86400


def _load_latest_recommendations() -> list[dict]:
    """加载最近一次推荐的 enriched 股票数据。"""
    if not ENRICHED_DIR.exists():
        return []
    enriched_files = sorted(
        ENRICHED_DIR.glob("*_enriched_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not enriched_files:
        return []
    try:
        stocks = json.loads(enriched_files[0].read_text(encoding="utf-8"))
        return [s for s in stocks if s.get("code")]
    except Exception:
        return []


def _load_alert_state() -> dict:
    """加载预警状态（用于冷却控制）。"""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"alerts": [], "last_check": None}


def _save_alert_state(state: dict) -> None:
    """保存预警状态。"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_in_cooldown(code: str, alert_type: str, state: dict) -> bool:
    """检查该股票的该类预警是否在冷却期内。"""
    now_ts = time.time()
    for alert in reversed(state.get("alerts", [])):
        if alert.get("code") == code and alert.get("type") == alert_type:
            if now_ts - alert.get("ts", 0) < ALERT_COOLDOWN:
                return True
            break
    return False


def check_alerts(
    stocks: list[dict],
    prices: dict,
    technicals: dict,
    changes_5d: dict,
    state: dict,
) -> list[dict]:
    """检查所有预警条件，返回触发的预警列表（含智能降噪）。

    预警类型：
    - stop_loss: 跌破止损位
    - volume_surge: 量比异常放大（>3.0）
    - ma_break: 跌破 5 日均线且幅度 > 1%
    - rapid_drop: 盘中快速下跌（跌幅 > 5%）
    - rsi_extreme: RSI 进入超买（>85）或超卖（<15）区域
    - macd_death: MACD 死叉
    - portfolio_drawdown: 组合级预警（当日浮亏超阈值）

    智能降噪：
    - 高波动股自适应调整阈值（ATR 越大阈值越高）
    - 预警优先级排序（严重 > 注意 > 机会）
    - 组合级预警汇总（避免逐只推送）
    """
    alerts = []
    portfolio_alerts = []
    now = _now_shanghai()
    now_str = now.strftime("%H:%M:%S")

    # 计算组合级指标
    total_market_value = 0
    total_unrealized_pnl = 0
    for stock in stocks:
        code = stock.get("code", "")
        price_info = prices.get(code, {})
        current_price = price_info.get("price")
        if current_price and current_price > 0:
            # 估算仓位（简化：等权）
            total_market_value += current_price

    for stock in stocks:
        code = stock.get("code", "")
        name = stock.get("name", "")
        if not code:
            continue

        price_info = prices.get(code, {})
        tech = technicals.get(code, {})
        current_price = price_info.get("price")
        if not current_price or current_price <= 0:
            continue

        change_pct = price_info.get("change_pct")

        # ── 智能降噪：基于个股波动率自适应阈值 ──
        atr = tech.get("atr_14")
        base_vol = (atr / current_price * 100) if (atr and current_price > 0) else 2.0
        # 高波动股放宽阈值
        vol_factor = max(1.0, base_vol / 2.0)  # 2% 波动为基准

        # 预警 1: 止损跌破
        exit_trigger = stock.get("exit_trigger", "")
        stop_price = _parse_stop_price(exit_trigger, current_price)
        if stop_price and current_price <= stop_price:
            if not _is_in_cooldown(code, "stop_loss", state):
                alerts.append({
                    "code": code, "name": name, "type": "stop_loss",
                    "level": "🔴 严重",
                    "priority": 1,
                    "msg": f"跌破止损位 {stop_price:.2f}（当前 {current_price:.2f}）",
                    "ts": time.time(), "time": now_str,
                })

        # 预警 2: 量比异常放大（自适应阈值）
        vol_ratio = tech.get("volume_ratio")
        surge_threshold = 3.0 * vol_factor
        if vol_ratio and vol_ratio > surge_threshold and change_pct is not None and change_pct < 0:
            if not _is_in_cooldown(code, "volume_surge", state):
                alerts.append({
                    "code": code, "name": name, "type": "volume_surge",
                    "level": "🟡 注意",
                    "priority": 2,
                    "msg": f"放量下跌：量比 {vol_ratio:.1f}（阈值{surge_threshold:.1f}），跌幅 {change_pct:+.2f}%",
                    "ts": time.time(), "time": now_str,
                })

        # 预警 3: 跌破 5 日均线（自适应阈值）
        ma5 = tech.get("ma5")
        ma_break_threshold = 1.0 * vol_factor
        if ma5 and current_price < ma5 * 0.99:
            dist = (current_price / ma5 - 1) * 100
            if dist < -ma_break_threshold and not _is_in_cooldown(code, "ma_break", state):
                alerts.append({
                    "code": code, "name": name, "type": "ma_break",
                    "level": "🟡 注意",
                    "priority": 2,
                    "msg": f"跌破5日线 {dist:+.1f}%（阈值{ma_break_threshold:.1f}%，MA5={ma5:.2f}）",
                    "ts": time.time(), "time": now_str,
                })

        # 预警 4: 盘中快速下跌（自适应阈值）
        drop_threshold = 5.0 * vol_factor
        if change_pct is not None and change_pct < -drop_threshold:
            if not _is_in_cooldown(code, "rapid_drop", state):
                alerts.append({
                    "code": code, "name": name, "type": "rapid_drop",
                    "level": "🔴 严重",
                    "priority": 1,
                    "msg": f"盘中急跌 {change_pct:+.2f}%（阈值{drop_threshold:.1f}%）",
                    "ts": time.time(), "time": now_str,
                })

        # 预警 5: RSI 极端值
        rsi = tech.get("rsi_14")
        if rsi is not None:
            if rsi > 85 and not _is_in_cooldown(code, "rsi_overbought", state):
                alerts.append({
                    "code": code, "name": name, "type": "rsi_overbought",
                    "level": "🟡 注意",
                    "priority": 3,
                    "msg": f"RSI 超买 {rsi:.0f}，注意短期回调风险",
                    "ts": time.time(), "time": now_str,
                })
            elif rsi < 15 and not _is_in_cooldown(code, "rsi_oversold", state):
                alerts.append({
                    "code": code, "name": name, "type": "rsi_oversold",
                    "level": "🟢 机会",
                    "priority": 4,
                    "msg": f"RSI 超卖 {rsi:.0f}，可能存在超跌反弹机会",
                    "ts": time.time(), "time": now_str,
                })

        # 预警 6: MACD 死叉
        macd_hist = tech.get("macd_hist")
        macd_line = tech.get("macd_line")
        if macd_hist is not None and macd_line is not None:
            if macd_hist < 0 and macd_line > 0 and not _is_in_cooldown(code, "macd_death", state):
                alerts.append({
                    "code": code, "name": name, "type": "macd_death",
                    "level": "🟡 注意",
                    "priority": 3,
                    "msg": f"MACD 死叉，趋势可能转弱",
                    "ts": time.time(), "time": now_str,
                })

    # ── 组合级预警：当日浮亏超阈值 ──
    if stocks:
        codes = [s.get("code") for s in stocks if s.get("code")]
        if codes:
            all_changes = [changes_5d.get(c) for c in codes if changes_5d.get(c) is not None]
            if all_changes:
                avg_change = sum(all_changes) / len(all_changes)
                if avg_change < -3:
                    portfolio_alerts.append({
                        "code": "PORTFOLIO",
                        "name": "组合",
                        "type": "portfolio_drawdown",
                        "level": "🔴 严重",
                        "priority": 0,
                        "msg": f"组合平均浮亏 {avg_change:+.2f}%，建议减仓或对冲",
                        "ts": time.time(), "time": now_str,
                    })

    # 按优先级排序（数字越小越优先）
    all_alerts = portfolio_alerts + alerts
    all_alerts.sort(key=lambda x: x.get("priority", 99))

    return all_alerts


def _parse_stop_price(exit_trigger: str, current_price: float) -> Optional[float]:
    """从止损描述中解析止损价格。"""
    if not exit_trigger:
        return None
    import re
    # 匹配 "跌破 XX.XX 元" 或 "约 XX.XX"
    m = re.search(r"(?:跌破|低于|约)\s*(\d+[\.\d]*)\s*(?:元|块)", exit_trigger)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # 匹配 "current * 0.94" 模式
    m = re.search(r"(\d+[\.\d]*)\s*(?:元|块)", exit_trigger)
    if m:
        try:
            price = float(m.group(1))
            if 0.5 < price / current_price < 1.5:
                return price
        except ValueError:
            pass
    return None


def format_alerts(alerts: list[dict]) -> str:
    """格式化预警列表为文本。"""
    if not alerts:
        return ""
    lines = [f"⚠️ 盘中预警（{_now_shanghai().strftime('%H:%M')}）\n"]
    for a in alerts:
        lines.append(f"{a['level']} {a['name']}({a['code']}): {a['msg']}")
    lines.append(f"\n共 {len(alerts)} 条预警")
    return "\n".join(lines)


def format_alerts_html(alerts: list[dict]) -> str:
    """格式化预警列表为 HTML 邮件片段。"""
    if not alerts:
        return ""
    level_colors = {"🔴 严重": "#dc2626", "🟡 注意": "#d97706", "🟢 机会": "#16a34a"}
    rows = []
    for a in alerts:
        color = level_colors.get(a["level"], "#333")
        rows.append(
            f'<tr><td style="padding:8px;border-bottom:1px solid #eee;">'
            f'<span style="color:{color};font-weight:bold;">{a["level"]}</span></td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;">{a["name"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;font-family:monospace;">{a["code"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;">{a["msg"]}</td>'
            f'<td style="padding:8px;border-bottom:1px solid #eee;color:#666;">{a["time"]}</td></tr>'
        )
    return (
        '<h3 style="margin:20px 0 10px;">⚠️ 盘中预警</h3>'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        '<tr style="background:#f8f9fa;font-weight:bold;">'
        '<th style="padding:8px;text-align:left;">级别</th>'
        '<th style="padding:8px;text-align:left;">股票</th>'
        '<th style="padding:8px;text-align:left;">代码</th>'
        '<th style="padding:8px;text-align:left;">预警内容</th>'
        '<th style="padding:8px;text-align:left;">时间</th></tr>'
        + "".join(rows) +
        '</table>'
    )


def send_alert_email(alerts: list[dict]) -> bool:
    """通过邮件发送预警。"""
    if not alerts:
        return True
    try:
        from email_sender import send_email
        now = _now_shanghai()
        subject = f"⚠️ 盘中预警 {now.strftime('%H:%M')} - {len(alerts)}条"
        html_body = format_alerts_html(alerts)
        send_email(subject, html_body)
        return True
    except Exception as exc:
        print(f"[预警邮件] 发送失败: {exc}", flush=True)
        return False




# ── 板块轮动监控 ──

SECTOR_ROTATION_COOLDOWN = 3600  # 板块轮动预警冷却时间（秒）


def _check_sector_rotation(state: dict) -> list[dict]:
    """检测板块轮动，生成风险/机会预警。

    当检测到极端分化：科技/半导体大跌 vs 消费/医药/红利大涨时，
    触发板块轮动预警，提示用户关注风格切换。

    Returns:
        轮动预警列表。
    """
    from sector_monitor import capture_market_signals

    alerts = []
    try:
        report, market, boards = capture_market_signals(
            mode="intraday", top_n=20, with_ai=False,
        )
    except Exception as exc:
        print(f"[板块轮动] 获取信号失败: {exc}", flush=True)
        return alerts

    if not boards:
        return alerts

    # 分离大涨板块和大跌板块
    rising = [b for b in boards if b.get("change_pct", 0) >= 2.0]
    falling = [b for b in boards if b.get("change_pct", 0) <= -2.0]

    if not rising or not falling:
        return alerts

    # 检测是否有高动量大涨（有资金推动）
    strong_rising = [b for b in rising if b.get("main_net_yi", 0) >= 2.0]
    strong_falling = [b for b in falling if b.get("main_net_yi", 0) <= -1.0]

    # 板块轮动预警
    if strong_rising and strong_falling:
        if _is_in_cooldown("SECTOR_ROTATION", "rotation", state):
            return alerts

        now = _now_shanghai()
        now_str = now.strftime("%H:%M:%S")

        rising_names = "、".join(
            f"{b['name']}({b['change_pct']:+.1f}%)" for b in strong_rising[:3]
        )
        falling_names = "、".join(
            f"{b['name']}({b['change_pct']:+.1f}%)" for b in strong_falling[:3]
        )

        alerts.append({
            "code": "SECTOR",
            "name": "板块轮动",
            "type": "sector_rotation",
            "level": "🔴 严重",
            "priority": 0,
            "msg": (
                f"风格极端切换 — 机会方: {rising_names}；"
                f"风险方: {falling_names}。建议检查持仓行业集中度。"
            ),
            "ts": time.time(),
            "time": now_str,
        })

        # 对 strong_falling 中的机会板块单独发机会信号
        for b in strong_rising[:2]:
            action = b.get("action", "")
            if action in ("加仓信号", "建仓信号", "观察试仓"):
                alerts.append({
                    "code": "SECTOR_OP",
                    "name": b["name"],
                    "type": "sector_opportunity",
                    "level": "🟢 机会",
                    "priority": 1,
                    "msg": (
                        f"{b['name']}板块 {b['change_pct']:+.1f}%，"
                        f"主力净流入 {b.get('main_net_yi', 0):.1f}亿，"
                        f"建议: {action}。{b.get('action_reason', '')}"
                    ),
                    "ts": time.time(),
                    "time": now_str,
                })

    return alerts

def run_monitor(
    poll_interval: int = DEFAULT_POLL_INTERVAL,
    max_rounds: int = 0,
    email: bool = True,
    verbose: bool = True,
) -> None:
    """运行盘中预警监控。

    Args:
        poll_interval: 轮询间隔（秒）。
        max_rounds: 最大轮询次数（0=无限，直到非交易时段）。
        email: 是否发送邮件预警。
        verbose: 是否输出详细日志。
    """
    state = _load_alert_state()
    round_count = 0

    if verbose:
        print(f"[盘中预警] 启动监控，轮询间隔 {poll_interval}s", flush=True)

    while True:
        now = _now_shanghai()

        # 非交易时段处理：收盘后自动退出（避免无限休眠直到 workflow 超时）
        if not _is_trading_hours(now):
            # 当天交易已结束（15:00 后）→ 退出
            if now.hour * 100 + now.minute > 1500:
                if verbose:
                    print(f"[盘中预警] 交易日已结束（{now.strftime('%H:%M')}），退出监控", flush=True)
                break
            # 午间休市（11:30-13:00）或开盘前 → 休眠等待
            sleep_secs = _next_trading_seconds(now)
            if verbose:
                print(f"[盘中预警] 非交易时段，休眠 {sleep_secs // 60} 分钟", flush=True)
            time.sleep(min(sleep_secs, 3600))
            continue

        # 加载推荐数据
        stocks = _load_latest_recommendations()
        if not stocks:
            if verbose:
                print("[盘中预警] 无推荐数据，跳过本轮", flush=True)
            time.sleep(poll_interval)
            continue

        codes = [s["code"] for s in stocks]
        if verbose:
            print(f"[盘中预警] 第 {round_count + 1} 轮，监控 {len(codes)} 只股票...", flush=True, end="")
        # 初始化预警列表
        alerts = []

        # 板块轮动检测（每 3 轮执行一次）
        if round_count % 3 == 0:
            sector_alerts = _check_sector_rotation(state)
            if sector_alerts:
                alerts.extend(sector_alerts)

        # 获取行情和技术指标
        try:
            prices = fetch_prices(codes)
            technicals = fetch_technical_indicators(codes)
            changes_5d = fetch_5day_changes(codes)
        except Exception as exc:
            print(f" 行情获取失败: {exc}", flush=True)
            time.sleep(poll_interval)
            continue

        if verbose:
            print(f" 获取 {len(prices)} 只行情", flush=True)

        # 检查预警（个股级 + 板块级）
        if not alerts:
            alerts = check_alerts(stocks, prices, technicals, changes_5d, state)
        else:
            # 已有板块轮动预警，叠加个股预警
            stock_alerts = check_alerts(stocks, prices, technicals, changes_5d, state)
            alerts.extend(stock_alerts)

        if alerts:
            # 记录预警到状态
            state["alerts"].extend(alerts)
            # 只保留最近 200 条
            state["alerts"] = state["alerts"][-200:]
            state["last_check"] = now.isoformat()
            _save_alert_state(state)

            # 输出
            alert_text = format_alerts(alerts)
            print(alert_text, flush=True)

            # 发送邮件
            if email:
                send_alert_email(alerts)
        else:
            if verbose:
                print(f"[盘中预警] 无预警（{now.strftime('%H:%M')}）", flush=True)
            state["last_check"] = now.isoformat()
            _save_alert_state(state)

        round_count += 1
        if max_rounds > 0 and round_count >= max_rounds:
            if verbose:
                print(f"[盘中预警] 达到最大轮次 {max_rounds}，退出", flush=True)
            break

        # 等到下一轮
        time.sleep(poll_interval)
