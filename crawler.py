"""知识星球专栏内容爬取模块。

优先使用 requests + cookie 直接调用 ZSXQ API（轻量、适合 CI），
Playwright 仅作 cookie 过期时的登录回退。
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests
import yaml


def _log(msg: str) -> None:
    print(msg, flush=True)


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


# ── ZSXQ API 直连 ──

ZSXQ_API_BASE = "https://api.zsxq.com/v2"
ZSXQ_PAGE_DELAY_SECONDS = 15
ZSXQ_1059_COOLDOWN_SECONDS = 30
ZSXQ_1059_RETRIES = 2


class ZSXQApiError(RuntimeError):
    """知识星球 API 返回业务错误。"""

    def __init__(self, code: int, message: str = ""):
        self.code = code
        self.message = message
        super().__init__(f"ZSXQ API error {code}: {message}")


def _get_zsxq_headers(cookies: list[dict]) -> dict:
    """从 cookies 列表构建 API 请求头。"""
    # 提取 zsxq_access_token
    token = ""
    for c in cookies:
        if c.get("name") == "zsxq_access_token":
            token = c.get("value", "")
            break

    # 构建 Cookie 字符串
    cookie_str = "; ".join(
        f"{c.get('name', '')}={c.get('value', '')}" for c in cookies
        if c.get("name") and c.get("value")
    )

    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": cookie_str,
        "Referer": "https://wx.zsxq.com/",
        "Origin": "https://wx.zsxq.com",
        "x-version": "2.66.0",
    }


def _fetch_topics_page(
    group_id: str,
    cookies: list[dict],
    end_time: str = "",
    count: int = 20,
) -> Optional[dict]:
    """请求一页 topics 数据。

    Args:
        group_id: 专栏 ID。
        cookies: cookie 列表。
        end_time: 上一页最后一条的时间（用于翻页），空字符串=第一页。
        count: 每页条数。

    Returns:
        API 响应 JSON 或 None。
    """
    params = {
        "scope": "all",
        "count": count,
        "sort": "create_time",
    }
    if end_time:
        params["end_time"] = end_time

    url = f"{ZSXQ_API_BASE}/groups/{group_id}/topics?{urlencode(params, quote_via=quote)}"
    headers = _get_zsxq_headers(cookies)

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            _log(f"  [API] HTTP {resp.status_code}: {resp.text[:200]}")
            return None

        data = resp.json()
        if not data.get("succeeded", False):
            err_code = data.get("code", 0)
            err_msg = data.get("message", "")
            _log(f"  [API] 错误 code={err_code}: {err_msg}")
            if err_code == 1059:
                _log("  [API] 触发 1059（Cookie/频率异常），将由分页层冷却重试")
            raise ZSXQApiError(err_code, err_msg)

        return data
    except requests.RequestException as e:
        _log(f"  [API] 请求失败: {e}")
        return None


def _api_crawl(
    group_url: str,
    max_posts: int = 0,
    since_topic_id: str = "",
) -> Optional[list[dict]]:
    """通过 ZSXQ API 直连爬取帖子（纯 requests，无需浏览器）。

    这是爬取的主路径，适合本地和 CI 环境。
    """
    from auth import load_cookies

    cookies = load_cookies()
    group_id = get_group_id_from_url(group_url)
    if not group_id:
        _log(f"错误：无法解析专栏 ID: {group_url}")
        return None

    incremental = bool(since_topic_id)
    all_topics = []
    end_time = ""
    page = 0

    _log(f"[API 爬取] group_id={group_id} 增量={incremental}")

    while True:
        page += 1
        data = None
        for attempt in range(ZSXQ_1059_RETRIES + 1):
            try:
                data = _fetch_topics_page(group_id, cookies, end_time=end_time, count=20)
                break
            except ZSXQApiError as exc:
                if exc.code == 1059 and attempt < ZSXQ_1059_RETRIES:
                    _log(
                        f"  [API] 1059 冷却 {ZSXQ_1059_COOLDOWN_SECONDS}s 后重试 "
                        f"({attempt + 1}/{ZSXQ_1059_RETRIES})"
                    )
                    time.sleep(ZSXQ_1059_COOLDOWN_SECONDS)
                    continue
                if page == 1:
                    _log("[API] 首页触发 1059 且重试失败，爬取中止")
                    return None
                raise

        if data is None:
            if page == 1:
                _log("[API] 首页请求失败，爬取中止")
                return None
            break

        topics = data.get("resp_data", {}).get("topics", [])
        if not topics:
            _log(f"[API] 第{page}页无数据，停止翻页")
            break

        _log(f"  [API] 第{page}页: {len(topics)} 条")

        # 增量模式：检查是否遇到上次的最后一条
        for t in topics:
            tid = t.get("topic_id", "")
            if incremental and tid == since_topic_id:
                _log(f"  [API] 遇到上次最新帖，停止翻页")
                break
            all_topics.append(t)
        else:
            # 正常翻页
            last_topic = topics[-1]
            end_time = last_topic.get("create_time", "")
            # 检查是否已获取足够
            if max_posts > 0 and len(all_topics) >= max_posts:
                _log(f"  [API] 已获取 {len(all_topics)} 条，达到上限")
                break
            # 翻页间隔（API 限流防护）
            time.sleep(ZSXQ_PAGE_DELAY_SECONDS)
            continue

        # 遇到了 since_topic_id，停止
        break

    # 限数量
    if max_posts > 0 and len(all_topics) > max_posts:
        all_topics = all_topics[:max_posts]

    # 解析为统一格式
    posts = [_parse_topic(t) for t in all_topics]
    _log(f"[爬取完成] 共 {len(posts)} 篇帖子")
    return posts


def crawl_group(
    group_url: str,
    max_posts: int = 0,
    fetch_comments: bool = True,
    since_topic_id: str = "",
) -> list[dict]:
    """爬取专栏帖子。

    优先使用 API 直连方式（轻量、适合 CI），
    如果 cookie 不可用或用户要求，则回退到 Playwright。

    Args:
        group_url: 专栏 URL。
        max_posts: 最大帖子数（0=不限制）。
        fetch_comments: 是否获取评论（当前未实现）。
        since_topic_id: 增量爬取的起始 topic_id。

    Returns:
        帖子列表。
    """
    # API 直连路径
    posts = _api_crawl(group_url, max_posts=max_posts, since_topic_id=since_topic_id)
    if posts is not None:
        return posts

    # 回退：Playwright（仅用于 API 请求失败时的本地调试）
    _log("[回退] API 直连失败，尝试 Playwright...")
    return _playwright_crawl(group_url, max_posts=max_posts, since_topic_id=since_topic_id)


def _playwright_crawl(
    group_url: str,
    max_posts: int = 0,
    since_topic_id: str = "",
) -> list[dict]:
    """Playwright 浏览器爬取（回退方案）。"""
    from playwright.sync_api import sync_playwright
    from auth import get_authenticated_context

    incremental = bool(since_topic_id)
    api_responses = []

    def capture_response(response):
        url = response.url
        if "/groups/" in url and "/topics" in url and response.status == 200:
            try:
                body = response.json()
                if "resp_data" in body:
                    topics = body["resp_data"].get("topics", [])
                    api_responses.append({"url": url, "topics": topics})
                    _log(f"  [API] 捕获 {len(topics)} 条帖子数据")
            except Exception:
                pass

    with sync_playwright() as p:
        browser, context, page = get_authenticated_context(p, headless=True)
        page.on("response", capture_response)

        _log(f"[打开页面] {group_url}")
        page.goto(group_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(3_000)

        scroll_round = 0
        no_new_data_rounds = 0
        max_no_new_rounds = 5

        while True:
            if max_posts > 0:
                total = sum(len(r["topics"]) for r in api_responses)
                if total >= max_posts:
                    _log(f"[达到上限] {max_posts} 篇，停止加载")
                    break

            scroll_round += 1
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.5)

            try:
                load_btn = page.query_selector(':has-text("加载更多"), [class*="more"]')
                if load_btn and load_btn.is_visible():
                    load_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            current_total = sum(len(r["topics"]) for r in api_responses)
            prev_total = sum(len(r["topics"]) for r in api_responses[:-1]) if len(api_responses) > 1 else 0
            new_data = current_total > prev_total
            if scroll_round == 1 or new_data:
                no_new_data_rounds = 0
                _log(f"[滚动 {scroll_round}] {current_total} 篇")
            else:
                no_new_data_rounds += 1
                if no_new_data_rounds >= max_no_new_rounds:
                    _log("[完成] 无更多数据")
                    break

        browser.close()

    # 合并去重
    seen = set()
    all_topics = []
    for response_data in api_responses:
        for topic in response_data["topics"]:
            tid = topic.get("topic_id", "")
            if tid and tid not in seen:
                seen.add(tid)
                all_topics.append(topic)

    if incremental:
        cutoff_idx = None
        for i, t in enumerate(all_topics):
            if t.get("topic_id", "") == since_topic_id:
                cutoff_idx = i
                break
        if cutoff_idx is not None:
            all_topics = all_topics[:cutoff_idx]

    if max_posts > 0 and len(all_topics) > max_posts:
        all_topics = all_topics[:max_posts]

    posts = [_parse_topic(t) for t in all_topics]
    _log(f"[爬取完成] 共 {len(posts)} 篇帖子")
    return posts


# ── 解析 ──

def _parse_topic(topic: dict) -> dict:
    """将 API 返回的 topic JSON 解析为统一帖子格式。"""
    t_type = topic.get("type", "")
    topic_id = topic.get("topic_id", "")
    create_time = topic.get("create_time", "")

    talk = topic.get("talk", {}) or {}
    owner = talk.get("owner", {}) or {}
    author_name = owner.get("name", "未知")

    content = ""
    images = []

    if t_type == "talk":
        text = talk.get("text", "") or ""
        # 清洗富文本标签 <e type="..." ... />
        from urllib.parse import unquote as _unquote
        hashtags = re.findall(r'<e type="hashtag"[^>]*title="([^"]*)"', text)
        for ht in hashtags:
            tag_match = re.search(rf'<e type="hashtag"[^>]*title="{re.escape(ht)}"[^>]*/>', text)
            if tag_match:
                text = text.replace(tag_match.group(0), _unquote(ht))
        text = re.sub(r"<e\s[^>]*/>", "", text)
        content = text.strip()

        imgs = talk.get("images", []) or []
        for img in imgs:
            url = img.get("files", [{}])[0].get("url", "") if img.get("files") else ""
            if url:
                images.append(url)

        files = talk.get("files", []) or []
        for f in files:
            f_url = f.get("files", [{}])[0].get("url", "") if f.get("files") else ""
            if f_url:
                images.append(f_url)

    elif t_type == "q&a":
        question = topic.get("question", {}) or {}
        content = question.get("text", "") or ""

    title = content[:50].replace("\n", " ") if content else "无标题"
    if len(content) > 50:
        title += "..."

    likes = topic.get("likes_count", 0) or 0
    comments_count = topic.get("comments_count", 0) or 0
    reading_count = topic.get("reading_count", 0) or 0
    url = f"https://wx.zsxq.com/dweb2/index/topic/{topic_id}" if topic_id else ""

    time_str = ""
    if create_time:
        try:
            dt = datetime.fromisoformat(create_time.replace("Z", "+00:00"))
            time_str = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            time_str = create_time

    tags = []
    if t_type == "talk":
        tags_data = talk.get("topic_ids", []) or []
        tags = [t.get("title", "") for t in tags_data if t.get("title")]

    return {
        "topic_id": topic_id,
        "title": title,
        "author": author_name,
        "content": content,
        "time": time_str,
        "url": url,
        "images": images,
        "likes": likes,
        "comments_count": comments_count,
        "reading_count": reading_count,
        "tags": tags,
        "content_type": _detect_type(content, images),
        "comments": [],
    }


def _detect_type(content: str, images: list) -> str:
    if images and len(content) < 50:
        return "image"
    elif images and content:
        return "article_with_images"
    elif content:
        return "text"
    return "unknown"


def get_group_id_from_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    query_group_id = parse_qs(parsed.query).get("groupId", [""])[0]
    if query_group_id:
        return query_group_id

    match = re.search(r"/group/(\w+)", url)
    return match.group(1) if match else None
