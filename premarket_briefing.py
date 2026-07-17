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
    """使用 LLM 生成盘前快讯摘要（HTML 格式）。"""
    if not news_text.strip():
        return "<p>今日盘前暂无重大科技财经新闻。</p>"

    try:
        from summarizer import get_client
        client, model, provider = get_client()
        print(f"[LLM] 使用 {provider} ({model}) 生成快讯...", flush=True)

        prompt = f"""请根据以下全球市场科技财经新闻，生成一份简洁的「盘前财经快讯」报告。

要求：
1. 按市场分组：A股、港股、美股、亚太（日本/韩国）
2. 每个市场提取 2-3 条最重要的科技/产业新闻，用一句话概括
3. 给出「今日关注」总结（3-5 条要点）
4. 给出「⚠️ 风险提示」段落（重要！）：
   - 根据新闻内容，分析今日需要警惕的 2-3 个风险点
   - 关注：板块回调风险、政策监管风险、外围市场冲击、资金面压力、地缘政治等
   - 对每个风险点用 ⚠️低 / ⚠️中 / ⚠️高 标记风险等级
   - 如新闻显示某板块大跌/资金外流/政策收紧，必须明确警示
5. 全文不超过 600 字，简洁有力
6. 使用中文
7. **输出纯 HTML 格式**（不要使用 Markdown 语法），要求：
   - 市场分组标题使用 <h3> 标签
   - 每条新闻使用 <li> 标签包裹在 <ul> 中
   - 「今日关注」和「⚠️ 风险提示」使用 <h3> 作为小标题，内容用 <p> 或 <ul><li> 包裹
   - 风险等级标记（⚠️低/⚠️中/⚠️高）用 <strong> 包裹
   - 不要输出 <html>/<head>/<body> 等文档标签，只输出内容片段

日期：{date_str}

新闻素材：
{news_text}"""

        result = client.create(
            system="你是一位资深财经分析师和风险管理顾问，擅长从全球市场新闻中提炼投资信号和风险预警。你的简报帮助投资者在开盘前快速把握机会、规避风险。你对风险信号特别敏感，会从新闻中识别回调、监管、资金外流等风险因素。请直接输出纯 HTML 内容片段，不要使用 Markdown。",
            prompt=prompt,
            max_tokens=1200,
        )
        return result if result else "<p>LLM 生成失败，请查看原始新闻。</p>"

    except Exception as e:
        print(f"[LLM] 生成失败: {e}", flush=True)
        # 降级：直接返回原始新闻
        return f"<h3>盘前快讯（{date_str}）</h3><pre>{news_text}</pre>"


def _normalize_to_html(text: str) -> str:
    """将 LLM 输出（Markdown 或裸 HTML）统一转为邮件兼容的内联样式 HTML。

    LLM 不一定严格按 prompt 输出 HTML，可能返回 Markdown。
    先检测：若含有 Markdown 语法（#, -, * 等），用 markdown 库转为 HTML；
    若已是 HTML 标签则直接通过。然后为裸标签添加内联样式。
    """
    import markdown as md_lib

    # 简单启发：包含 Markdown 标题/列表/加粗语法 → 转换
    looks_like_md = any(line.lstrip().startswith(("# ", "## ", "### ", "- ", "* "))
                        for line in text.split("\n")[:15])
    if looks_like_html(text) and not looks_like_md:
        html = text
    else:
        html = md_lib.markdown(text, extensions=["tables", "fenced_code"])

    return _style_inline_html(html)


def looks_like_html(text: str) -> bool:
    """判断文本是否已经是 HTML（含常见 HTML 标签）。"""
    stripped = text.lstrip()
    if stripped.startswith(("<!DOCTYPE", "<!doctype", "<html", "<div", "<h1", "<h2", "<h3")):
        return True
    tag_re = re.compile(r"<(p|ul|ol|li|h[1-6]|strong|br|div|span|table)[^>]*>", re.IGNORECASE)
    return len(tag_re.findall(text)) >= 3


def _style_inline_html(html: str) -> str:
    """为裸 HTML 标签添加邮件兼容的内联样式（标题/段落/列表/加粗）。"""
    html = html.replace(
        "<h3>",
        '<h3 style="color:#1d4ed8;margin:20px 0 10px;font-size:17px;line-height:1.4;'
        'border-left:4px solid #2563eb;padding-left:10px;">',
    )
    html = html.replace(
        "<h2>",
        '<h2 style="color:#1d4ed8;margin:24px 0 12px;font-size:18px;line-height:1.4;'
        'border-left:4px solid #2563eb;padding-left:10px;">',
    )
    html = html.replace(
        "<p>",
        '<p style="margin:8px 0;line-height:1.8;color:#374151;font-size:15px;">',
    )
    html = html.replace(
        "<ul>",
        '<ul style="margin:8px 0 14px;padding-left:22px;line-height:1.8;color:#374151;">',
    )
    html = html.replace(
        "<li>",
        '<li style="margin:5px 0;">',
    )
    html = html.replace(
        "<strong>",
        '<strong style="color:#111827;font-weight:700;">',
    )
    return html


def generate_briefing() -> str:
    """生成完整的盘前快讯报告（HTML 格式，可直接作为邮件正文）。"""
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

    # LLM 摘要（无论返回 MD 还是 HTML，统一转为邮件兼容的内联样式 HTML）
    summary_html = _normalize_to_html(_summarize_with_llm(news_text, f"{date_str} {weekday}"))

    # 组装 HTML 邮件
    report = f"""\
<div style="background:#f3f6fb;padding:18px 10px;">
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:720px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;color:#1f2937;">
  <!-- 头部横幅 -->
  <div style="background:linear-gradient(135deg,#1d4ed8,#0284c7);color:white;padding:24px 28px;">
    <h2 style="margin:0;font-size:22px;line-height:1.35;">📰 盘前财经快讯</h2>
    <p style="margin:8px 0 0;opacity:0.9;font-size:14px;">{date_str} {weekday}</p>
  </div>

  <!-- LLM 快讯正文 -->
  <div style="padding:20px 28px;font-size:15px;line-height:1.8;">
    {summary_html}
  </div>

  <!-- 风险提示 -->
  <div style="background:#fffbeb;border-left:4px solid #f59e0b;padding:16px 20px;margin:0 20px 16px;border-radius:0 8px 8px 0;">
    <h3 style="margin:0 0 10px;font-size:15px;color:#b45309;">🛡️ 风险提示</h3>
    <ul style="margin:0;padding-left:20px;font-size:14px;line-height:1.8;color:#92400e;">
      <li>以上信息仅供参考，不构成任何投资建议</li>
      <li>股市有风险，投资需谨慎</li>
      <li>盘中走势受多种因素影响，请结合自身风险承受能力做出独立判断</li>
      <li>建议单只股票仓位不超过总资金的 20%，单一行业不超过 40%</li>
      <li>设置好止损位，严格执行纪律</li>
    </ul>
  </div>

  <!-- 页脚 -->
  <div style="color:#6b7280;font-size:12px;padding:14px 28px;border-top:1px solid #e5e7eb;background:#fafafa;line-height:1.7;">
    数据来源：新浪财经 | 自动生成于 {now.strftime('%H:%M')} 北京时间
  </div>
</div>
</div>"""
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
