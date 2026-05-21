#!/usr/bin/env python3
"""个股深度研究模块。

搜索知识星球专栏内所有关于指定个股的帖子，综合分析后生成深度研究报告。
支持股票名称、代码、别名等多种搜索方式，并补充实时行情数据。
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

import yaml


def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


# ── 股票别名映射（常见简称 → 规范名称 + 代码） ──
_STOCK_ALIASES = {
    # 示例，后续可从接口自动补全
}


def search_stock_posts(
    posts: list[dict],
    stock_name: str,
    stock_code: str = "",
) -> list[dict]:
    """从帖子列表中搜索与指定个股相关的所有帖子。

    匹配策略（命中任一即保留）：
    1. 精确匹配股票名称（支持部分匹配，如"茅台"匹配"贵州茅台"）
    2. 精确匹配6位股票代码
    3. 匹配帖子中的 #标签（如 #拓斯达）

    Args:
        posts: 结构化帖子列表。
        stock_name: 股票名称（如"贵州茅台"、"茅台"均可）。
        stock_code: 6位股票代码（可选）。

    Returns:
        匹配的帖子列表，按时间降序排列。
    """
    matched = []
    name_lower = stock_name.lower().strip()
    code_clean = stock_code.strip()

    for post in posts:
        text = (
            post.get("title", "") + " " +
            post.get("content", "")
        ).lower()
        tags = [t.lower() for t in post.get("tags", [])]

        hit = False

        # 1. 股票名称匹配（部分匹配，"茅台"可匹配"贵州茅台"）
        if name_lower and name_lower in text:
            hit = True

        # 2. 股票代码匹配
        if code_clean and re.search(rf"\b{code_clean}\b", text):
            hit = True

        # 3. 标签匹配
        if name_lower and any(name_lower in tag for tag in tags):
            hit = True

        # 4. 评论区匹配
        if not hit:
            for comment in post.get("comments", []):
                comment_text = comment.get("content", "").lower()
                if name_lower and name_lower in comment_text:
                    hit = True
                    break
                if code_clean and re.search(rf"\b{code_clean}\b", comment_text):
                    hit = True
                    break

        if hit:
            matched.append(post)

    # 按时间降序排列（最新在前）
    matched.sort(key=lambda p: p.get("time", ""), reverse=True)
    return matched


def _resolve_stock_code(stock_name: str) -> str:
    """尝试通过腾讯API模糊搜索解析股票代码。

    Args:
        stock_name: 股票名称或代码。

    Returns:
        6位股票代码，无法解析返回空字符串。
    """
    # 如果已经是6位纯数字，直接返回
    if re.match(r"^\d{6}$", stock_name):
        return stock_name

    # 尝试通过腾讯搜索API获取代码
    try:
        import requests
        url = f"http://suggestion.gtimg.cn/data3.php?q={stock_name}&t=1"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        try:
            text = resp.content.decode("gbk")
        except (UnicodeDecodeError, UnicodeEncodeError):
            text = resp.text

        # 解析格式: v_hint="xxx~xxx~600519~贵州茅台~..."
        matches = re.findall(r"~(\d{6})~([^~]+)~", text)
        for code, name in matches:
            # 优先精确匹配
            if name.strip() == stock_name.strip():
                return code
        # 取第一个结果
        if matches:
            return matches[0][0]
    except Exception:
        pass

    # 腾讯 suggestion 偶发 502；东方财富搜索接口作为兜底。
    try:
        import requests
        url = "https://searchapi.eastmoney.com/api/suggest/get"
        params = {
            "input": stock_name,
            "type": "14",
            "token": "D43BF722C8E33F6000EF0CAF2F516D31",
        }
        resp = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        items = (
            data.get("QuotationCodeTable", {})
            .get("Data", [])
        )
        a_stocks = [i for i in items if i.get("Classify") == "AStock" and i.get("Code")]
        for item in a_stocks:
            if item.get("Name", "").strip() == stock_name.strip():
                return item.get("Code", "")
        if a_stocks:
            return a_stocks[0].get("Code", "")
    except Exception:
        pass

    return ""


def _get_neodata_financial_data(stock_name: str, stock_code: str) -> dict:
    """通过 NeoData 获取个股财务数据。

    Returns:
        财务数据字典，获取失败返回空字典。
    """
    if not stock_code:
        return {}

    try:
        import requests
        import uuid
        # NeoData 本地代理地址（默认端口 19000）
        config = _load_config()
        neodata_config = config.get("neodata", {})
        proxy_url = neodata_config.get("proxy_url", "http://localhost:19000/proxy/api")
        remote_url = "https://jprx.m.qq.com/aizone/skillserver/v1/proxy/teamrouter_neodata/query"

        # 构造查询
        query = f"{stock_name} {stock_code} 最新财报数据 营收 净利润 ROE EPS 估值"
        payload = {
            "channel": "neodata",
            "sub_channel": "qclaw",
            "query": query,
            "request_id": uuid.uuid4().hex,
            "data_type": "api",  # 仅获取结构化数据
            "se_params": {},
            "extra_params": {},
        }
        headers = {
            "Content-Type": "application/json",
            "Remote-URL": remote_url,
        }

        resp = requests.post(proxy_url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        if result.get("suc") and result.get("code") == "200":
            return result.get("data", {})
    except Exception as e:
        print(f"  [NeoData] 获取失败: {e}", flush=True)

    return {}


def _get_realtime_data(stock_code: str) -> dict:
    """获取个股实时行情数据。

    Returns:
        行情数据字典，获取失败返回空字典。
    """
    if not stock_code:
        return {}
    try:
        from price_fetcher import fetch_single_price
        return fetch_single_price(stock_code) or {}
    except Exception:
        return {}


def _resolve_group_id(group_id: str = "") -> str:
    """解析用于知识星球搜索的专栏 ID。"""
    if group_id:
        raw_group_id = str(group_id).strip()
        try:
            from crawler import get_group_id_from_url
            parsed = get_group_id_from_url(raw_group_id)
            if parsed:
                return parsed
        except Exception:
            pass
        return raw_group_id

    config = _load_config()
    zsxq_config = config.get("zsxq", {})
    configured_group_id = str(zsxq_config.get("group_id", "")).strip()
    if configured_group_id:
        return configured_group_id

    group_url = zsxq_config.get("group_url", "")
    if group_url:
        try:
            from crawler import get_group_id_from_url
            parsed = get_group_id_from_url(group_url)
            if parsed:
                return parsed
        except Exception:
            pass

    try:
        from storage import load_latest_raw
        _, fp = load_latest_raw()
        if fp:
            m = re.search(r"(\d+)", Path(fp).stem)
            if m:
                return m.group(1)
    except Exception:
        pass

    return ""


def _search_zsxq_posts_for_research(
    stock_name: str,
    stock_code: str = "",
    group_id: str = "",
    max_posts: int = 100,
) -> list[dict]:
    """调用知识星球搜索接口，获取指定个股相关帖子。

    搜索接口比“抓最近 N 篇后本地匹配”覆盖面更广，可以命中更早的历史帖子。
    """
    try:
        import requests
        from auth import load_cookies
        from crawler import _get_zsxq_headers, _parse_topic

        resolved_group_id = _resolve_group_id(group_id)
        if not resolved_group_id:
            print("  [搜索] 无法确定专栏 ID", flush=True)
            return []

        cookies = load_cookies()
        if not cookies:
            print("  [搜索] 无法加载 Cookie", flush=True)
            return []

        headers = _get_zsxq_headers(cookies)
        keywords = []
        if stock_name.strip():
            keywords.append(stock_name.strip())
        if stock_code.strip() and stock_code.strip() not in keywords:
            keywords.append(stock_code.strip())

        seen_topic_ids = set()
        results = []
        per_page = 20

        for keyword in keywords:
            index = ""
            fetched_for_keyword = 0
            while len(results) < max_posts:
                params = {
                    "keyword": keyword,
                    "count": per_page,
                    "scope": "all",
                }
                if index:
                    params["index"] = index

                url = (
                    f"https://api.zsxq.com/v2/search/groups/{resolved_group_id}/topics?"
                    f"{urlencode(params, quote_via=quote)}"
                )
                data = None
                for attempt in range(3):
                    resp = requests.get(url, headers=headers, timeout=30)
                    if resp.status_code != 200:
                        err = f"HTTP {resp.status_code}: {resp.text[:120]}"
                    else:
                        data = resp.json()
                        if data.get("succeeded", False):
                            err = ""
                            break
                        err = data.get("message", "") or f"code={data.get('code', '')}"

                    if attempt < 2:
                        wait_seconds = 5 * (attempt + 1)
                        print(f"  [搜索] {keyword} 第{attempt + 1}次失败({err})，{wait_seconds}s 后重试", flush=True)
                        time.sleep(wait_seconds)
                    else:
                        print(f"  [搜索] {keyword} API 失败: {err}", flush=True)

                if not data or not data.get("succeeded", False):
                    break

                resp_data = data.get("resp_data", {}) or {}
                topics = resp_data.get("topics", []) or []
                if not topics:
                    break

                for topic in topics:
                    topic_id = str(topic.get("topic_id", ""))
                    if topic_id and topic_id not in seen_topic_ids:
                        seen_topic_ids.add(topic_id)
                        results.append(_parse_topic(topic))
                        fetched_for_keyword += 1
                        if len(results) >= max_posts:
                            break

                next_index = resp_data.get("index", "")
                if not next_index or str(next_index) == str(index) or len(topics) < per_page:
                    break
                index = str(next_index)
                time.sleep(2)

            print(f"  [搜索] 关键词「{keyword}」命中 {fetched_for_keyword} 篇", flush=True)

        results.sort(key=lambda p: p.get("time", ""), reverse=True)
        return results
    except Exception as e:
        print(f"  [搜索] API 搜索失败: {e}", flush=True)
        return []


def _format_relevant_posts(posts: list[dict], stock_name: str) -> str:
    """格式化相关帖子内容，供AI分析。

    对每篇帖子：
    - 高亮标注股票名称出现的位置
    - 保留完整内容
    - 包含评论中的相关讨论
    """
    parts = []
    for i, post in enumerate(posts, 1):
        section = [f"【帖子 {i}】"]
        if post.get("title"):
            section.append(f"标题: {post['title']}")
        section.append(f"作者: {post.get('author', '未知')}")
        section.append(f"时间: {post.get('time', '未知')}")
        section.append(f"点赞: {post.get('likes', 0)} | 评论: {post.get('comments_count', 0)}")

        content = post.get("content", "")
        section.append(f"\n内容:\n{content}")

        # 包含相关评论
        relevant_comments = []
        name_lower = stock_name.lower()
        for comment in post.get("comments", []):
            comment_text = comment.get("content", "")
            if name_lower in comment_text.lower():
                relevant_comments.append(
                    f"  → {comment.get('author', '匿名')}: {comment_text[:300]}"
                )
        if relevant_comments:
            section.append(f"\n相关评论:")
            section.extend(relevant_comments[:5])

        parts.append("\n".join(section))

    return "\n\n---\n\n".join(parts)


def _crawl_posts_for_research(max_posts: int = 150) -> list[dict]:
    """直接从 ZSXQ API 爬取帖子用于个股搜索。

    不依赖本地 raw 文件，始终从 API 获取最新数据。
    如果爬取失败则回退到 load_latest_raw()。

    Args:
        max_posts: 最多爬取帖子数。

    Returns:
        帖子列表，失败返回空列表。
    """
    try:
        from auth import load_cookies
        from crawler import _parse_topic, get_group_id_from_url, _get_zsxq_headers
        from urllib.parse import quote, urlencode
        import requests, time

        cookies = load_cookies()
        if not cookies:
            print("  [爬取] 无法加载 Cookie", flush=True)
            raise RuntimeError("Cookie 为空")

        headers = _get_zsxq_headers(cookies)

        # 从配置获取专栏 URL
        config_path = Path(__file__).parent / "config.yaml"
        group_id = ""
        if config_path.exists():
            with open(config_path, "r") as f:
                cfg = yaml.safe_load(f) or {}
            group_url = cfg.get("zsxq", {}).get("group_url", "")
            if group_url:
                group_id = get_group_id_from_url(group_url)

        if not group_id:
            print("  [爬取] 无法解析专栏 ID，尝试从最新 raw 文件名推导...", flush=True)
            from storage import load_latest_raw
            posts_fallback, fp = load_latest_raw()
            if fp:
                import re
                m = re.search(r"(\d+)", Path(fp).stem)
                if m:
                    group_id = m.group(1)
            if not group_id:
                raise RuntimeError("无法确定专栏 ID")

        print(f"  [爬取] 专栏 {group_id}，目标 {max_posts} 篇...", flush=True)

        all_topics = []
        end_time = ""
        pages = min(max_posts // 20 + 1, 15)  # 最多 15 页

        for page_num in range(pages):
            params = {"scope": "all", "count": 20}
            if end_time:
                params["end_time"] = end_time
            url = f"https://api.zsxq.com/v2/groups/{group_id}/topics?{urlencode(params, quote_via=quote)}"

            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"  [爬取] 第{page_num+1}页 HTTP {resp.status_code}，停止", flush=True)
                break

            data = resp.json()
            if not data.get("succeeded"):
                print(f"  [爬取] 第{page_num+1}页 API 失败: {data.get('message', '')}", flush=True)
                break

            topics = data.get("resp_data", {}).get("topics", [])
            if not topics:
                print(f"  [爬取] 第{page_num+1}页无数据，停止", flush=True)
                break

            for t in topics:
                all_topics.append(_parse_topic(t))

            last_topic = topics[-1]
            end_time = last_topic.get("create_time", "")

            if len(all_topics) >= max_posts:
                all_topics = all_topics[:max_posts]
                break

            time.sleep(3)  # 防限流

        posts = all_topics
        print(f"  [爬取] 完成: {len(posts)} 篇", flush=True)
        return posts

    except Exception as e:
        print(f"  [爬取] API 直连失败: {e}, 回退到本地文件...", flush=True)
        from storage import load_latest_raw
        posts_fallback, _ = load_latest_raw()
        return posts_fallback or []


def generate_deep_research(
    stock_name: str,
    stock_code: str = "",
    posts: Optional[list[dict]] = None,
    send_email: bool = True,
    group_id: str = "",
) -> str:
    """生成个股深度研究报告。

    流程：
    1. 解析股票代码（如果未提供）
    2. 优先调用知识星球搜索接口获取历史相关帖子，失败后回退到最近帖子本地匹配
    3. 获取实时行情数据
    4. 获取 NeoData 财务数据
    5. AI 综合分析生成深度报告
    6. 保存报告
    7. 可选：发送邮件

    Args:
        stock_name: 股票名称（如"华亚智能"）。
        stock_code: 股票代码（可选，自动解析）。
        posts: 帖子数据（不传则从 API 爬取）。
        send_email: 是否发送邮件通知。
        group_id: 专栏ID（可选，用于加载特定专栏的数据）。

    Returns:
        Markdown 格式的深度研究报告。
    """
    print(f"\n{'='*50}", flush=True)
    print(f"个股深度研究: {stock_name}", flush=True)
    print(f"{'='*50}", flush=True)

    # ── 1. 解析股票代码 ──
    if not stock_code:
        print(f"[1/6] 解析股票代码...", flush=True)
        stock_code = _resolve_stock_code(stock_name)
        if stock_code:
            print(f"  → 代码: {stock_code}", flush=True)
        else:
            print(f"  → 未能解析代码，仅按名称搜索", flush=True)
    else:
        print(f"[1/6] 股票代码: {stock_code}", flush=True)

    # ── 2. 搜索/加载帖子数据 ──
    if posts is None:
        print(f"[2/6] 搜索知识星球相关帖子「{stock_name}」...", flush=True)
        matched_posts = _search_zsxq_posts_for_research(
            stock_name,
            stock_code=stock_code,
            group_id=group_id,
            max_posts=120,
        )
        if not matched_posts:
            print("  → 搜索接口无结果，回退到抓取最近帖子后本地匹配...", flush=True)
            posts = _crawl_posts_for_research(max_posts=200)
            if not posts:
                return _empty_research(
                    stock_name,
                    "知识星球搜索和 API 爬取均失败，且没有本地缓存数据。请先运行 crawl 命令或检查 Cookie。"
                )
            print(f"  → 共 {len(posts)} 篇帖子，搜索匹配中...", flush=True)
            matched_posts = search_stock_posts(posts, stock_name, stock_code)
        else:
            print(f"  → 搜索接口匹配 {len(matched_posts)} 篇", flush=True)
    else:
        print(f"[2/6] 使用传入数据搜索「{stock_name}」...", flush=True)
        print(f"  → 共 {len(posts)} 篇帖子，搜索匹配中...", flush=True)
        matched_posts = search_stock_posts(posts, stock_name, stock_code)

    if not matched_posts:
        return _empty_research(
            stock_name,
            f"专栏内未找到与「{stock_name}」相关的帖子。"
        )
    print(f"  → 最终用于分析: {len(matched_posts)} 篇", flush=True)

    # ── 3. 获取实时行情 ──
    print(f"[3/6] 获取实时行情...", flush=True)
    realtime = _get_realtime_data(stock_code)
    if realtime:
        print(f"  → 当前价: {realtime.get('price', '-')} | "
              f"PE: {realtime.get('pe', '-')} | "
              f"市值: {realtime.get('market_cap_yi', '-')}亿", flush=True)
    else:
        print(f"  → 未能获取行情数据", flush=True)

    # ── 4. 获取 NeoData 财务数据 ──
    print(f"[4/6] 获取 NeoData 财务数据...", flush=True)
    neodata = _get_neodata_financial_data(stock_name, stock_code)
    if neodata:
        print(f"  → 已获取财务数据", flush=True)
    else:
        print(f"  → 未能获取 NeoData 数据", flush=True)

    # ── 5. AI 深度分析 ──
    print(f"[5/6] AI 深度分析 ({len(matched_posts)} 篇帖子)...", flush=True)
    from summarizer import get_client
    client, model, provider = get_client()
    print(f"  AI: {provider} ({model})", flush=True)

    posts_text = _format_relevant_posts(matched_posts, stock_name)
    realtime_section = _format_realtime_section(realtime, stock_name, stock_code)
    neodata_section = _format_neodata_section(neodata, stock_name, stock_code)

    report = _ai_deep_research(
        client, stock_name, stock_code, posts_text, realtime_section, neodata_section, len(matched_posts)
    )
    print(f"  → 报告生成完成", flush=True)

    # ── 6. 保存 & 发送 ──
    print(f"[6/6] 保存报告...", flush=True)
    full_report = _wrap_report(stock_name, stock_code, report, len(matched_posts), realtime)

    from storage import save_research_report
    filepath = save_research_report(full_report, stock_name=stock_name)
    print(f"  → 已保存: {filepath}", flush=True)

    if send_email:
        try:
            from email_sender import send_report_notification
            send_report_notification(
                filepath,
                subject_override=f"🔍 个股深度研究: {stock_name} ({datetime.now().strftime('%Y-%m-%d')})",
            )
            print(f"  → 邮件已发送", flush=True)
        except Exception as e:
            print(f"  → 邮件发送失败（不影响报告）: {e}", flush=True)

    print(f"\n{'='*50}", flush=True)
    print(f"深度研究完成: {stock_name}", flush=True)
    print(f"{'='*50}", flush=True)

    return full_report


def _format_neodata_section(neodata: dict, stock_name: str, stock_code: str) -> str:
    """格式化 NeoData 财务数据供 AI 参考。"""
    if not neodata:
        return "（无 NeoData 财务数据）"

    parts = [f"## {stock_name}({stock_code}) NeoData 财务数据"]

    # 解析 NeoData API 返回结构
    api_data = neodata.get("apiData", {})
    if api_data:
        # 添加实体信息
        entities = api_data.get("entity", [])
        if entities:
            parts.append(f"\n### 标的信息")
            for ent in entities[:3]:
                parts.append(f"- {ent.get('name', '')} ({ent.get('code', '')})")

        # 添加 API 召回数据
        api_recall = api_data.get("apiRecall", [])
        if api_recall:
            parts.append(f"\n### 财务数据详情")
            for item in api_recall[:5]:  # 最多取5个数据块
                item_type = item.get("type", "")
                content = item.get("content", "")
                if content:
                    # 截取前2000字符避免过长
                    content_preview = content[:2000] if len(content) > 2000 else content
                    parts.append(f"\n**{item_type}:**")
                    parts.append(content_preview)

    return "\n".join(parts)


def _format_realtime_section(realtime: dict, stock_name: str, stock_code: str) -> str:
    """格式化实时行情数据供 AI 参考。"""
    if not realtime:
        return "（无实时行情数据）"

    parts = [f"## {stock_name}({stock_code}) 实时行情"]
    parts.append(f"- 当前价格: {realtime.get('price', '-')} 元")
    parts.append(f"- 涨跌幅: {realtime.get('change_pct', '-')}%")
    parts.append(f"- 市盈率(PE): {realtime.get('pe', '-')}")
    parts.append(f"- 市净率(PB): {realtime.get('pb', '-')}")
    parts.append(f"- 总市值: {realtime.get('market_cap_yi', '-')} 亿元")
    parts.append(f"- 今日最高: {realtime.get('high', '-')} 元")
    parts.append(f"- 今日最低: {realtime.get('low', '-')} 元")
    parts.append(f"- 换手率: {realtime.get('turnover_rate', '-')}%")
    return "\n".join(parts)


def _ai_deep_research(
    client,
    stock_name: str,
    stock_code: str,
    posts_text: str,
    realtime_section: str,
    neodata_section: str,
    post_count: int,
) -> str:
    """调用 AI 生成深度研究报告。"""

    system = (
        "你是一位资深的A股投研分析师，擅长从碎片化的专家观点、调研纪要和讨论中"
        "提炼出系统性的投资逻辑。你的报告结论先行、数据支撑、逻辑清晰，"
        "不预测股价走势，不给买卖建议，只帮投资者看清全貌。"
    )

    prompt = f"""请基于以下知识星球专栏中关于「{stock_name}」的所有帖子，结合实时行情数据和 NeoData 财务数据，生成一份深度研究报告。

## 实时行情参考
{realtime_section}

## NeoData 财务数据
{neodata_section}

## 专栏相关帖子（共 {post_count} 篇）
{posts_text}

---

请按以下结构输出报告，每个部分都要有实质内容，不要泛泛而谈：

## 一、核心结论
用3-5句话概括对{stock_name}的核心判断，必须包含：
- 专栏讨论的共识方向（看多/看空/分歧）
- 最关键的投资逻辑
- 最大的风险点

## 二、投资逻辑梳理
将所有帖子中提到的投资逻辑按优先级排列，每条逻辑需要：
- 逻辑内容（一句话说清楚）
- 逻辑来源（引用具体帖子编号和作者）
- 逻辑强度（强/中/弱 — 基于被讨论频次和论据充分程度）

分类为：
1. **核心逻辑**（最被认可的主线逻辑）
2. **辅助逻辑**（支撑性或次级逻辑）
3. **潜在逻辑**（个别专家提出的尚未被验证的逻辑）

## 三、关键数据与量化参考
提取所有帖子中提到的具体数据，包括但不限于：
- 目标价/目标市值
- 业绩预测（营收、净利润、增速）
- 估值参考（PE、PB、PS等）
- 订单/产能/市占率等经营数据
- 每项数据标注来源帖子

## 四、产业链定位
- {stock_name}在产业链中的位置（上游/中游/下游）
- 核心客户和供应商关系
- 竞争对手和差异化优势
- 产业链变化对{stock_name}的影响

## 五、板块与赛道分析
- {stock_name}所属板块的整体景气度
- 板块内其他标的对比（如有提及）
- 政策/技术/需求等驱动力分析
- 板块轮动位置判断

## 六、风险与不确定性
按严重程度排列所有提及的风险因素：
1. **重大风险**（可能导致逻辑根本性改变）
2. **中等风险**（可能影响短期表现）
3. **潜在风险**（需要持续跟踪的不确定因素）

每项风险需要：
- 风险描述
- 发生概率评估（高/中/低）
- 影响程度评估（高/中/低）
- 来源帖子

## 七、专栏讨论时间线
按时间顺序列出所有相关帖子的核心观点，展示讨论的演变过程。
格式：日期 | 作者 | 核心观点 | 逻辑变化标注

## 八、综合评价
- 逻辑清晰度评分（1-10）
- 信息充分度评分（1-10）
- 专栏共识度评分（1-10）
- 短期关注重点（1-3个关键催化/风险事件）
- 中长期跟踪要点

注意事项：
- 所有结论必须有帖子内容支撑，不要凭空推断
- 如有矛盾观点，客观呈现而非选择性忽略
- 量化数据务必标注来源，区分"作者观点"和"客观数据"
- 不预测股价走势，不给买卖建议
"""

    return client.create(system=system, prompt=prompt, max_tokens=6000)


def _wrap_report(
    stock_name: str,
    stock_code: str,
    ai_report: str,
    post_count: int,
    realtime: dict,
) -> str:
    """包装完整的深度研究报告。"""
    code_str = f"({stock_code})" if stock_code else ""
    price_str = ""
    if realtime:
        price = realtime.get("price", "-")
        mc = realtime.get("market_cap_yi", "-")
        pe = realtime.get("pe", "-")
        change = realtime.get("change_pct", "-")
        price_str = (
            f"\n> 实时行情: {price}元 | 涨跌幅: {change}% | "
            f"PE: {pe} | 市值: {mc}亿"
        )

    lines = [
        f"# 🔍 个股深度研究: {stock_name}{code_str}",
        "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 数据来源: 知识星球专栏 ({post_count} 篇相关帖子){price_str}",
        "",
        ai_report,
        "",
        "---",
        "",
        "*免责声明：本报告由AI基于知识星球专栏内容自动生成，仅供参考，不构成任何投资建议。"
        "投资有风险，入市需谨慎。*",
    ]
    return "\n".join(lines)


def _empty_research(stock_name: str, reason: str) -> str:
    """无数据时的空报告。"""
    return (
        f"# 🔍 个股深度研究: {stock_name}\n\n"
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"**无法生成报告**: {reason}\n"
    )
