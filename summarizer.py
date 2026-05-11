"""内容总结模块。

支持 DeepSeek（默认）和 Claude 两种 AI 后端，
通过 config.yaml 中的 ai.provider 切换。
"""

import json
import os
from pathlib import Path

import yaml


def _load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_client():
    """根据配置获取 AI client，支持 deepseek / claude。

    Returns:
        tuple: (client, model, provider_name)
            client 是统一包装后的调用对象，提供 .create(system, prompt, max_tokens) 方法。
    """
    config = _load_config()
    ai_config = config.get("ai", {})
    provider = ai_config.get("provider", "deepseek")

    if provider == "deepseek":
        return _init_deepseek(ai_config.get("deepseek", {}))
    elif provider == "claude":
        return _init_claude(ai_config.get("claude", {}))
    else:
        raise ValueError(f"不支持的 AI provider: {provider}，可选: deepseek, claude")


def _init_deepseek(ds_config: dict):
    """初始化 DeepSeek client（OpenAI 兼容接口）。"""
    from openai import OpenAI

    api_key = os.environ.get("DEEPSEEK_API_KEY") or ds_config.get("api_key", "")
    if not api_key:
        raise ValueError(
            "请设置 DEEPSEEK_API_KEY 环境变量或在 config.yaml 中配置 ai.deepseek.api_key"
        )

    base_url = ds_config.get("base_url", "https://api.deepseek.com")
    model = ds_config.get("model", "deepseek-chat")

    client = OpenAI(api_key=api_key, base_url=base_url)

    # 包装为统一接口
    class DeepSeekWrapper:
        def create(self, system: str, prompt: str, max_tokens: int = 4096) -> str:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content

    return DeepSeekWrapper(), model, "deepseek"


def _init_claude(claude_config: dict):
    """初始化 Claude client。"""
    from anthropic import Anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY") or claude_config.get("api_key", "")
    if not api_key:
        raise ValueError(
            "请设置 ANTHROPIC_API_KEY 环境变量或在 config.yaml 中配置 ai.claude.api_key"
        )

    model = claude_config.get("model", "claude-sonnet-4-6")
    raw_client = Anthropic(api_key=api_key)

    class ClaudeWrapper:
        def create(self, system: str, prompt: str, max_tokens: int = 4096) -> str:
            response = raw_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text

    return ClaudeWrapper(), model, "claude"


def summarize_posts(posts: list[dict], batch_size: int = 20) -> str:
    """对帖子列表进行分批总结，返回 Markdown 格式报告。

    Args:
        posts: 清洗后的结构化帖子列表（来自 extractor.py）。
        batch_size: 每批处理的帖子数。

    Returns:
        str: Markdown 格式的总结报告。
    """
    if not posts:
        return "# 总结报告\n\n暂无内容。\n"

    client, model, provider = get_client()
    print(f"AI 后端: {provider} ({model})", flush=True)

    from extractor import generate_stats
    stats = generate_stats(posts)

    total_batches = (len(posts) + batch_size - 1) // batch_size
    all_summaries = []
    print(f"共 {len(posts)} 篇帖子，分 {total_batches} 批总结", flush=True)

    for i in range(0, len(posts), batch_size):
        batch = posts[i : i + batch_size]
        batch_num = i // batch_size + 1
        start_idx = i + 1
        end_idx = min(i + batch_size, len(posts))
        print(f"  [总结 {batch_num}/{total_batches}] 处理第 {start_idx}-{end_idx} 篇...", flush=True)

        summary = _summarize_batch(client, batch, batch_num, total_batches)
        all_summaries.append(summary)
        print(f"  [总结 {batch_num}/{total_batches}] 完成", flush=True)

    if total_batches > 1:
        print("生成整体概述...", flush=True)
        overview = _summarize_overview(client, all_summaries, stats)
        print("整体概述完成", flush=True)
    else:
        overview = ""

    report = _build_report(stats, all_summaries, overview)
    return report


def _format_post(post: dict, index: int) -> str:
    """格式化单篇帖子供 API 处理。"""
    parts = []

    parts.append(f"【帖子 {index}】")
    if post.get("title"):
        parts.append(f"标题: {post['title']}")
    parts.append(f"作者: {post.get('author', '未知')}")
    parts.append(f"时间: {post.get('time', '未知')}")
    parts.append(f"点赞: {post.get('likes', 0)} | 评论: {post.get('comments_count', 0)}")
    parts.append(f"类型: {post.get('content_type', 'text')}")
    if post.get("tags"):
        parts.append(f"标签: {', '.join('#' + t for t in post['tags'])}")
    parts.append(f"\n内容:\n{post.get('content', '')}")

    if post.get("comments"):
        parts.append(f"\n精选评论 ({len(post['comments'])} 条):")
        for c in post["comments"][:10]:
            parts.append(f"  - {c['author']}: {c['content'][:200]}")

    return "\n".join(parts)


def _summarize_batch(client, batch: list[dict], batch_num: int, total_batches: int) -> str:
    """总结一批帖子。"""
    posts_text = "\n\n---\n\n".join(
        _format_post(p, idx + 1) for idx, p in enumerate(batch)
    )

    system = "你是一位专业的内容分析师，擅长从大量信息中提取关键观点和进行结构化归纳。请严格按照要求格式输出。"

    prompt = f"""请分析以下知识星球专栏的帖子内容（第 {batch_num}/{total_batches} 批），提取重点并进行归纳。

要求：
1. **核心观点提取**：从这段内容中提取 5-10 个最有价值的核心观点或知识点，用简洁的语言概括。
2. **主题分类**：将帖子按主题归类（如：技术干货、商业思考、行业趋势、实用技巧等）。
3. **高价值内容标记**：特别指出点赞数高、评论热烈的帖子，简述其价值所在。
4. **关键引用**：如果有特别精彩的原文表述，直接引用（注明作者和原帖编号）。

以下是帖子内容：

{posts_text}

请用 Markdown 格式输出本批次的总结。"""

    return client.create(system=system, prompt=prompt, max_tokens=4096)


def _summarize_overview(client, batch_summaries: list[str], stats: dict) -> str:
    """基于各批次总结，生成整体概述。"""
    combined = "\n\n---\n\n".join(
        f"## 第 {i+1} 批次总结\n{s}" for i, s in enumerate(batch_summaries)
    )

    system = "你是一位资深内容编辑，擅长从大量内容中提炼精华。请严格按照要求格式输出。"

    prompt = f"""请基于以下各批次的总结内容，生成一份整体概述。

统计信息：
- 总帖子数: {stats['total']}
- 总点赞数: {stats['total_likes']}
- 总评论数: {stats['total_comments']}
- 作者数: {stats['unique_authors']}

要求：
1. **整体主题概览**：用 2-3 句话概括这一批内容的整体主题和方向。
2. **TOP 10 核心要点**：从所有批次中筛选出最重要的 10 个核心观点或知识点。
3. **热门话题排序**：列出讨论最热烈的 3-5 个话题。
4. **推荐阅读**：推荐 5-10 篇最值得深度阅读的帖子及其理由。

以下是各批次总结：

{combined}

请用 Markdown 格式输出。"""

    return client.create(system=system, prompt=prompt, max_tokens=4096)


def _build_report(stats: dict, batch_summaries: list[str], overview: str) -> str:
    """组装完整的 Markdown 报告。"""
    from datetime import datetime

    parts = []

    parts.append(f"# 知识星球专栏内容总结")
    parts.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    parts.append("## 数据概览\n")
    parts.append(f"| 指标 | 数值 |")
    parts.append(f"|------|------|")
    parts.append(f"| 总帖子数 | {stats['total']} |")
    parts.append(f"| 总点赞数 | {stats['total_likes']} |")
    parts.append(f"| 总评论数 | {stats['total_comments']} |")
    parts.append(f"| 作者数 | {stats['unique_authors']} |")
    if stats.get("content_types"):
        types_str = ", ".join(f"{k}: {v}" for k, v in stats["content_types"].items())
        parts.append(f"| 内容类型 | {types_str} |")
    parts.append("")

    if overview:
        parts.append("## 整体概述\n")
        parts.append(overview)
        parts.append("")

    if batch_summaries:
        parts.append("## 详细总结\n")
        for i, summary in enumerate(batch_summaries):
            parts.append(f"### 第 {i + 1} 批\n")
            parts.append(summary)
            parts.append("")

    return "\n".join(parts)


def summarize_single_post(post: dict) -> dict:
    """对单篇帖子进行快速总结，返回要点列表。

    Args:
        post: 单篇帖子数据。

    Returns:
        dict: 含 key_points, tags, value_score 的字典。
    """
    content = post.get("content", "")
    if not content.strip():
        return {"key_points": [], "tags": [], "value_score": 0}

    client, _, _ = get_client()

    system = "你是一个内容分析工具。请严格按照 JSON 格式返回结果。"
    prompt = f"""请分析以下这篇知识星球帖子，提取关键信息。

帖子内容:
{content[:3000]}

请用 JSON 格式返回（不要包含 markdown 代码块标记）:
{{
  "key_points": ["要点1", "要点2", "要点3"],
  "tags": ["标签1", "标签2"],
  "value_score": 1-10 的分数（信息密度和实用价值）
}}"""

    raw = client.create(system=system, prompt=prompt, max_tokens=512)

    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return {"key_points": [], "tags": [], "value_score": 0}
