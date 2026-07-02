#!/usr/bin/env python3
"""盘前财经快讯模块。

在每个 A 股交易日盘前（北京时间 08:50）自动抓取全球市场科技财经新闻，
使用 LLM 生成简洁报告，通过邮件推送。
"""

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

# ── 配置 ──

SINA_NEWS_API = "https://feed.mix.sina.com.cn/api/roll/get"

# 新浪财经频道 lid 映射
CHANNELS = {
    "A股": 2509,
    "港股": 2516,
    "美股": 2515,
}

# 科技/市场相关关键词（用于过滤）
TECH_KEYWORDS = [
    "AI", "人工智能", "芯片", "半导体", "科技", "算力", "大模型",
    "机器人", "自动化", "新能源", "光伏", "储能", "锂电",
    "英伟达", "特斯拉", "苹果", "微软", "谷歌", "Meta",
    "三星", "SK海力士", "台积电", "中芯国际",
    "纳斯达克", "标普", "道琼斯", "日经", "韩国", "KOSPI",
    "港股", "恒指", "科指", "北向", "涨停", "跌停",
    "涨停板", "龙虎榜", "主力", "资金", "净流入",
    "创新药", "医药", "消费", "白酒",
]

# 排除关键词（体育、娱乐、非财经）
EXCLUDE_KEYWORDS = [
    "夺冠", "金牌", "奥运", "世界杯", "春晚", "春节",
    "靳东", "郑钦文", "李樟煜", "网球", "晋级", "凯恩",
    "英格兰", "比利时", "金球", "巨星", "半场", "全场",
    "CBA", "NBA", "湖人", "勇士", "梅西", "C罗",
    "结婚", "离婚", "恋情", "出轨", "八卦",
]


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def fetch_sina_news(lid: int, num: int = 20) -> list[dict]:
    """从新浪财经频道获取新闻。"""
    url = f"{SINA_NEWS_API}?pageid=153&lid={lid}&num={num}&page=1"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Referer": "https://finance.sina.com.cn/",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8")
        parsed = json.loads(data)
        return parsed.get("result", {}).get("data", [])
    except Exception as e:
        print(f"[新闻获取] 频道 {lid} 失败: {e}", flush=True)
        return []


def _is_relevant(title: str) -> bool:
    """判断新闻标题是否与科技/市场相关。"""
    if any(k in title for k in EXCLUDE_KEYWORDS):
        return False
    return any(k in title for k in TECH_KEYWORDS)


def _format_timestamp(ts_str: str) -> str:
    """将 Unix 时间戳转为 HH:MM 格式。"""
    try:
        ts = int(ts_str)
        return datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
    except (ValueError, OSError):
        return ""


def collect_news(max_per_channel: int = 15) -> dict[str, list[dict]]:
    """从所有频道收集并过滤新闻。"""
    all_news = {}
    seen_titles = set()

    for channel_name, lid in CHANNELS.items():
        items = fetch_sina_news(lid, num=max_per_channel * 2)
        filtered = []
        for item in items:
            title = item.get("title", "").strip()
            if not title or title in seen_titles:
                continue
            if _is_relevant(title):
                seen_titles.add(title)
                filtered.append({
                    "title": title,
                    "time": _format_timestamp(item.get("intime", "")),
                    "channel": channel_name,
                    "url": item.get("url", ""),
                })
                if len(filtered) >= max_per_channel:
                    break
        all_news[channel_name] = filtered
        print(f"[新闻] {channel_name}频道: 获取 {len(filtered)} 条相关新闻", flush=True)
        time.sleep(0.3)  # 避免请求过快

    return all_news


def _build_news_summary(news: dict[str, list[dict]]) -> str:
    """将新闻数据格式化为 LLM 摘要输入文本。"""
    lines = []
    for channel, items in news.items():
        if not items:
            continue
        lines.append(f"## {channel}市场")
        for item in items:
            time_str = f"[{item['time']}] " if item['time'] else ""
            lines.append(f"- {time_str}{item['title']}")
        lines.append("")
    return "\n".join(lines)


def _summarize_with_llm(news_text: str, date_str: str) -> str:
    """使用 LLM 生成盘前快讯摘要。"""
    if not news_text.strip():
        return "今日盘前暂无重大科技财经新闻。"

    try:
        from summarizer import get_client
        client, model, provider = get_client()
        print(f"[LLM] 使用 {provider} ({model}) 生成快讯...", flush=True)

        prompt = f"""请根据以下全球市场科技财经新闻，生成一份简洁的「盘前财经快讯」报告。

要求：
1. 按市场分组：A股、港股、美股、亚太（日本/韩国）
2. 每个市场提取 2-3 条最重要的科技/产业新闻
3. 每条新闻用一句话概括核心信息
4. 最后给出一段「今日关注」总结（3-5 条要点）
5. 全文不超过 400 字，简洁有力
6. 使用中文，格式为 Markdown

日期：{date_str}

新闻素材：
{news_text}"""

        result = client.create(
            system="你是一位专业的财经新闻编辑，擅长从海量资讯中提炼核心信息，为投资者提供简洁有力的盘前简报。",
            prompt=prompt,
            max_tokens=1024,
        )
        return result if result else "LLM 生成失败，请查看原始新闻。"

    except Exception as e:
        print(f"[LLM] 生成失败: {e}", flush=True)
        # 降级：直接返回原始新闻
        return f"## 盘前快讯（{date_str}）\n\n{news_text}"


def generate_briefing() -> str:
    """生成完整的盘前快讯报告。"""
    now = _now_shanghai()
    date_str = now.strftime("%Y年%m月%d日")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]

    # 收集新闻
    news = collect_news(max_per_channel=15)
    total = sum(len(v) for v in news.values())
    print(f"[快讯] 共收集 {total} 条科技财经新闻", flush=True)

    # 格式化新闻文本
    news_text = _build_news_summary(news)

    # LLM 摘要
    summary = _summarize_with_llm(news_text, f"{date_str} {weekday}")

    # 组装报告
    report = f"""# 📰 盘前财经快讯 | {date_str} {weekday}

{summary}

---
*数据来源：新浪财经 | 自动生成于 {now.strftime('%H:%M')}*
"""
    return report


def send_briefing_email(report: str) -> bool:
    """发送快讯邮件。"""
    try:
        from email_sender import send_email
        now = _now_shanghai()
        subject = f"📰 盘前财经快讯 {now.strftime('%m月%d日')}"
        send_email(subject=subject, body_html=report)
        return True
    except Exception as e:
        print(f"[邮件] 发送失败: {e}", flush=True)
        return False


def main():
    """主入口。"""
    print("=" * 60, flush=True)
    print("📰 盘前财经快讯生成器", flush=True)
    print("=" * 60, flush=True)

    now = _now_shanghai()
    print(f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)", flush=True)

    # 生成报告
    report = generate_briefing()
    print(f"\n{report}", flush=True)

    # 发送邮件
    if send_briefing_email(report):
        print("\n[完成] 快讯邮件已发送", flush=True)
    else:
        print("\n[警告] 邮件发送失败", flush=True)

    # 保存到本地
    try:
        from storage import save_premarket_briefing
        save_premarket_briefing(report)
    except Exception:
        pass


if __name__ == "__main__":
    main()
