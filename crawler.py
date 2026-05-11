"""知识星球专栏内容爬取模块。

通过 Playwright 复用登录态，拦截 API 响应获取结构化数据。
比 DOM 解析更可靠，直接拿到 ZSXQ 后端返回的 JSON。
"""

import json
import random
import sys
import time
from pathlib import Path
from typing import Optional

import yaml


def _log(msg: str) -> None:
    print(msg, flush=True)


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def crawl_group(
    group_url: str,
    max_posts: int = 0,
    fetch_comments: bool = True,
    since_topic_id: str = "",
) -> list[dict]:
    """爬取专栏帖子（通过拦截 ZSXQ API 获取结构化数据）。"""
    from playwright.sync_api import sync_playwright
    from auth import get_authenticated_context

    config = load_config()
    crawler_config = config.get("crawler", {})
    if max_posts == 0:
        max_posts = crawler_config.get("max_posts", 0)

    group_id = get_group_id_from_url(group_url)
    incremental = bool(since_topic_id)

    api_responses = []

    def capture_response(response):
        """拦截 API 响应，提取 topics 数据。"""
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

        # 监听 API 响应
        page.on("response", capture_response)

        _log(f"[打开页面] {group_url}")
        page.goto(group_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(3_000)

        # 下载滚动加载（触发 API 请求）
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
            time.sleep(random.uniform(1.5, 2.5))

            # 尝试点击加载更多
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
                progress = f"{current_total} 篇"
                if max_posts:
                    progress += f" / 目标 {max_posts}"
                _log(f"[滚动 {scroll_round}] {progress}")
            else:
                no_new_data_rounds += 1
                _log(f"[滚动 {scroll_round}] 等待新数据 {no_new_data_rounds}/{max_no_new_rounds}")
                if no_new_data_rounds >= max_no_new_rounds:
                    _log("[完成] 无更多数据")
                    break

        browser.close()

    # 合并去重 API 响应中的 topics
    seen = set()
    all_topics = []
    for response_data in api_responses:
        for topic in response_data["topics"]:
            tid = topic.get("topic_id", "")
            if tid and tid not in seen:
                seen.add(tid)
                all_topics.append(topic)

    _log(f"[API] 共获取 {len(all_topics)} 条唯一帖子")

    # 增量模式：只保留 since_topic_id 之前的新帖
    if incremental and all_topics:
        cutoff_idx = None
        for i, t in enumerate(all_topics):
            if t.get("topic_id", "") == since_topic_id:
                cutoff_idx = i
                break
        if cutoff_idx is not None:
            all_topics = all_topics[:cutoff_idx]
            _log(f"[增量] 过滤后剩余 {len(all_topics)} 篇新帖")

    # 限数量
    if max_posts > 0 and len(all_topics) > max_posts:
        all_topics = all_topics[:max_posts]

    # 解析为统一格式
    posts = [_parse_topic(t) for t in all_topics]

    _log(f"[爬取完成] 共 {len(posts)} 篇帖子")
    return posts


def _parse_topic(topic: dict) -> dict:
    """将 API 返回的 topic JSON 解析为统一帖子格式。"""
    t_type = topic.get("type", "")
    topic_id = topic.get("topic_id", "")
    create_time = topic.get("create_time", "")

    # 作者信息
    talk = topic.get("talk", {}) or {}
    owner = talk.get("owner", {}) or {}
    author_name = owner.get("name", "未知")

    # 内容 — 根据 type 不同取不同字段
    content = ""
    images = []

    if t_type == "talk":
        text = talk.get("text", "") or ""
        # 清洗富文本标签 <e type="..." ... />，转为可读文本
        import re as _re
        from urllib.parse import unquote as _unquote
        # 提取 hashtag 标题转为 #标签 格式
        hashtags = _re.findall(r'<e type="hashtag"[^>]*title="([^"]*)"', text)
        for ht in hashtags:
            text = text.replace(
                _re.search(rf'<e type="hashtag"[^>]*title="{_re.escape(ht)}"[^>]*/>', text).group(0),
                _unquote(ht)
            )
        # 移除所有剩余的 <e ... /> 标签
        text = _re.sub(r"<e\s[^>]*/>", "", text)
        content = text.strip()

        # 图片
        imgs = talk.get("images", []) or []
        for img in imgs:
            url = img.get("files", [{}])[0].get("url", "") if img.get("files") else ""
            if url:
                images.append(url)

        # 文件/附件
        files = talk.get("files", []) or []
        for f in files:
            f_url = f.get("files", [{}])[0].get("url", "") if f.get("files") else ""
            if f_url:
                images.append(f_url)

    elif t_type == "q&a":
        question = topic.get("question", {}) or {}
        content = question.get("text", "") or ""

    # 标题
    title = content[:50].replace("\n", " ") if content else "无标题"
    if len(content) > 50:
        title += "..."

    # 互动数据
    likes = topic.get("likes_count", 0) or 0
    comments_count = topic.get("comments_count", 0) or 0
    reading_count = topic.get("reading_count", 0) or 0

    # URL
    url = f"https://wx.zsxq.com/dweb2/index/topic/{topic_id}" if topic_id else ""

    # 时间格式化
    time_str = ""
    if create_time:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(create_time.replace("Z", "+00:00"))
            time_str = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            time_str = create_time

    # 话题标签
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
    import re
    match = re.search(r"/group/(\w+)", url)
    return match.group(1) if match else None
