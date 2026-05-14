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
    elif args.command == "all":
        cmd_all(args.url)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
