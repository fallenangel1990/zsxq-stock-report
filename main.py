#!/usr/bin/env python3
"""知识星球内容爬取与总结工具 — CLI 入口。

用法:
  python3 main.py login                    # 手动扫码登录，保存 cookie
  python3 main.py crawl <专栏URL>           # 增量爬取（首次最多100篇）
  python3 main.py summary                  # 对最近爬取的内容进行 AI 总结
  python3 main.py stocks                   # 对最近爬取的内容提取股票投资机会
  python3 main.py all <专栏URL>             # 一键执行：增量爬取 + 股票提取 + 总结
"""

import argparse
import sys


def _log(msg: str) -> None:
    print(msg, flush=True)


def cmd_login() -> None:
    from auth import login
    login(headless=False)


def cmd_crawl(group_url: str, max_posts: int = 0) -> list[dict]:
    from crawler import crawl_group, get_group_id_from_url
    from storage import load_crawl_state, save_crawl_state, save_raw_data

    group_id = get_group_id_from_url(group_url)
    if not group_id:
        _log(f"错误：无法从 URL 中解析专栏 ID: {group_url}")
        _log("URL 格式应为: https://wx.zsxq.com/dweb2/index/group/<group_id>")
        sys.exit(1)

    _log(f"专栏 ID: {group_id}")

    state = load_crawl_state(group_id)
    since_topic_id = state.get("last_topic_id", "")
    is_first_run = not since_topic_id

    if is_first_run:
        if max_posts == 0:
            max_posts = 100
        _log(f"[首次运行] 最多抓取 {max_posts} 篇帖子")
    else:
        _log(f"[增量运行] 上次: {state.get('crawled_at', '未知')}，仅抓取新内容")
        max_posts = 0

    posts = crawl_group(group_url, max_posts=max_posts, since_topic_id=since_topic_id)

    if not posts:
        _log("没有新内容，无需更新。")
        return []

    from extractor import extract_structured_content
    cleaned = extract_structured_content(posts)

    save_raw_data(cleaned, group_name=group_id)
    save_crawl_state(group_id, cleaned[0], len(cleaned))

    from extractor import generate_stats
    stats = generate_stats(cleaned)
    _log(f"\n爬取完成！新增 {stats['total']} 篇，总赞 {stats['total_likes']}，总评论 {stats['total_comments']}")

    return cleaned


def _crawl_recent_for_report(group_url: str, group_id: str, limit: int = 100) -> list[dict]:
    """无新增内容时抓取最近 N 篇，用于定时报告兜底总结。"""
    from crawler import crawl_group
    from extractor import extract_structured_content, generate_stats
    from storage import save_raw_data

    _log(f"[无新增兜底] 抓取最近 {limit} 篇帖子用于本次总结")
    posts = crawl_group(group_url, max_posts=limit, since_topic_id="")
    if not posts:
        _log("错误：未能抓取到最近帖子。")
        return []

    cleaned = extract_structured_content(posts)
    save_raw_data(cleaned, group_name=group_id)

    stats = generate_stats(cleaned)
    _log(
        f"\n兜底抓取完成！最近 {stats['total']} 篇，"
        f"总赞 {stats['total_likes']}，总评论 {stats['total_comments']}"
    )
    return cleaned


def cmd_summary() -> None:
    from storage import load_latest_raw
    posts, filepath = load_latest_raw()
    if not posts:
        _log("错误：没有找到已爬取的数据。请先运行 crawl 命令。")
        sys.exit(1)

    _log(f"共 {len(posts)} 篇帖子，开始总结...")
    from summarizer import summarize_posts
    report = summarize_posts(posts)

    from storage import save_summary
    import re
    from pathlib import Path as PathLib
    group_name = ""
    if filepath:
        name_match = re.search(r"(.+)_\d{8}", PathLib(filepath).stem)
        if name_match:
            group_name = name_match.group(1)

    save_summary(report, group_name=group_name)
    _log("\n总结完成！")


def cmd_stocks() -> None:
    """从最近爬取的数据中提取股票投资机会。"""
    from storage import load_latest_raw
    posts, filepath = load_latest_raw()
    if not posts:
        _log("错误：没有找到已爬取的数据。请先运行 crawl 命令。")
        sys.exit(1)

    _log(f"共 {len(posts)} 篇帖子，开始提取股票机会...")
    from stock_extractor import extract_stock_opportunities
    report = extract_stock_opportunities(posts)

    # 打印报告
    _log("\n" + "=" * 60)
    _log("股票机会提取结果：")
    _log("=" * 60)
    print(report)

    # 保存
    import re
    from pathlib import Path as PathLib
    group_name = ""
    if filepath:
        name_match = re.search(r"(.+)_\d{8}", PathLib(filepath).stem)
        if name_match:
            group_name = name_match.group(1)

    from storage import save_stock_report
    save_stock_report(report, group_name=group_name)
    _log("\n股票机会提取完成！")

    # 如果启用了同花顺同步，自动执行
    _try_thssync_auto()


def _try_thssync_auto() -> None:
    """如果配置启用了同花顺同步，自动执行同步。"""
    try:
        config = _load_thssync_config()
        if not config.get("enabled", False):
            return
        _log("\n[同花顺同步] 配置已启用，开始同步...")
        from ths_sync import THSClient, format_sync_result, resolve_group_name
        from storage import load_latest_stock_data
        stocks, _ = load_latest_stock_data()
        if not stocks:
            _log("[同花顺同步] 无股票数据，跳过")
            return

        group_name = resolve_group_name(
            config.get("group_name_prefix", "知识星球"),
            config.get("group_name", "auto"),
        )
        client = THSClient(
            cookies_path=config.get("cookies_path", "cookies_ths.json"),
            score_threshold=config.get("score_threshold", 3.0),
            request_delay=config.get("request_delay", 0.3),
            group_name=group_name,
            create_group_if_missing=config.get("create_group_if_missing", True),
            also_add_to_watchlist=config.get("also_add_to_watchlist", False),
        )
        result = client.sync_stocks(stocks)
        _log("\n" + "=" * 50)
        _log("同花顺同步结果：")
        _log("=" * 50)
        print(format_sync_result(result))
    except Exception as e:
        _log(f"[同花顺同步] 自动同步失败（不影响主流程）: {e}")


def cmd_thssync(args) -> None:
    """将评分达标的股票同步到同花顺自选或指定分组。"""
    from ths_sync import THSClient, format_sync_result

    from storage import load_latest_stock_data
    stocks, _ = load_latest_stock_data()
    if not stocks:
        _log("错误：没有找到已提取的股票数据。请先运行 stocks 或 all 命令。")
        sys.exit(1)

    ths_config = _load_thssync_config()
    score_threshold = (
        args.score if args.score is not None
        else ths_config.get("score_threshold", 3.0)
    )
    from ths_sync import resolve_group_name
    group_name = resolve_group_name(
        ths_config.get("group_name_prefix", "知识星球"),
        ths_config.get("group_name", "auto"),
    )
    target = group_name if group_name else "默认自选股"

    _log(f"共 {len(stocks)} 只股票，评分阈值: >={score_threshold}，目标: {target}")
    _log("正在同步到同花顺...")

    client = THSClient(
        cookies_path=ths_config.get("cookies_path", "cookies_ths.json"),
        score_threshold=score_threshold,
        request_delay=ths_config.get("request_delay", 0.3),
        group_name=group_name,
        create_group_if_missing=ths_config.get("create_group_if_missing", True),
        also_add_to_watchlist=ths_config.get("also_add_to_watchlist", False),
    )
    result = client.sync_stocks(stocks)

    _log("\n" + "=" * 50)
    _log("同花顺同步结果：")
    _log("=" * 50)
    print(format_sync_result(result))

    return result


def _load_thssync_config() -> dict:
    """加载同花顺同步配置。"""
    import yaml
    from pathlib import Path
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
        return config.get("ths", {})
    return {}


def cmd_all(group_url: str) -> None:
    _log("=" * 50)
    _log("知识星球内容爬取与总结工具（增量模式）")
    _log("=" * 50)

    from crawler import get_group_id_from_url
    group_id = get_group_id_from_url(group_url) or "unknown"

    _log("\n[1/4] 检查登录状态...")
    from auth import load_cookies
    load_cookies()

    _log("\n[2/4] 爬取最新内容...")
    posts = cmd_crawl(group_url)
    report_scope = "新内容"

    if not posts:
        _log("\n没有新内容，将改为提取最近 100 篇帖子生成报告。")
        posts = _crawl_recent_for_report(group_url, group_id, limit=100)
        report_scope = "最近内容"
        if not posts:
            _log("\n没有可用于总结的内容，流程结束。")
            return

    # ── 第 3 步：提取股票机会 ──
    _log(f"\n[3/4] 对{report_scope} ({len(posts)} 篇) 提取股票机会...")
    from stock_extractor import extract_stock_opportunities
    from storage import save_stock_report

    stock_report = extract_stock_opportunities(posts)
    _log("\n" + "=" * 60)
    _log("股票机会提取结果：")
    _log("=" * 60)
    print(stock_report)
    save_stock_report(stock_report, group_name=group_id)

    # ── 第 4 步：完整 AI 总结 ──
    _log(f"\n[4/4] 对{report_scope} ({len(posts)} 篇) 生成完整 AI 总结...")
    from summarizer import summarize_posts
    from storage import save_summary

    report = summarize_posts(posts)
    save_summary(report, group_name=group_id)

    # ── 第 5 步：同花顺同步（可选，根据配置） ──
    _try_thssync_auto()

    _log("\n" + "=" * 50)
    _log("全部完成！请查看 data/ 目录下的输出文件。")
    _log("=" * 50)


def main():
    parser = argparse.ArgumentParser(
        description="知识星球内容爬取与总结工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 main.py login
  python3 main.py crawl https://wx.zsxq.com/dweb2/index/group/123456
  python3 main.py summary
  python3 main.py stocks
  python3 main.py all https://wx.zsxq.com/dweb2/index/group/123456
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    subparsers.add_parser("login", help="手动扫码登录，保存 cookie")

    crawl_parser = subparsers.add_parser("crawl", help="增量爬取指定专栏")
    crawl_parser.add_argument("url", help="专栏 URL")
    crawl_parser.add_argument("-n", "--max-posts", type=int, default=0, help="首次运行最大帖子数（默认100）")

    subparsers.add_parser("summary", help="对最近爬取的内容进行 AI 总结")

    subparsers.add_parser("stocks", help="对最近爬取的内容提取股票投资机会")

    thssync_parser = subparsers.add_parser("thssync", help="将重点推荐股票同步到同花顺自选股")
    thssync_parser.add_argument(
        "-s", "--score", type=float, default=None,
        help="推荐指数阈值（覆盖 config.yaml 设置）",
    )

    all_parser = subparsers.add_parser("all", help="一键执行完整流程")
    all_parser.add_argument("url", help="专栏 URL")

    args = parser.parse_args()

    if args.command == "login":
        cmd_login()
    elif args.command == "crawl":
        cmd_crawl(args.url, max_posts=args.max_posts)
    elif args.command == "summary":
        cmd_summary()
    elif args.command == "stocks":
        cmd_stocks()
    elif args.command == "thssync":
        cmd_thssync(args)
    elif args.command == "all":
        cmd_all(args.url)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
