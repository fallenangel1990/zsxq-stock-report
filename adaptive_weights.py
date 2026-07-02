"""自适应权重模块。

基于因子 IC（信息系数）分析，自动调整评分权重。
IC 越高的因子分配越高权重，IC 为负或接近零的因子降权或禁用。
支持滚动 IC 计算、因子衰减检测、权重平滑更新。
"""

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml


DATA_DIR = Path(__file__).parent / "data"
WEIGHTS_HISTORY_FILE = DATA_DIR / "weights_history.json"
IC_HISTORY_FILE = DATA_DIR / "ic_history.json"

# 默认权重（市场中性）
DEFAULT_WEIGHTS = {
    "upside": 0.22,
    "quality": 0.16,
    "consensus": 0.12,
    "sector": 0.12,
    "trend": 0.08,
    "fundamentals": 0.08,
    "capital_flow": 0.08,
    "volume_confirm": 0.07,
    "logic": 0.07,
}

# 因子名称映射：score_detail key → 权重 key
FACTOR_TO_WEIGHT = {
    "upside": "upside",
    "quality": "quality",
    "consensus": "consensus",
    "sector": "sector",
    "trend": "trend",
    "fundamentals": "fundamentals",
    "capital_flow": "capital_flow",
    "volume_confirm": "volume_confirm",
    "logic": "logic",
}

# IC 衰减检测参数
IC_DECLINE_WINDOW = 3  # 连续下降多少周触发警告
IC_MIN_THRESHOLD = 0.02  # IC 低于此值视为无效
IC_WEIGHT_FLOOR = 0.02  # 单因子最低权重（防止完全归零）
IC_WEIGHT_CEILING = 0.35  # 单因子最高权重（防止过度集中）
SMOOTHING_ALPHA = 0.3  # 权重平滑系数（0=不更新, 1=完全替换）

# 因子半衰期（天）：快因子信号衰减快，慢因子更持久
FACTOR_HALF_LIFE = {
    "upside": 10,       # 目标价因子：中速衰减
    "quality": 30,      # 质量因子：慢衰减
    "consensus": 5,     # 共识因子：快衰减（热度来得快去得快）
    "sector": 7,        # 板块因子：中速
    "trend": 5,         # 趋势因子：快衰减
    "fundamentals": 60, # 基本面：极慢衰减
    "capital_flow": 3,  # 资金流：极快衰减
    "volume_confirm": 3, # 量价确认：极快衰减
    "logic": 14,        # 逻辑因子：中速
}


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _load_weights_history() -> list[dict]:
    """加载权重更新历史。"""
    if not WEIGHTS_HISTORY_FILE.exists():
        return []
    try:
        return json.loads(WEIGHTS_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_weights_history(history: list[dict]) -> None:
    """保存权重更新历史。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WEIGHTS_HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_ic_history() -> list[dict]:
    """加载 IC 历史。"""
    if not IC_HISTORY_FILE.exists():
        return []
    try:
        return json.loads(IC_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_ic_history(history: list[dict]) -> None:
    """保存 IC 历史。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IC_HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _rank_values(values: list[float]) -> list[float]:
    """计算秩排名（处理并列）。"""
    sorted_vals = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(sorted_vals):
        j = i
        while j < len(sorted_vals) - 1 and sorted_vals[j + 1][1] == sorted_vals[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[sorted_vals[k][0]] = avg_rank
        i = j + 1
    return ranks


def compute_rolling_ic(
    records: list[dict],
    factor_name: str,
    return_days: int = 5,
    min_samples: int = 5,
) -> Optional[float]:
    """计算单个因子的 Rank IC。

    IC = Spearman rank correlation between factor values and forward returns.
    """
    if len(records) < min_samples:
        return None

    pairs = []
    for rec in records:
        detail = rec.get("score_detail") or {}
        val = rec.get(factor_name) or detail.get(factor_name)
        entry_price = rec.get("current_price")
        code = rec.get("code")
        if val is None or not entry_price or entry_price <= 0 or not code:
            continue

        fwd_ret = rec.get(f"forward_return_{return_days}d")
        if fwd_ret is None:
            # 尝试从 score_detail 获取
            fwd_ret = rec.get("forward_return_latest")
        if fwd_ret is None:
            continue

        pairs.append((val, fwd_ret))

    if len(pairs) < min_samples:
        return None

    n = len(pairs)
    factor_ranks = _rank_values([p[0] for p in pairs])
    return_ranks = _rank_values([p[1] for p in pairs])

    d_sq_sum = sum((fr - rr) ** 2 for fr, rr in zip(factor_ranks, return_ranks))
    ic = 1 - 6 * d_sq_sum / (n * (n * n - 1)) if n > 1 else 0
    return round(ic, 4)


def compute_all_factor_ics(
    records: list[dict],
    return_days: int = 5,
) -> dict[str, dict]:
    """计算所有因子的 IC 指标。

    Returns:
        {
            factor_name: {
                "ic": float,
                "ic_abs": float,
                "count": int,
                "positive_rate": float,
            }
        }
    """
    factor_names = list(FACTOR_TO_WEIGHT.keys()) + ["score", "buy_score", "technical_score"]
    results = {}

    for fname in factor_names:
        ic = compute_rolling_ic(records, fname, return_days)
        if ic is not None:
            results[fname] = {
                "ic": ic,
                "ic_abs": abs(ic),
                "count": len(records),
            }

    return results


def detect_ic_decline(
    ic_history: list[dict],
    factor_name: str,
    window: int = IC_DECLINE_WINDOW,
) -> dict:
    """检测因子 IC 是否连续下降。

    Returns:
        {
            "trend": "declining" | "stable" | "improving",
            "decline_weeks": int,
            "current_ic": float,
            "avg_ic": float,
            "alert": bool,
        }
    """
    weekly_ics = []
    for entry in ic_history:
        factor_data = entry.get("factors", {}).get(factor_name)
        if factor_data and "ic" in factor_data:
            weekly_ics.append(factor_data["ic"])

    if len(weekly_ics) < 2:
        return {"trend": "stable", "decline_weeks": 0, "alert": False}

    recent = weekly_ics[-window:] if len(weekly_ics) >= window else weekly_ics
    decline_count = 0
    for i in range(len(recent) - 1):
        if recent[i] > recent[i + 1]:
            decline_count += 1
        else:
            break

    current_ic = weekly_ics[-1]
    avg_ic = sum(weekly_ics) / len(weekly_ics)

    if decline_count >= window - 1 and abs(current_ic) < IC_MIN_THRESHOLD:
        trend = "declining"
        alert = True
    elif decline_count >= window - 1:
        trend = "declining"
        alert = False
    elif all(recent[i] < recent[i + 1] for i in range(len(recent) - 1)):
        trend = "improving"
        alert = False
    else:
        trend = "stable"
        alert = False

    return {
        "trend": trend,
        "decline_weeks": decline_count,
        "current_ic": round(current_ic, 4),
        "avg_ic": round(avg_ic, 4),
        "alert": alert,
    }


def check_ic_stability(
    ic_history: list[dict],
    factor_name: str,
    window: int = 6,
) -> dict:
    """检验因子 IC 的稳定性。

    计算滚动 IC 的自相关性和标准差，判断因子是否可靠。
    自相关性高 = 因子信号稳定；标准差大 = 因子波动大，需降权。

    Args:
        ic_history: IC 历史记录列表。
        factor_name: 因子名称。
        window: 滚动窗口大小。

    Returns:
        {
            "stable": bool,
            "autocorr": float (IC 自相关性),
            "ic_std": float (IC 标准差),
            "ic_mean": float (IC 均值),
            "reliability": float (0-1 可靠性评分),
            "recommendation": str,
        }
    """
    weekly_ics = []
    for entry in ic_history:
        factor_data = entry.get("factors", {}).get(factor_name)
        if factor_data and "ic" in factor_data:
            weekly_ics.append(factor_data["ic"])

    if len(weekly_ics) < window:
        return {
            "stable": True,
            "autocorr": 0.0,
            "ic_std": 0.0,
            "ic_mean": 0.0,
            "reliability": 0.5,
            "recommendation": "数据不足，暂不评估",
        }

    recent_ics = weekly_ics[-window:]
    n = len(recent_ics)

    # IC 均值和标准差
    ic_mean = sum(recent_ics) / n
    ic_var = sum((x - ic_mean) ** 2 for x in recent_ics) / n
    ic_std = ic_var ** 0.5

    # IC 自相关性（lag-1）
    if n >= 3:
        lagged = recent_ics[:-1]
        current = recent_ics[1:]
        mean_lagged = sum(lagged) / len(lagged)
        mean_current = sum(current) / len(current)
        cov = sum((l - mean_lagged) * (c - mean_current) for l, c in zip(lagged, current)) / len(lagged)
        var_lagged = sum((l - mean_lagged) ** 2 for l in lagged) / len(lagged)
        var_current = sum((c - mean_current) ** 2 for c in current) / len(current)
        denom = (var_lagged * var_current) ** 0.5
        autocorr = cov / denom if denom > 0 else 0.0
    else:
        autocorr = 0.0

    # 可靠性评分：自相关性高 + IC 均值高 + 标准差小 = 可靠
    reliability = min(1.0, max(0.0, (
        abs(autocorr) * 0.35 +           # 自相关性权重
        abs(ic_mean) * 2.0 * 0.35 +      # IC 均值权重
        max(0, 0.1 - ic_std) * 3.0 * 0.3  # 低波动权重
    )))

    # 稳定性判断
    stable = abs(autocorr) > 0.3 and abs(ic_mean) > IC_MIN_THRESHOLD

    if stable and reliability > 0.6:
        recommendation = "因子稳定，可正常使用"
    elif stable:
        recommendation = "因子基本稳定，建议观察"
    elif abs(ic_mean) < IC_MIN_THRESHOLD:
        recommendation = "因子 IC 均值过低，建议降权或移除"
    else:
        recommendation = "因子波动大，建议降低权重"

    return {
        "stable": stable,
        "autocorr": round(autocorr, 4),
        "ic_std": round(ic_std, 4),
        "ic_mean": round(ic_mean, 4),
        "reliability": round(reliability, 4),
        "recommendation": recommendation,
    }


def check_all_factors_stability(
    ic_history: list[dict],
    window: int = 6,
) -> dict:
    """检验所有因子的 IC 稳定性。

    Returns:
        {factor_name: stability_result} 的字典。
    """
    results = {}
    for factor_name in FACTOR_TO_WEIGHT:
        results[factor_name] = check_ic_stability(ic_history, factor_name, window)
    return results


def compute_adaptive_weights(
    records: list[dict],
    current_weights: Optional[dict] = None,
    return_days: int = 5,
    smoothing: float = SMOOTHING_ALPHA,
) -> dict:
    """基于因子 IC 计算自适应权重。

    流程：
    1. 计算各因子 IC
    2. IC > threshold 的因子按 IC 大小分配权重
    3. IC <= threshold 的因子权重归零
    4. 与当前权重做指数平滑，避免剧烈跳变
    5. 应用上下限约束

    Returns:
        {
            "weights": {factor_name: weight},
            "ic_data": {factor_name: {ic, count}},
            "decay_alerts": [str],
            "method": "ic_adaptive_smoothed",
        }
    """
    if not records:
        return {
            "weights": current_weights or DEFAULT_WEIGHTS,
            "ic_data": {},
            "decay_alerts": [],
            "method": "fallback_default",
        }

    ic_data = compute_all_factor_ics(records, return_days)

    # 加载 IC 历史用于衰减检测
    ic_history = _load_ic_history()
    decay_alerts = []

    # 基于 IC 计算原始权重
    raw_weights = {}
    for fname, weight_key in FACTOR_TO_WEIGHT.items():
        factor_ic = ic_data.get(fname, {}).get("ic", 0)
        if factor_ic > IC_MIN_THRESHOLD:
            raw_weights[weight_key] = factor_ic
        else:
            raw_weights[weight_key] = 0.0

        # 衰减检测
        decay = detect_ic_decline(ic_history, fname)
        if decay["alert"]:
            decay_alerts.append(
                f"因子 {fname} IC 连续下降至 {decay['current_ic']:.4f}，已失效"
            )
            raw_weights[weight_key] = 0.0
        elif decay["trend"] == "declining":
            raw_weights[weight_key] *= 0.5  # 衰减中降权 50%
            decay_alerts.append(
                f"因子 {fname} IC 趋势下降（当前 {decay['current_ic']:.4f}），已降权"
            )

    # 归一化
    total = sum(raw_weights.values())
    if total > 0:
        normalized = {k: round(v / total, 4) for k, v in raw_weights.items()}
    else:
        # 全部因子失效，回退到均匀权重
        n = len(raw_weights)
        normalized = {k: round(1 / n, 4) for k in raw_weights} if n > 0 else {}

    # 应用上下限约束
    for k in normalized:
        normalized[k] = max(IC_WEIGHT_FLOOR, min(IC_WEIGHT_CEILING, normalized[k]))
    # 重新归一化
    total = sum(normalized.values())
    if total > 0:
        normalized = {k: round(v / total, 4) for k, v in normalized.items()}

    # IC 稳定性调整：对不稳定因子额外降权
    ic_history_stability = _load_ic_history()
    if ic_history_stability:
        stability_results = check_all_factors_stability(ic_history_stability, window=6)
        for fname, weight_key in FACTOR_TO_WEIGHT.items():
            stab = stability_results.get(fname, {})
            if not stab.get("stable", True) and raw_weights.get(weight_key, 0) > 0:
                reliability = stab.get("reliability", 0.5)
                # 不稳定因子按可靠性打折
                raw_weights[weight_key] = raw_weights.get(weight_key, 0) * reliability
                decay_alerts.append(
                    f"因子 {fname} IC 不稳定（自相关{stab.get('autocorr', 0):.2f}，可靠性{reliability:.2f}），已按可靠性降权"
                )

    # 与当前权重做指数平滑
    if current_weights:
        smoothed = {}
        all_keys = set(list(normalized.keys()) + list(current_weights.keys()))
        for k in all_keys:
            new_val = normalized.get(k, 0)
            old_val = current_weights.get(k, 0)
            smoothed[k] = round(smoothing * new_val + (1 - smoothing) * old_val, 4)
        # 重新归一化
        total = sum(smoothed.values())
        if total > 0:
            smoothed = {k: round(v / total, 4) for k, v in smoothed.items()}
        final_weights = smoothed
    else:
        final_weights = normalized

    return {
        "weights": final_weights,
        "ic_data": ic_data,
        "decay_alerts": decay_alerts,
        "method": "ic_adaptive_smoothed",
    }


def update_weight_history(
    weights: dict,
    ic_data: dict,
    decay_alerts: list[str],
) -> None:
    """记录一次权重更新到历史。"""
    history = _load_weights_history()
    history.append({
        "timestamp": _now_shanghai().isoformat(),
        "weights": weights,
        "ic_data": {k: v for k, v in ic_data.items() if "ic" in v},
        "decay_alerts": decay_alerts,
    })
    # 只保留最近 100 条
    history = history[-100:]
    _save_weights_history(history)


def update_ic_history(ic_data: dict) -> None:
    """记录一次 IC 计算到历史。"""
    history = _load_ic_history()
    history.append({
        "timestamp": _now_shanghai().isoformat(),
        "factors": ic_data,
    })
    # 保留最近 52 周
    history = history[-52:]
    _save_ic_history(history)




def orthogonalize_factor_weights(factor_corr_matrix: dict, base_weights: dict) -> dict:
    """因子正交化：去除因子间相关性导致的重复计分。

    当两个因子相关系数 > 0.7 时，对 IC 更低的因子做降权。
    避免同一风险被重复计入。

    Args:
        factor_corr_matrix: {factor_a: {factor_b: corr}} 相关系数矩阵
        base_weights: 基础权重

    Returns:
        正交化后的权重
    """
    adjusted = dict(base_weights)
    CORR_THRESHOLD = 0.7

    for fa in adjusted:
        for fb in adjusted:
            if fa >= fb:
                continue
            corr = factor_corr_matrix.get(fa, {}).get(fb, 0)
            if abs(corr) > CORR_THRESHOLD:
                # 对 IC 绝对值更低的因子降权
                ic_a = abs(_latest_ic_cache.get(fa, 0)) if "_latest_ic_cache" in globals() else 0.1
                ic_b = abs(_latest_ic_cache.get(fb, 0)) if "_latest_ic_cache" in globals() else 0.1
                weaker = fb if ic_a >= ic_b else fa
                adjusted[weaker] = adjusted.get(weaker, 0) * 0.5
                print(f"[正交化] {fa}-{fb} 相关={corr:.2f}, {weaker} 权重减半", flush=True)

    # 归一化
    total = sum(adjusted.values())
    if total > 0:
        adjusted = {k: round(v / total, 4) for k, v in adjusted.items()}
    return adjusted


def get_market_regime_weights(market_regime: str) -> dict:
    """根据市场状态返回因子权重模板。

    不同市场状态下，有效因子不同：
    - 牛市进攻: 重趋势、重动量、重资金流
    - 熊市防守: 重质量、重估值、重基本面
    - 震荡观察: 均衡配置，加重护城河
    - 修复可试仓: 重资金流、重超跌反弹

    Args:
        market_regime: 市场状态标签

    Returns:
        该状态下的因子权重模板
    """
    regime_templates = {
        "强势进攻": {
            "upside": 0.25, "quality": 0.12, "consensus": 0.15,
            "sector": 0.15, "trend": 0.12, "fundamentals": 0.05,
            "capital_flow": 0.08, "volume_confirm": 0.05, "logic": 0.03,
        },
        "修复可试仓": {
            "upside": 0.20, "quality": 0.15, "consensus": 0.10,
            "sector": 0.12, "trend": 0.08, "fundamentals": 0.10,
            "capital_flow": 0.12, "volume_confirm": 0.08, "logic": 0.05,
        },
        "震荡观察": {
            "upside": 0.18, "quality": 0.18, "consensus": 0.10,
            "sector": 0.10, "trend": 0.08, "fundamentals": 0.15,
            "capital_flow": 0.08, "volume_confirm": 0.06, "logic": 0.07,
        },
        "防守降仓": {
            "upside": 0.10, "quality": 0.22, "consensus": 0.08,
            "sector": 0.08, "trend": 0.05, "fundamentals": 0.20,
            "capital_flow": 0.10, "volume_confirm": 0.05, "logic": 0.12,
        },
    }
    return regime_templates.get(market_regime, {})

def get_latest_weights() -> Optional[dict]:
    """获取最近一次更新的权重。"""
    history = _load_weights_history()
    if not history:
        return None
    return history[-1].get("weights")


def load_weights_from_config() -> dict:
    """从 config.yaml 加载静态权重作为初始值。"""
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
            scoring = config.get("stocks", {}).get("scoring", {})
            weights = {}
            for key in DEFAULT_WEIGHTS:
                weights[key] = scoring.get(f"{key}_weight", DEFAULT_WEIGHTS[key])
            return weights
        except Exception:
            pass
    return dict(DEFAULT_WEIGHTS)


def apply_adaptive_weights(records: list[dict], verbose: bool = True) -> dict:
    """完整流程：计算 IC → 更新权重 → 保存历史。

    Returns:
        最终权重字典。
    """
    # 获取当前权重（优先用历史，回退到 config）
    current = get_latest_weights() or load_weights_from_config()

    result = compute_adaptive_weights(records, current_weights=current)

    if verbose:
        print(f"[自适应权重] 方法: {result['method']}", flush=True)
        for fname, ic_info in sorted(
            result["ic_data"].items(),
            key=lambda x: abs(x[1].get("ic", 0)),
            reverse=True,
        )[:5]:
            ic = ic_info.get("ic", 0)
            weight = result["weights"].get(FACTOR_TO_WEIGHT.get(fname, ""), 0)
            print(f"  {fname}: IC={ic:.4f}, 权重={weight:.4f}", flush=True)
        if result["decay_alerts"]:
            for alert in result["decay_alerts"]:
                print(f"  ⚠️ {alert}", flush=True)

    # 保存历史
    update_weight_history(result["weights"], result["ic_data"], result["decay_alerts"])
    update_ic_history(result["ic_data"])

    return result["weights"]


def format_weights_report(weights: dict, ic_data: dict = None, alerts: list[str] = None) -> str:
    """格式化权重报告。"""
    lines = ["## 自适应权重报告\n"]

    if ic_data:
        lines.append("### 因子 IC 排名\n")
        lines.append("| 因子 | IC | 样本数 | 有效性 | 权重 |")
        lines.append("|------|-----|--------|--------|------|")
        sorted_factors = sorted(ic_data.items(), key=lambda x: abs(x[1].get("ic", 0)), reverse=True)
        for fname, info in sorted_factors:
            ic = info.get("ic", 0)
            count = info.get("count", 0)
            weight_key = FACTOR_TO_WEIGHT.get(fname, "")
            weight = weights.get(weight_key, 0)
            if abs(ic) >= 0.05:
                level = "✅ 强"
            elif abs(ic) >= 0.03:
                level = "⚠️ 中"
            else:
                level = "❌ 弱"
            lines.append(f"| {fname} | {ic:.4f} | {count} | {level} | {weight:.4f} |")
        lines.append("")

    if alerts:
        lines.append("### 衰减警告\n")
        for alert in alerts:
            lines.append(f"- {alert}")
        lines.append("")

    lines.append("### 最终权重\n")
    lines.append("| 因子 | 权重 |")
    lines.append("|------|------|")
    for k, v in sorted(weights.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v:.4f} |")
    lines.append("")

    # IC 稳定性报告
    ic_history = _load_ic_history()
    if ic_history:
        stability = check_all_factors_stability(ic_history, window=6)
        lines.append("### IC 稳定性评估\n")
        lines.append("| 因子 | 自相关 | IC均值 | IC标准差 | 可靠性 | 状态 |")
        lines.append("|------|--------|--------|----------|--------|------|")
        for fname, stab in sorted(stability.items(), key=lambda x: -x[1].get("reliability", 0)):
            status = "✅ 稳定" if stab.get("stable") else "⚠️ 欠稳定"
            lines.append(
                f"| {fname} | {stab.get('autocorr', 0):+.3f} | "
                f"{stab.get('ic_mean', 0):+.4f} | "
                f"{stab.get('ic_std', 0):.4f} | "
                f"{stab.get('reliability', 0):.2f} | {status} |"
            )
        lines.append("")

    return "\n".join(lines)
