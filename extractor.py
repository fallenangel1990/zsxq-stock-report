"""内容解析与清洗模块。

从爬取的原始帖子数据中提取结构化信息，清洗无用内容。
"""

from datetime import datetime


def extract_structured_content(posts: list[dict]) -> list[dict]:
    """将原始帖子清洗并标准化为结构化数据。

    对每篇帖子：
    - 清理多余空白和换行
    - 解析时间字符串为统一格式
    - 提取话题标签
    - 鉴别内容类型（纯文本/图文/链接分享）

    Args:
        posts: crawler.py 返回的原始帖子列表。

    Returns:
        list[dict]: 清洗后的结构化数据。
    """
    cleaned = []

    for post in posts:
        item = {
            "topic_id": post.get("topic_id", ""),
            "title": _clean_text(post.get("title", "")),
            "author": post.get("author", "未知"),
            "content": _clean_text(post.get("content", "")),
            "time": _parse_time(post.get("time", "")),
            "time_raw": post.get("time", ""),
            "url": post.get("url", ""),
            "images": post.get("images", []),
            "likes": post.get("likes", 0),
            "comments_count": post.get("comments_count", 0),
            "comments": [
                {
                    "author": _clean_text(c.get("author", "")),
                    "content": _clean_text(c.get("content", "")),
                }
                for c in post.get("comments", [])
            ],
            "tags": _extract_tags(post.get("content", "")),
            "content_type": _detect_type(post),
            "word_count": len(post.get("content", "")),
        }
        cleaned.append(item)

    return cleaned


def _clean_text(text: str) -> str:
    """清理文本：去除多余空白、正常化换行。"""
    if not text:
        return ""
    # 合并连续空白
    import re
    text = re.sub(r"[ \t]+", " ", text)
    # 合并 3 个以上连续换行为两个换行
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 去除首尾空白
    return text.strip()


def _parse_time(time_text: str) -> str:
    """将各种时间格式统一为 ISO 格式字符串。"""
    if not time_text:
        return ""

    import re

    # 尝试匹配常见格式
    # "2024-01-15 14:30"
    m = re.match(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", time_text)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            return dt.isoformat()
        except ValueError:
            pass

    # "1月15日 14:30"
    m = re.match(r"(\d{1,2})月(\d{1,2})日\s*(\d{2}:\d{2})", time_text)
    if m:
        now = datetime.now()
        dt = datetime(now.year, int(m.group(1)), int(m.group(2)))
        return dt.strftime("%Y-%m-%d")

    # "昨天 14:30" / "今天 14:30"
    if "今天" in time_text:
        return datetime.now().strftime("%Y-%m-%d")
    if "昨天" in time_text:
        from datetime import timedelta
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # "3天前" / "1小时前"
    m = re.match(r"(\d+)\s*天前", time_text)
    if m:
        from datetime import timedelta
        return (datetime.now() - timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    m = re.match(r"(\d+)\s*小时前", time_text)
    if m:
        from datetime import timedelta
        dt = datetime.now() - timedelta(hours=int(m.group(1)))
        return dt.strftime("%Y-%m-%d %H:%M")

    return time_text


def _extract_tags(text: str) -> list[str]:
    """从文本中提取 #话题 标签。"""
    import re
    tags = re.findall(r"#(\S+)", text)
    return list(set(tags))


def _detect_type(post: dict) -> str:
    """判断内容类型。"""
    images = post.get("images", [])
    content = post.get("content", "")
    url = post.get("url", "")

    if images and len(content) < 50:
        return "image"
    elif images and content:
        return "article_with_images"
    elif "http" in content and len(content) < 200:
        return "link_share"
    else:
        return "text"


def generate_stats(posts: list[dict]) -> dict:
    """生成帖子列表的统计信息。"""
    if not posts:
        return {"total": 0}

    total_likes = sum(p.get("likes", 0) for p in posts)
    total_comments = sum(p.get("comments_count", 0) for p in posts)
    authors = set(p.get("author", "") for p in posts)

    # 内容类型分布
    type_dist = {}
    for p in posts:
        t = p.get("content_type", "text")
        type_dist[t] = type_dist.get(t, 0) + 1

    # 按时间分布
    time_dist = {}
    for p in posts:
        t = p.get("time", "")
        if t:
            month = t[:7]  # YYYY-MM
            time_dist[month] = time_dist.get(month, 0) + 1

    return {
        "total": len(posts),
        "total_likes": total_likes,
        "total_comments": total_comments,
        "unique_authors": len(authors),
        "content_types": type_dist,
        "monthly_distribution": dict(sorted(time_dist.items())),
        "top_posts": sorted(posts, key=lambda p: p.get("likes", 0), reverse=True)[:5],
    }
