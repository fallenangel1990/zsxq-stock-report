#!/usr/bin/env python3
"""知识星球内容爬取与总结工具 — CLI 入口。

用法:
  python3 main.py login                    # 手动扫码登录，保存 cookie
  python3 main.py crawl <专栏URL>           # 增量爬取（默认最多 300 条）
  python3 main.py summary                  # 对最近爬取的内容进行 AI 总结
  python3 main.py stocks                   # 对最近爬取的内容提取股票投资机会
  python3 main.py research <股票名称>       # 个股深度研究（搜索专栏内所有相关信息）
  python3 main.py all <专栏URL>             # 一键执行：增量爬取 + 股票提取 + 总结
"""

import argparse
import sys

DEFAULT_INCREMENTAL_MAX_POSTS = 300


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

    effective_max_posts = max_posts
    if max_posts > 0:
        since_topic_id = ""
        _log(f"[手动限量] 抓取最近 {max_posts} 篇帖子（忽略上次增量位置）")
    else:
        effective_max_posts = DEFAULT_INCREMENTAL_MAX_POSTS
        if is_first_run:
            _log(f"[首次运行] 增量上限 {effective_max_posts} 条，将抓取最近内容作为起点")
        else:
            _log(
                f"[增量运行] 上次: {state.get('crawled_at', '未知')}，"
                f"抓取上次记录之后的新内容（最多 {effective_max_posts} 条）"
            )

    posts = crawl_group(group_url, max_posts=effective_max_posts, since_topic_id=since_topic_id)

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
            _log("[同花顺同步] 配置未启用，跳过（config.yaml ths.enabled = false）")
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

    if getattr(args, "strict", False) and result.get("status") != "success":
        sys.exit(1)

    return result


def cmd_sectors(args) -> None:
    """捕获 A 股主流板块异动并生成复盘报告。"""
    from sector_monitor import capture_sector_signals
    from storage import save_sector_report

    _log(
        f"开始捕获板块信号：mode={args.mode}, board_type={args.board_type}, "
        f"top={args.top}, ai={not args.no_ai}"
    )
    report, signals = capture_sector_signals(
        mode=args.mode,
        top_n=args.top,
        board_type=args.board_type,
        with_ai=not args.no_ai,
    )

    _log("\n" + "=" * 60)
    _log("板块异动/复盘结果：")
    _log("=" * 60)
    print(report)

    filepath = save_sector_report(report, mode=args.mode)

    if args.email:
        try:
            from email_sender import send_report_notification
            subject = (
                "📈 A股盘后板块主力建仓复盘"
                if args.mode == "review"
                else "⚡ A股盘中板块异动信号"
            )
            send_report_notification(filepath, subject_override=subject)
            _log("邮件已发送")
        except Exception as e:
            _log(f"邮件发送失败（不影响报告）: {e}")

    _log(f"\n捕获完成：{len(signals)} 个板块信号")


def cmd_market(args) -> None:
    """监控大盘和主流板块，生成建仓/加仓信号。"""
    from sector_monitor import capture_market_signals
    from storage import save_market_signal_report

    _log(
        f"开始监控大盘与主流板块：mode={args.mode}, board_type={args.board_type}, "
        f"top={args.top}, ai={not args.no_ai}"
    )
    report, market, signals = capture_market_signals(
        mode=args.mode,
        top_n=args.top,
        board_type=args.board_type,
        with_ai=not args.no_ai,
    )

    _log("\n" + "=" * 60)
    _log("大盘与板块建仓/加仓信号：")
    _log("=" * 60)
    print(report)

    filepath = save_market_signal_report(report, mode=args.mode)

    if args.email:
        try:
            from email_sender import send_report_notification
            send_report_notification(
                filepath,
                subject_override=f"📊 A股大盘与板块信号：{market.get('level', '未知')}",
            )
            _log("邮件已发送")
        except Exception as e:
            _log(f"邮件发送失败（不影响报告）: {e}")

    _log(f"\n监控完成：大盘={market.get('level')}，板块信号={len(signals)} 个")


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


def cmd_research(
    stock_name: str,
    stock_code: str = "",
    data_file: str = "",
    group_id: str = "",
) -> None:
    """对指定个股生成深度研究报告。"""
    from research import generate_deep_research
    
    posts = None
    if data_file:
        import json
        from pathlib import Path
        filepath = Path(data_file)
        if not filepath.exists():
            _log(f"错误：数据文件不存在: {data_file}")
            sys.exit(1)
        posts = json.loads(filepath.read_text(encoding="utf-8"))
        _log(f"已加载数据文件: {data_file} ({len(posts)} 篇帖子)")
    
    generate_deep_research(
        stock_name,
        stock_code=stock_code,
        posts=posts,
        send_email=True,
        group_id=group_id,
    )


def cmd_all(group_url: str, max_posts: int = 0) -> None:
    _log("=" * 50)
    _log("知识星球内容爬取与总结工具（增量模式）")
    _log("=" * 50)

    from crawler import get_group_id_from_url
    group_id = get_group_id_from_url(group_url) or "unknown"

    _log("\n[1/4] 检查登录状态...")
    from auth import load_cookies
    load_cookies()

    _log("\n[2/4] 爬取最新内容...")
    posts = cmd_crawl(group_url, max_posts=max_posts)
    report_scope = "新内容"

    if not posts:
        _log("\n没有新内容，流程结束。")
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
  python3 main.py sectors --mode review
  python3 main.py sectors --mode intraday --no-ai
  python3 main.py market --mode intraday
  python3 main.py research 华亚智能
  python3 main.py research 拓斯达 -c 300607
  python3 main.py all https://wx.zsxq.com/dweb2/index/group/123456
  python3 main.py all https://wx.zsxq.com/dweb2/index/group/123456 --max-posts 50
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    subparsers.add_parser("login", help="手动扫码登录，保存 cookie")

    crawl_parser = subparsers.add_parser("crawl", help="增量爬取指定专栏")
    crawl_parser.add_argument("url", help="专栏 URL")
    crawl_parser.add_argument(
        "-n", "--max-posts", type=int, default=0,
        help="最大帖子数（默认0=增量模式最多300条；填N=抓最近N条并忽略上次位置）",
    )

    subparsers.add_parser("summary", help="对最近爬取的内容进行 AI 总结")

    subparsers.add_parser("stocks", help="对最近爬取的内容提取股票投资机会")

    research_parser = subparsers.add_parser("research", help="个股深度研究：搜索专栏内所有关于该个股的信息")
    research_parser.add_argument("name", help="股票名称（如 华亚智能）")
    research_parser.add_argument("-c", "--code", default="", help="股票代码（可选，自动解析）")
    research_parser.add_argument("-f", "--file", default="", help="数据文件路径（可选，不指定则使用最新爬取的数据）")
    research_parser.add_argument("-g", "--group-id", default="", help="知识星球专栏ID或搜索URL（可选）")

    thssync_parser = subparsers.add_parser("thssync", help="将重点推荐股票同步到同花顺自选股")
    thssync_parser.add_argument(
        "-s", "--score", type=float, default=None,
        help="推荐指数阈值（覆盖 config.yaml 设置）",
    )
    thssync_parser.add_argument(
        "--strict", action="store_true",
        help="同步未成功时返回非零退出码（用于 CI）",
    )

    sectors_parser = subparsers.add_parser("sectors", help="捕获A股主流板块盘中异动/盘后建仓复盘")
    sectors_parser.add_argument(
        "-m", "--mode",
        choices=["intraday", "review"],
        default="review",
        help="intraday=盘中异动，review=盘后复盘（默认）",
    )
    sectors_parser.add_argument(
        "-t", "--top",
        type=int,
        default=12,
        help="输出板块数量（默认12）",
    )
    sectors_parser.add_argument(
        "--board-type",
        choices=["all", "industry", "concept"],
        default="all",
        help="板块范围：行业/概念/全部（默认all）",
    )
    sectors_parser.add_argument("--no-ai", action="store_true", help="只输出规则模型报告，不调用AI解读")
    sectors_parser.add_argument("--email", action="store_true", help="生成后发送邮件")

    market_parser = subparsers.add_parser("market", help="监控大盘和主流板块，输出建仓/加仓信号")
    market_parser.add_argument(
        "-m", "--mode",
        choices=["intraday", "review"],
        default="intraday",
        help="intraday=盘中监控，review=盘后复盘（默认intraday）",
    )
    market_parser.add_argument(
        "-t", "--top",
        type=int,
        default=10,
        help="输出板块信号数量（默认10）",
    )
    market_parser.add_argument(
        "--board-type",
        choices=["all", "industry", "concept"],
        default="all",
        help="板块范围：行业/概念/全部（默认all）",
    )
    market_parser.add_argument("--no-ai", action="store_true", help="只输出规则模型信号，不调用AI解读")
    market_parser.add_argument("--email", action="store_true", help="生成后发送邮件")

    all_parser = subparsers.add_parser("all", help="一键执行完整流程")
    all_parser.add_argument("url", help="专栏 URL")
    all_parser.add_argument(
        "-n", "--max-posts", type=int, default=0,
        help="最大帖子数（默认0=增量模式最多300条；填N=抓最近N条并忽略上次位置）",
    )

    args = parser.parse_args()

    if args.command == "login":
        cmd_login()
    elif args.command == "crawl":
        cmd_crawl(args.url, max_posts=args.max_posts)
    elif args.command == "summary":
        cmd_summary()
    elif args.command == "stocks":
        cmd_stocks()
    elif args.command == "research":
        cmd_research(args.name, stock_code=args.code, data_file=args.file, group_id=args.group_id)
    elif args.command == "thssync":
        cmd_thssync(args)
    elif args.command == "sectors":
        cmd_sectors(args)
    elif args.command == "market":
        cmd_market(args)
    elif args.command == "all":
        cmd_all(args.url, max_posts=args.max_posts)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
