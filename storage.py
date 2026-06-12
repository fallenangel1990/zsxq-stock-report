"""数据持久化模块。

负责将原始内容和总结报告保存到本地文件系统。
"""

import json
import re
from datetime import datetime
from pathlib import Path

import yaml


def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_dirs() -> tuple[Path, Path]:
    config = _load_config()
    storage_config = config.get("storage", {})
    base = Path(__file__).parent
    raw_dir = base / storage_config.get("raw_dir", "data/raw")
    summary_dir = base / storage_config.get("summary_dir", "data/summary")
    return raw_dir, summary_dir


def save_raw_data(posts: list[dict], group_name: str = "") -> str:
    """保存原始帖子数据为 JSON 文件。

    Args:
        posts: 清洗后的帖子列表。
        group_name: 专栏名称，用于生成文件名。

    Returns:
        保存的文件路径。
    """
    raw_dir, _ = _get_dirs()
    raw_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    if group_name:
        filename = f"{group_name}_{date_str}.json"
    else:
        filename = f"posts_{date_str}.json"

    filepath = raw_dir / filename
    filepath.write_text(
        json.dumps(posts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"原始数据已保存到: {filepath} ({len(posts)} 篇)")
    return str(filepath)


def save_summary(report: str, group_name: str = "") -> str:
    """保存总结报告为 Markdown 文件。

    Args:
        report: Markdown 格式的总结报告。
        group_name: 专栏名称。

    Returns:
        保存的文件路径。
    """
    _, summary_dir = _get_dirs()
    summary_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    if group_name:
        filename = f"{group_name}_summary_{date_str}.md"
    else:
        filename = f"summary_{date_str}.md"

    filepath = summary_dir / filename
    filepath.write_text(report, encoding="utf-8")
    print(f"总结报告已保存到: {filepath}")
    return str(filepath)


def save_stock_report(report: str, group_name: str = "") -> str:
    """保存股票机会提取报告为 Markdown 文件。

    Args:
        report: Markdown 格式的股票机会报告。
        group_name: 专栏名称。

    Returns:
        保存的文件路径。
    """
    _, summary_dir = _get_dirs()
    summary_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    if group_name:
        filename = f"{group_name}_stocks_{date_str}.md"
    else:
        filename = f"stocks_{date_str}.md"

    filepath = summary_dir / filename
    filepath.write_text(report, encoding="utf-8")
    _log(f"股票机会报告已保存到: {filepath}")
    return str(filepath)


def save_sector_report(report: str, mode: str = "review") -> str:
    """保存板块异动/盘后复盘报告为 Markdown 文件。"""
    _, summary_dir = _get_dirs()
    sector_dir = summary_dir / "sectors"
    sector_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_mode = re.sub(r"[^\w\u4e00-\u9fff]", "_", mode or "review")
    filename = f"sector_{safe_mode}_{date_str}.md"

    filepath = sector_dir / filename
    filepath.write_text(report, encoding="utf-8")
    _log(f"板块复盘报告已保存到: {filepath}")
    return str(filepath)


def save_market_signal_report(report: str, mode: str = "intraday") -> str:
    """保存大盘与板块建仓/加仓信号报告。"""
    _, summary_dir = _get_dirs()
    market_dir = summary_dir / "market"
    market_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_mode = re.sub(r"[^\w\u4e00-\u9fff]", "_", mode or "intraday")
    filename = f"market_signals_{safe_mode}_{date_str}.md"

    filepath = market_dir / filename
    filepath.write_text(report, encoding="utf-8")
    _log(f"大盘信号报告已保存到: {filepath}")
    return str(filepath)


def save_market_review_report(report: str) -> str:
    """保存盘后复盘报告。"""
    _, summary_dir = _get_dirs()
    review_dir = summary_dir / "reviews"
    review_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = ".html" if report.lstrip().lower().startswith(("<!doctype html", "<html")) else ".md"
    filename = f"market_review_{date_str}{suffix}"

    filepath = review_dir / filename
    filepath.write_text(report, encoding="utf-8")
    _log(f"盘后复盘报告已保存到: {filepath}")
    return str(filepath)


def save_enriched_stocks(stocks: list[dict], group_name: str = "") -> str:
    """保存增强后的股票数据为 JSON 文件（供 ths_sync 使用）。

    Args:
        stocks: 增强后的股票列表（含 score/code/name 等字段）。
        group_name: 专栏名称。

    Returns:
        保存的文件路径。
    """
    _, summary_dir = _get_dirs()
    summary_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    if group_name:
        filename = f"{group_name}_enriched_{date_str}.json"
    else:
        filename = f"stocks_enriched_{date_str}.json"

    filepath = summary_dir / filename
    filepath.write_text(
        json.dumps(stocks, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _log(f"增强股票数据已保存到: {filepath} ({len(stocks)} 只)")
    return str(filepath)


def append_recommendation_history(stocks: list[dict], group_name: str = "") -> str:
    """追加保存本次推荐快照，供后续命中率和回撤统计使用。"""
    _, summary_dir = _get_dirs()
    history_dir = summary_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    filepath = history_dir / "recommendations.jsonl"
    generated_at = datetime.now().isoformat()
    rows = []
    for stock in stocks:
        if not stock.get("code") and not stock.get("name"):
            continue
        rows.append({
            "generated_at": generated_at,
            "group_name": group_name or "",
            "code": stock.get("code", ""),
            "name": stock.get("name", ""),
            "current_price": stock.get("current_price"),
            "market_cap_yi": stock.get("market_cap_yi"),
            "score": stock.get("score"),
            "buy_score": stock.get("buy_score"),
            "action": stock.get("action", ""),
            "decision_tier": stock.get("decision_tier", ""),
            "opportunity_type": stock.get("opportunity_type", ""),
            "trade_period": stock.get("trade_period", ""),
            "position_advice": stock.get("position_advice", ""),
            "entry_ref": stock.get("entry_ref", ""),
            "exit_trigger": stock.get("exit_trigger", ""),
            "risk_display": stock.get("risk_display", ""),
            "market_filter": stock.get("market_filter", {}),
            "score_detail": stock.get("score_detail", {}),
        })

    if rows:
        with filepath.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    _log(f"推荐历史已追加: {filepath} ({len(rows)} 条)")
    return str(filepath)


def load_latest_stock_data() -> tuple[list[dict], str]:
    """加载最近一次保存的增强股票数据。

    Returns:
        (stocks_list, filepath) 元组，无数据则返回 ([], "")。
    """
    _, summary_dir = _get_dirs()
    if not summary_dir.exists():
        return [], ""

    # 查找最新的 enrich JSON 文件
    enriched_files = sorted(
        summary_dir.glob("*_enriched_*.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not enriched_files:
        return [], ""

    filepath = enriched_files[0]
    stocks = json.loads(filepath.read_text(encoding="utf-8"))
    _log(f"已加载增强股票数据: {filepath} ({len(stocks)} 只)")
    return stocks, str(filepath)


def load_latest_raw(group_id: str = "") -> tuple[list[dict], str]:
    """加载最近一次保存的原始数据。

    Args:
        group_id: 专栏 ID，传入时只匹配该专栏的文件。

    Returns:
        (posts, filepath) 元组。
    """
    raw_dir, _ = _get_dirs()
    if not raw_dir.exists():
        return [], ""

    if group_id:
        json_files = sorted(
            raw_dir.glob(f"{group_id}_*.json"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
    else:
        json_files = sorted(raw_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not json_files:
        return [], ""

    filepath = json_files[0]
    posts = json.loads(filepath.read_text(encoding="utf-8"))
    print(f"已加载原始数据: {filepath} ({len(posts)} 篇)")
    return posts, str(filepath)


# ── 增量爬取状态管理 ──

def _get_state_dir() -> Path:
    base = Path(__file__).parent
    return base / "data" / "state"


def load_crawl_state(group_id: str) -> dict:
    """加载专栏的上次爬取状态。

    Returns:
        dict: {last_url: str, last_time: str, crawled_at: str, total: int}
        如果没有历史记录返回空 dict。
    """
    state_file = _get_state_dir() / f"{group_id}.json"
    if state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {}


def save_crawl_state(group_id: str, latest_post: dict, total_new: int) -> None:
    """保存本次爬取状态，记录最新一篇帖子的标识。

    Args:
        group_id: 专栏 ID。
        latest_post: 本次爬取到的最新（第一篇）帖子。
        total_new: 本次新增帖子数。
    """
    state_dir = _get_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "last_topic_id": latest_post.get("topic_id", ""),
        "last_title": latest_post.get("title", ""),
        "last_time": latest_post.get("time", ""),
        "crawled_at": datetime.now().isoformat(),
        "total_new": total_new,
    }
    state_file = state_dir / f"{group_id}.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    _log(f"爬取状态已更新: {state_file}")


def save_research_report(report: str, stock_name: str = "") -> str:
    """保存个股深度研究报告为 Markdown 文件。

    Args:
        report: Markdown 格式的深度研究报告。
        stock_name: 股票名称。

    Returns:
        保存的文件路径。
    """
    _, summary_dir = _get_dirs()
    research_dir = summary_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^\w\u4e00-\u9fff]", "_", stock_name) if stock_name else "unknown"
    filename = f"{safe_name}_research_{date_str}.md"

    filepath = research_dir / filename
    filepath.write_text(report, encoding="utf-8")
    _log(f"深度研究报告已保存到: {filepath}")
    return str(filepath)


def _log(msg: str) -> None:
    print(msg, flush=True)
