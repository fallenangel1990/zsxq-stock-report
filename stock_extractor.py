"""股票机会提取模块。

从知识星球帖子中提取股票投资机会，使用 AI 进行分类整理，
增强行情数据、计算推荐指数，按优先级排序输出。
"""

import json
import re
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml


def _now_shanghai() -> datetime:
    """返回北京时间当前时间，用于报告中展示的生成时间。"""
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _load_scoring_config() -> dict:
    """加载评分配置权重（含向后兼容默认值）。"""
    config_path = Path(__file__).parent / "config.yaml"
    scoring = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
        scoring = config.get("stocks", {}).get("scoring", {})
    # 向后兼容默认值：config.yaml 缺少新键时静默关闭趋势功能
    scoring.setdefault("trend_weight", 0.0)
    scoring.setdefault("sector_aliases", {})
    scoring.setdefault("trend", {
        "min_stocks_for_trend": 2,
        "max_trend_score": 10,
        "momentum_weight": 0.35,
        "size_weight": 0.25,
        "discussion_weight": 0.25,
        "logic_weight": 0.15,
    })
    return scoring


# ── 投资相关关键词（用于预过滤，减少无关帖子送入 AI） ──
_INVEST_KEYWORDS = [
    # 交易动作
    "买入", "卖出", "持有", "加仓", "减仓", "建仓", "清仓", "止盈", "止损",
    # 目标/观点
    "目标价", "目标市值", "看到", "看高", "看多", "看空", "看好", "看涨", "看跌",
    "推荐", "关注", "机会", "弹性", "空间", "潜力",
    # 评级
    "买入评级", "增持", "中性", "减持", "强推", "强烈推荐",
    # 财务指标
    "业绩", "增速", "利润", "营收", "PE", "PB", "EPS", "ROE", "毛利率", "净利率",
    # 行情描述
    "涨停", "跌停", "突破", "反弹", "回调", "龙头", "黑马", "白马", "牛股",
    # 估值
    "低估", "高估", "估值", "市值",
    # 赛道/板块
    "赛道", "板块", "概念", "产业链", "景气",
]

_FOREIGN_BANK_KEYWORDS = [
    "高盛", "goldman", "摩根士丹利", "morgan stanley", "大摩",
    "摩根大通", "jpmorgan", "jp morgan", "小摩", "瑞银", "ubs",
    "花旗", "citi", "citigroup", "美银", "bofa", "bank of america",
    "美林", "merrill", "德银", "deutsche bank", "巴克莱", "barclays",
    "汇丰", "hsbc", "野村", "nomura", "麦格理", "macquarie",
    "杰富瑞", "jefferies", "里昂", "clsa", "伯恩斯坦", "bernstein",
]

_RESEARCH_REPORT_KEYWORDS = [
    "研报", "报告", "评级", "目标价", "目标市值", "上调", "下调",
    "首予", "覆盖", "维持", "买入", "增持", "推荐",
]

REPORT_RECOMMENDATION_THRESHOLD = 3.0
REPORT_OBSERVATION_THRESHOLD = 2.0
REPORT_MIN_VISIBLE_STOCKS = 8
REPORT_MIN_RECOMMENDATIONS = 5


def _filter_investment_posts(posts: list[dict]) -> tuple[list[dict], list[dict]]:
    """预过滤帖子：只保留包含投资关键词的帖子，减少 AI token 消耗。

    对每篇帖子的标题+内容做关键词匹配，命中任意关键词则保留。
    不区分大小写。

    Args:
        posts: 结构化帖子列表。

    Returns:
        (relevant_posts, skipped_posts): 相关帖子列表和被跳过的帖子列表。
    """
    relevant = []
    skipped = []
    for post in posts:
        text = (post.get("title", "") + " " + post.get("content", "")).lower()
        # 检查是否包含 6 位股票代码（强信号，直接保留）
        if re.search(r"\b\d{6}\b", text):
            relevant.append(post)
            continue
        # 关键词匹配
        if any(kw.lower() in text for kw in _INVEST_KEYWORDS):
            relevant.append(post)
        else:
            skipped.append(post)
    return relevant, skipped


def _is_foreign_bank_research_post(post: dict) -> bool:
    """识别国外投行/外资券商研报相关帖子。"""
    text = f"{post.get('title', '')} {post.get('content', '')}".lower()
    has_bank = any(keyword.lower() in text for keyword in _FOREIGN_BANK_KEYWORDS)
    has_report_signal = any(keyword.lower() in text for keyword in _RESEARCH_REPORT_KEYWORDS)
    return has_bank and has_report_signal


def _source_post_numbers(source: str) -> set[int]:
    """从 source 字段中提取帖子编号。"""
    if not source:
        return set()
    return {int(n) for n in re.findall(r"帖子\s*(\d+)", source)}


def _annotate_foreign_research_sources(stocks_json: dict, foreign_post_numbers: set[int]) -> None:
    """给来自国外投行研报帖子的股票打标。"""
    if not foreign_post_numbers:
        return
    for category in ("quantitative", "elastic"):
        for stock in stocks_json.get(category, []):
            source_numbers = _source_post_numbers(stock.get("source", ""))
            if source_numbers & foreign_post_numbers:
                stock["foreign_research"] = True
                stock["source_note"] = "国外投行研报"


def _is_a_share_candidate(stock: dict) -> bool:
    """只保留 A 股推荐；非 A 股代码或明显境外标的剔除。"""
    code = (stock.get("code") or "").strip()
    if code:
        return bool(re.fullmatch(r"\d{6}", code))

    # 无代码时只要不像明显境外代码，仍保留给后续行情/人工判断，避免错杀未写代码的 A 股。
    name = (stock.get("name") or "").strip()
    if re.search(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", name):
        return False
    return True


def extract_stock_opportunities(
    posts: list[dict],
    batch_size: int = 30,
    verbose: bool = True,
) -> str:
    """从帖子列表中提取股票投资机会，增强行情数据和推荐指数。

    Args:
        posts: 清洗后的结构化帖子列表。
        batch_size: 每批处理的帖子数。

    Returns:
        Markdown 格式的增强股票机会报告（含市值、推荐指数、排序）。
    """
    if not posts:
        return _empty_report()

    # ── 预过滤：仅保留含投资关键词的帖子，减少 AI token 消耗 ──
    relevant_posts, skipped_posts = _filter_investment_posts(posts)
    if verbose:
        skip_pct = len(skipped_posts) / len(posts) * 100 if posts else 0
        print(
            f"帖子预过滤: {len(posts)} → {len(relevant_posts)} "
            f"（跳过 {len(skipped_posts)} 篇无关，{skip_pct:.0f}%）",
            flush=True,
        )

    if not relevant_posts:
        return _empty_report()

    from summarizer import get_client
    client, model, provider = get_client()
    if verbose:
        print(f"股票提取 AI: {provider} ({model})", flush=True)

    total_batches = (len(relevant_posts) + batch_size - 1) // batch_size
    batch_reports = []
    all_stocks_json = {"quantitative": [], "elastic": [], "sectors": [], "risks": []}

    if verbose:
        print(
            f"从 {len(relevant_posts)} 篇帖子中提取股票机会，"
            f"分 {total_batches} 批，并发执行...",
            flush=True,
        )

    # 准备所有批次
    batches = []
    for i in range(0, len(relevant_posts), batch_size):
        batch_num = i // batch_size + 1
        batches.append((
            client,
            relevant_posts[i : i + batch_size],
            i,
            batch_num,
            total_batches,
        ))

    foreign_post_numbers = {
        i + 1 for i, post in enumerate(relevant_posts)
        if _is_foreign_bank_research_post(post)
    }

    # 并发调用 AI（最多 3 个并发，避免触发 API 限流）
    max_workers = min(3, len(batches))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(_extract_stocks_batch, *b): b[3]
            for b in batches
        }
        # 按批次号收集结果以保持顺序
        results = {}
        for future in as_completed(future_to_idx):
            batch_num = future_to_idx[future]
            try:
                report = future.result()
                if not report:
                    print(f"  [股票 {batch_num}/{total_batches}] 失败: AI 返回空响应", flush=True)
                    continue
                results[batch_num] = report
                batch_json = _parse_stock_json(report)
                _annotate_foreign_research_sources(batch_json, foreign_post_numbers)
                _merge_json(all_stocks_json, batch_json)
                q = len(batch_json.get("quantitative", []))
                e = len(batch_json.get("elastic", []))
                if verbose:
                    print(f"  [股票 {batch_num}/{total_batches}] 完成 (量化:{q} 弹性:{e})", flush=True)
            except Exception as exc:
                print(f"  [股票 {batch_num}/{total_batches}] 失败: {exc}", flush=True)

    # 按批次号排序结果，过滤空字符串
    batch_reports = [results[k] for k in sorted(results.keys()) if results[k]]
    if not batch_reports:
        raise RuntimeError(
            "股票提取全部批次失败，请检查 AI API Key、模型和 base_url 配置。"
        )

    if verbose:
        total_q = len(all_stocks_json["quantitative"])
        total_e = len(all_stocks_json["elastic"])
        print(f"JSON 合并完成: 量化 {total_q} 只, 弹性 {total_e} 只", flush=True)

    # AI 合并 Markdown（保留第三、四部分的原始格式）
    if total_batches > 1:
        if verbose:
            print("合并去重 Markdown 报告...", flush=True)
        merged_md = _merge_stock_reports(client, batch_reports)
    else:
        merged_md = batch_reports[0]

    # ── 增强步骤：获取价格 → 计算评分 → 排序重建 ──
    if verbose:
        print("获取行情数据并计算推荐指数...", flush=True)
    enriched, trend_data = _enrich_and_score(all_stocks_json, verbose=verbose)
    display_meta = _select_report_display_stocks(enriched)[1]
    trend_data["display_meta"] = display_meta
    # 市场状态配置传递给报告层
    if "market_regime" not in trend_data:
        trend_data["market_regime"] = {}
    if verbose:
        print(
            "股票评分完成: "
            f"可评分候选 {display_meta['candidate_count']} 只, "
            f"{REPORT_RECOMMENDATION_THRESHOLD:.0f}分以上 {display_meta['recommendation_count']} 只, "
            f"最终展示 {display_meta['display_count']} 只",
            flush=True,
        )

    # 保存增强后的股票数据到 JSON（供 ths_sync 等模块使用）
    if enriched:
        try:
            from storage import append_recommendation_history, save_enriched_stocks
            save_enriched_stocks(enriched, group_name="latest")
            append_recommendation_history(enriched, group_name="latest")
        except Exception as exc:
            print(f"[存储] 推荐历史保存失败（不影响主流程）: {exc}", flush=True)

    merged = _rebuild_report(enriched, merged_md, trend_data)

    return _build_stock_report(merged, len(posts))


def _format_post_for_stocks(post: dict, index: int) -> str:
    """精简格式化帖子供股票提取。"""
    parts = [f"【帖子 {index}】"]
    if post.get("title"):
        parts.append(f"标题: {post['title']}")
    parts.append(f"作者: {post.get('author', '未知')}")
    parts.append(f"时间: {post.get('time', '未知')}")
    parts.append(f"\n内容:\n{post.get('content', '')}")
    return "\n".join(parts)


def _extract_stocks_batch(
    client,
    batch: list[dict],
    start_index: int,
    batch_num: int,
    total_batches: int,
) -> str:
    """将一批帖子发送给 AI，提取股票机会并输出表格 + JSON。"""
    posts_text = "\n\n---\n\n".join(
        _format_post_for_stocks(p, start_index + i + 1) for i, p in enumerate(batch)
    )

    system = (
        "你是一位专业的A股投资分析师，擅长从大量财经资讯中"
        "精确提取和分类股票投资机会。你输出干净、结构化的Markdown表格和JSON数据，"
        "绝不输出分析过程或解释性文字。本步骤负责候选提取，不负责最终买入评级；"
        "后续系统会根据行情、技术面和风险二次评分过滤。对于完全没有投资逻辑的内容，"
        "直接说明\"无符合条件的标的\"而不编造。"
    )

    prompt = f"""请分析以下知识星球专栏的帖子内容（第 {batch_num}/{total_batches} 批），
提取其中提到的股票投资机会，并按以下四个类别整理成表格。

对于每只股票：
- 本步骤是"候选池提取"：只要帖子给出 A 股公司名/代码，并同时给出产业受益、业绩变化、订单/政策/事件催化、估值目标、技术突破、研报观点等任一投资逻辑，就应纳入候选
- **重点关注长期趋势和涨价预期**：优先提取有明确涨价逻辑（涨价/提价/量价齐升）、供需紧张（供不应求/产能紧缺/低库存）、景气向上（景气上行/需求回暖）的标的
- 不要只输出最强的 2-3 只；在每批内容中尽量完整覆盖有投资逻辑的 A 股候选，后续系统会评分筛掉低质量标的
- 仍需排除单纯背景提及、新闻罗列、无投资逻辑的名字堆砌，禁止为了凑数量编造股票
- 区分"投资建议"和"背景提及"——只在表格中包含有明确投资逻辑的股票
- 只提取 A 股投资推荐；港股、美股、海外上市公司、ETF、ADR、指数、基金等非 A 股推荐一律忽略
- 如果原帖是国外投行/外资券商研报，只保留其中涉及 A 股的推荐，并在逻辑或来源中体现"国外投行研报"
- 如果有股票代码，请只填写 6 位 A 股代码；不要填写境外代码
- 如果同一只股票出现在多个帖子中，合并为一条最完整的记录
- 尽量提取该股票对应的风险点/潜在利空；若原文没有明确提及，JSON 中 risk 写空字符串，不要编造确定性风险
- 单批输出上限建议：量化目标最多 20 只，弹性标的最多 25 只；若真实候选更少则按实际输出
- **置信度评分（confidence）**：对每只股票评估提取置信度（1-5分），5=高度确信是投资推荐，1=仅背景提及或信息模糊。影响后续评分权重

按以下四个部分输出：

## 一、有明确量化目标的股票
包括：给出了目标价、目标市值、估值区间、业绩预测等具体量化参考的标的。

| 序号 | 股票名称 | 代码 | 上涨/投资逻辑 | 量化参考 | 来源帖子 |

## 二、产业趋势中弹性最大的标的
包括：处于高景气赛道、被分析师认为股价弹性最大、受益最直接的标的。

| 序号 | 股票名称 | 代码 | 所属赛道 | 核心逻辑 | 来源帖子 |

## 三、细分板块机会
按板块/行业归类的投资机会，列出板块内的核心标的。

| 序号 | 板块名称 | 核心标的 | 板块逻辑 | 来源帖子 |

## 四、风险提示
帖子中提到的需要警惕的风险因素、需要回避的标的、或需要关注的不确定性。

| 序号 | 风险类型 | 涉及标的/板块 | 风险描述 | 来源帖子 |

投资分析要求（借鉴巴菲特-芒格-段永平框架）：
- 每只股票的"投资逻辑"应包含：①生意本质（客户是谁、为什么付钱）②护城河类型及强度
- "量化参考"应给出三情景目标价：激进（最乐观）/ 稳健（基准）/ 保守（悲观）
- "护城河"字段从以下类型选择：品牌定价权/转换成本/网络效应/规模效应/技术壁垒/渠道壁垒/无明显护城河
- "管理层"字段判断：创始人在任/职业经理人/有诚信污点/减持中/持股增加
- 风险字段需包含："反过来想——这家公司可能失败的最大路径是什么？"

注意事项：
- "来源帖子"列填写"帖子 X"格式的引用（X为帖子编号）
- 每只股票的 sector 字段必须填写！author 字段填写帖子作者名（与帖子头"作者:"一致），未知则填空字符串。
- 如果某个类别没有符合条件的标的，写"**本批次暂无符合条件的标的**"
- 非 A 股推荐不要放入任何表格或 JSON
- 不要输出表格以外的解释性文字
- 表格使用标准Markdown格式

## JSON 数据输出（重要！）
请在所有表格之后，输出一个 JSON 代码块（```json），包含所有表格中提取的结构化数据：
```json
{{
  "quantitative": [
    {{"name": "股票名称", "code": "股票代码或空字符串", "sector": "所属赛道/行业（必填）", "logic": "投资逻辑简述（含生意本质：客户是谁/为什么付钱）", "target": "量化参考原文", "target_aggressive": "激进情景目标价/目标市值（最乐观）", "target_moderate": "稳健情景目标价/目标市值（基准）", "target_conservative": "保守情景目标价/目标市值（悲观）", "risk": "风险点/潜在利空或空字符串", "moat": "护城河类型：品牌定价权/转换成本/网络效应/规模效应/技术壁垒/无明显护城河", "moat_score": "护城河强度1-5分", "management": "管理层评估关键词：诚信/能力/股东利益一致/有疑虑", "source": "帖子X", "author": "作者名", "confidence": 4}}
  ],
  "elastic": [
    {{"name": "股票名称", "code": "股票代码或空字符串", "sector": "所属赛道/行业（必填）", "logic": "核心逻辑简述（含生意本质）", "target": "量化参考原文", "target_aggressive": "激进情景目标/空字符串", "target_moderate": "基准情景目标/空字符串", "target_conservative": "保守情景目标/空字符串", "risk": "风险点/潜在利空或空字符串", "moat": "护城河类型", "moat_score": "护城河1-5分", "management": "管理层评估/空字符串", "source": "帖子X", "author": "作者名", "confidence": 3}}
  ],
  "sectors": [
    {{"sector": "板块名称", "stocks": "核心标的名称列表", "logic": "板块逻辑", "source": "帖子X"}}
  ],
  "risks": [
    {{"type": "风险类型", "target": "涉及标的/板块", "desc": "风险描述", "source": "帖子X"}}
  ]
}}
```
**重要：每只股票的 sector 字段必须填写！author 字段填写帖子作者名（与帖子头"作者:"一致），未知则填空字符串。** 根据帖子内容推断所属行业/赛道，如：AI/人工智能、半导体/芯片、新能源、机器人/自动化、汽车/新能源车、光纤光缆、商业航天、医疗医药、消费电子等。如果无法确定，填写最接近的行业大类。
仅包含表格中实际列出的条目，空数组写 []。JSON 块必须放在 Markdown 表格输出之后。
不要改变 Markdown 表格的输出格式。

以下是帖子内容：

{posts_text}"""

    result = client.create(system=system, prompt=prompt, max_tokens=8192)
    if result is None:
        return ""
    return result


def _merge_stock_reports(client, batch_reports: list[str]) -> str:
    """合并多批次股票报告，去重并统一编号。"""
    valid_reports = [r for r in batch_reports if r]
    if not valid_reports:
        return ""
    combined = "\n\n---\n\n".join(
        f"## 第 {i + 1} 批次\n{r}" for i, r in enumerate(valid_reports)
    )

    system = (
        "你是一位专业投资分析师。请将多批次的股票提取结果合并去重，"
        "统一格式输出。只输出最终表格和JSON，不输出过程说明。"
    )

    prompt = f"""请将以下多个批次的股票机会提取结果合并为一份完整报告。

要求：
1. **去重合并**：同一只股票出现在多个批次的，合并为一条，取最完整的描述
2. **统一编号**：重新从1开始编号
3. **统一格式**：保持四部分结构 + JSON 数据块不变
4. **移除空类别标记**：如果合并后某个类别不再为空，移除各批次中的"暂无符合条件的标的"
5. **只保留 A 股**：港股、美股、海外上市公司、ETF、ADR、指数、基金等非 A 股推荐一律移除
6. **国外投行研报标注**：如果来源或逻辑包含"国外投行研报/外资券商研报"信息，合并后继续保留该标注

以下是各批次结果：

{combined}

请输出合并后的完整报告（四部分 Markdown 表格 + JSON 代码块）。"""

    result = client.create(system=system, prompt=prompt, max_tokens=6144)
    if result is None:
        return ""
    return result


# ═══════════════════════════════════════════════════════════════
# 增强层：价格获取 + 评分 + 排序
# ═══════════════════════════════════════════════════════════════

def _parse_stock_json(markdown: str) -> dict:
    """从 AI 输出文本中提取 JSON 结构化数据。

    优先查找 ```json 代码块，若不存在则回退到正则解析 Markdown 表格。
    """
    if not markdown:
        return {"quantitative": [], "elastic": [], "sectors": [], "risks": []}
    # 方法1：提取 JSON 代码块
    json_match = re.search(r"```json\s*\n(.*?)\n```", markdown, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            # 验证结构
            for key in ("quantitative", "elastic", "sectors", "risks"):
                if key not in data:
                    data[key] = []
            return data
        except json.JSONDecodeError:
            pass

    # 方法2：回退 — 正则解析 Markdown 表格
    return _fallback_parse_tables(markdown)


def _merge_text(existing: str, new: str, sep: str = "；") -> str:
    """合并短文本，避免重复片段。"""
    existing = (existing or "").strip()
    new = (new or "").strip()
    if not new:
        return existing
    if not existing:
        return new
    if new in existing:
        return existing
    return f"{existing}{sep}{new}"


def _merge_json(target: dict, source: dict) -> None:
    """将 source 中的股票数据合并到 target，按股票名称去重。

    直接修改 target dict，不返回值。
    """
    # 合并 quantitative 和 elastic — 按 name 去重
    for category in ("quantitative", "elastic"):
        existing_by_name = {s.get("name", ""): s for s in target[category]}
        for stock in source.get(category, []):
            name = stock.get("name", "")
            if not name or not _is_a_share_candidate(stock):
                continue
            if name not in existing_by_name:
                target[category].append(stock)
                existing_by_name[name] = stock
            else:
                existing = existing_by_name[name]
                for field in ("code", "sector", "target"):
                    if not existing.get(field) and stock.get(field):
                        existing[field] = stock.get(field)
                for field in ("logic", "risk", "source"):
                    existing[field] = _merge_text(existing.get(field, ""), stock.get(field, ""))
                if stock.get("foreign_research"):
                    existing["foreign_research"] = True
                    existing["source_note"] = stock.get("source_note", "国外投行研报")
                # 合并独立作者集合
                if stock.get("author"):
                    existing_authors = existing.get("authors", set())
                    if not isinstance(existing_authors, set):
                        existing_authors = set()
                    existing_authors.add(stock["author"].strip())
                    existing["authors"] = existing_authors

    # 合并 sectors — 按 sector 名去重
    existing_sectors = {s.get("sector", "") for s in target["sectors"]}
    for sector in source.get("sectors", []):
        s_name = sector.get("sector", "")
        if s_name and s_name not in existing_sectors:
            target["sectors"].append(sector)
            existing_sectors.add(s_name)

    # 合并 risks — 按 type + target 去重，避免同一风险类型下不同标的被吞掉
    existing_risks = {
        (r.get("type", ""), r.get("target", ""))
        for r in target["risks"]
    }
    for risk in source.get("risks", []):
        r_key = (risk.get("type", ""), risk.get("target", ""))
        if r_key[0] and r_key not in existing_risks:
            target["risks"].append(risk)
            existing_risks.add(r_key)


def _fallback_parse_tables(markdown: str) -> dict:
    """从 Markdown 表格中回退解析股票数据（JSON 解析失败时使用）。"""
    result = {"quantitative": [], "elastic": [], "sectors": [], "risks": []}
    if not markdown:
        return result

    sections = {
        "quantitative": "有明确量化目标的股票",
        "elastic": "产业趋势中弹性最大的标的",
        "sectors": "细分板块机会",
        "risks": "风险提示",
    }

    for key, section_title in sections.items():
        # 找到对应段落
        pattern = rf"##\s*[一二三四]、\s*{section_title}.*?\n(.*?)(?=##\s*[一二三四]、|\Z)"
        section_match = re.search(pattern, markdown, re.DOTALL)
        if not section_match:
            continue

        section_text = section_match.group(1)
        # 匹配表格行（跳过表头和分隔行）
        table_rows = re.findall(r"^\|(\d+)\|(.+)\|$", section_text, re.MULTILINE)
        for row in table_rows:
            cols = [c.strip() for c in row[1].split("|")]
            if key == "quantitative" and len(cols) >= 5:
                result[key].append({
                    "name": _extract_stock_name(cols[0]),
                    "code": _extract_code(cols[1]) if len(cols) > 1 else "",
                    "logic": cols[2] if len(cols) > 2 else "",
                    "target": cols[3] if len(cols) > 3 else "",
                    "risk": "",
                    "source": cols[4] if len(cols) > 4 else "",
                })
            elif key == "elastic" and len(cols) >= 5:
                result[key].append({
                    "name": _extract_stock_name(cols[0]),
                    "code": _extract_code(cols[1]) if len(cols) > 1 else "",
                    "sector": cols[2] if len(cols) > 2 else "",
                    "logic": cols[3] if len(cols) > 3 else "",
                    "risk": "",
                    "source": cols[4] if len(cols) > 4 else "",
                })
            elif key == "sectors" and len(cols) >= 4:
                result[key].append({
                    "sector": cols[0],
                    "stocks": cols[1] if len(cols) > 1 else "",
                    "logic": cols[2] if len(cols) > 2 else "",
                    "source": cols[3] if len(cols) > 3 else "",
                })
            elif key == "risks" and len(cols) >= 4:
                result[key].append({
                    "type": cols[0],
                    "target": cols[1] if len(cols) > 1 else "",
                    "desc": cols[2] if len(cols) > 2 else "",
                    "source": cols[3] if len(cols) > 3 else "",
                })

    return result


def _extract_stock_name(text: str) -> str:
    """从表格单元格中提取股票名称（去除可能的代码括号）。"""
    # 匹配 "名称（代码）" 或 "名称(代码)" 格式
    m = re.match(r"([一-龥A-Za-z]{2,10})", text)
    return m.group(1) if m else text


def _extract_code(text: str) -> str:
    """从文本中提取 6 位数字代码。"""
    m = re.search(r"\b(\d{6})\b", text)
    return m.group(1) if m else ""


def _sector_stocks_to_text(stocks_value) -> str:
    """把 AI 返回的 sectors.stocks 统一成可拆分文本。"""
    if stocks_value is None:
        return ""
    if isinstance(stocks_value, str):
        return stocks_value.strip()
    if isinstance(stocks_value, list):
        parts = []
        for item in stocks_value:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("stock") or item.get("股票名称") or "").strip()
                code = str(item.get("code") or item.get("股票代码") or "").strip()
                text = f"{name} {code}".strip()
            else:
                text = str(item).strip()
            if text:
                parts.append(text)
        return "、".join(parts)
    if isinstance(stocks_value, dict):
        name = str(stocks_value.get("name") or stocks_value.get("stock") or stocks_value.get("股票名称") or "").strip()
        code = str(stocks_value.get("code") or stocks_value.get("股票代码") or "").strip()
        text = f"{name} {code}".strip()
        if text:
            return text
    return str(stocks_value).strip()


def _split_sector_stock_entries(sector_entry: dict) -> list[dict]:
    """把"细分板块机会"的核心标的拆成个股候选。

    AI 有时会把股票只放在 sectors.stocks，而 quantitative/elastic 为空。
    这里把这类核心标的补进弹性候选，避免最终报告只有空表。
    """
    if not isinstance(sector_entry, dict):
        return []
    stocks_text = _sector_stocks_to_text(sector_entry.get("stocks"))
    sector_name = (sector_entry.get("sector") or "").strip()
    if not stocks_text or not sector_name:
        return []

    normalized = re.sub(r"<br\s*/?>", "、", stocks_text, flags=re.IGNORECASE)
    normalized = re.sub(r"[（(](\d{6})[)）]", r" \1", normalized)
    raw_items = re.split(r"[、,，/；;|｜\n\r\t]+|(?:\s+和\s+)|(?:\s+及\s+)|(?:以及)", normalized)

    entries = []
    seen = set()
    for item in raw_items:
        item = re.sub(r"\*\*|`", "", item or "").strip()
        item = re.sub(r"^(?:核心标的|标的|包括|主要|关注)[:：\s]*", "", item)
        item = item.strip(" .。:：()（）[]【】")
        if not item:
            continue

        code = _extract_code(item)
        name = re.sub(r"\b\d{6}\b", "", item).strip(" .。:：()（）[]【】")
        name_match = re.match(r"([一-龥A-Za-z]{2,12})", name)
        name = name_match.group(1) if name_match else name

        if not name or len(name) < 2:
            continue
        if name in {"相关公司", "核心标的", "龙头公司", "上市公司", "板块"}:
            continue
        if re.search(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", name):
            continue

        key = code or name
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            "name": name,
            "code": code,
            "sector": sector_name,
            "logic": sector_entry.get("logic", ""),
            "risk": "",
            "source": sector_entry.get("source", ""),
        })

    return entries


def _assess_quality(target_str: str) -> float:
    """从量化参考文本评估信息质量（0.0 ~ 1.0）。

    有明确目标价/市值 = 高质量 (0.7-1.0)
    有逻辑但无量化 = 中等 (0.3-0.6)
    纯定性 = 较低 (0.1-0.3)
    """
    if not target_str:
        return 0.3
    score = 0.3
    # 有价格目标
    import re
    if re.search(r'\d+[\.\d]*\s*(?:元|块)', target_str):
        score += 0.3
    # 有市值目标
    if re.search(r'\d+[\.\d]*\s*(?:亿|e|E)', target_str):
        score += 0.2
    # 有 PE/PB/估值
    if re.search(r'(?:PE|PB|PS|估值|倍)', target_str, re.IGNORECASE):
        score += 0.1
    # 有增速/业绩
    if re.search(r'(?:增速|增长|利润|营收|EPS|业绩)', target_str, re.IGNORECASE):
        score += 0.1
    return min(1.0, score)


def _normalize_sector_name(sector: str, sector_aliases: dict) -> str:
    """将板块名标准化为规范名。

    用 sector_aliases 映射各种别名为标准板块名。
    返回标准化后的板块名，未匹配返回空字符串。
    """
    if not sector:
        return ""
    sector = sector.strip()
    # 直接匹配
    if sector in sector_aliases:
        return sector_aliases[sector]
    # 子字符串匹配
    for alias, canonical in sector_aliases.items():
        if alias in sector or sector in alias:
            return canonical
    return ""


def _parse_moat_score(value) -> float:
    """解析护城河评分（1-5 分映射到 0-10）。"""
    if isinstance(value, (int, float)) and 1 <= value <= 5:
        return round(value * 2, 1)
    return 5.0  # 默认中等


def _parse_target_value(target_str: str) -> Optional[float]:
    """从量化参考文本中提取数值（目标价/目标市值等）。

    支持格式：目标价150元 / 目标市值340亿 / 看200e+ / 目标150

    Returns:
        提取的数值，无法解析返回 None。
    """
    if not target_str:
        return None

    # 目标价 XX元 / 看到XX元 / 看高到XX元
    m = re.search(r"(?:目标价|看到|看[高到]|目标)[^\d]*(\d+[\.\d]*)\s*(?:元|块)?", target_str)
    if m:
        return float(m.group(1))

    # XX亿 / XXe（市值目标）
    m = re.search(r"(\d+[\.\d]*)\s*(?:亿|[eE]\b)", target_str)
    if m:
        return float(m.group(1))

    # 纯数字（如 "150"）
    m = re.search(r"(\d+[\.\d]+)", target_str)
    if m:
        return float(m.group(1))

    return None


def _apply_factor_orthogonalization(enriched: list[dict]) -> None:
    """对高相关因子做正交化降权。

    当两个因子相关系数 > 0.7 时，对 IC 更低的因子做降权，
    避免同一风险被重复计入。
    """
    if len(enriched) < 5:
        return

    # 定义需要检查相关性的因子对
    factor_pairs = [
        ("sector", "trend"),        # 板块热度 vs 行业趋势
        ("volume_confirm", "capital_flow"),  # 量价确认 vs 资金流
        ("upside", "logic"),        # 目标空间 vs 逻辑评分
    ]

    for f1_name, f2_name in factor_pairs:
        vals1 = [(s.get("score_detail") or {}).get(f1_name, 5) for s in enriched]
        vals2 = [(s.get("score_detail") or {}).get(f2_name, 5) for s in enriched]

        # 计算简单相关系数
        n = len(vals1)
        if n < 5:
            continue
        mean1 = sum(vals1) / n
        mean2 = sum(vals2) / n
        cov = sum((v1 - mean1) * (v2 - mean2) for v1, v2 in zip(vals1, vals2)) / n
        std1 = (sum((v1 - mean1) ** 2 for v1 in vals1) / n) ** 0.5
        std2 = (sum((v2 - mean2) ** 2 for v2 in vals2) / n) ** 0.5
        corr = cov / (std1 * std2) if std1 > 0 and std2 > 0 else 0

        # 高相关时对后一个因子做降权标记
        if abs(corr) > 0.7:
            for s in enriched:
                detail = s.get("score_detail", {})
                if f2_name in detail:
                    detail[f"{f2_name}_orthogonal_adj"] = round(detail[f2_name] * 0.6, 2)


def _calculate_style_exposure(enriched: list[dict]) -> dict:
    """计算当前候选池的风格暴露。

    分析候选股票在以下维度的暴露：
    - 动量（momentum）：变化率和位置
    - 价值（value）：PE/PB
    - 成长（growth）：趋势分数
    - 波动（volatility）：ATR
    - 规模（size）：市值

    Returns:
        {style: {"exposure": float, "direction": str}}
    """
    if not enriched:
        return {}

    n = len(enriched)

    # 动量暴露
    changes = [s.get("score_detail", {}).get("trend", 5) for s in enriched if s.get("score_detail")]
    avg_momentum = sum(changes) / len(changes) if changes else 5

    # 价值暴露（低 PE = 高价值）
    pes = [s.get("pe", 30) for s in enriched if s.get("pe") is not None and s.get("pe") > 0]
    avg_pe = sum(pes) / len(pes) if pes else 30
    value_score = max(0, min(10, 10 - avg_pe / 10))  # PE 越低，价值分越高

    # 成长暴露
    trends = [s.get("trend_score", 5) for s in enriched]
    avg_growth = sum(trends) / len(trends) if trends else 5

    # 波动率暴露
    atrs = []
    for s in enriched:
        tech = s.get("technical", {})
        atr = tech.get("atr_14")
        price = s.get("current_price", 0)
        if atr and price and price > 0:
            atrs.append(atr / price * 100)
    avg_vol = sum(atrs) / len(atrs) if atrs else 2.5

    # 规模暴露
    caps = [s.get("market_cap_yi", 200) for s in enriched if s.get("market_cap_yi")]
    avg_cap = sum(caps) / len(caps) if caps else 200
    size_score = min(10, max(0, avg_cap / 200))  # 市值越大分越高

    return {
        "momentum": {"exposure": round(avg_momentum, 2), "direction": "positive" if avg_momentum > 5 else "negative"},
        "value": {"exposure": round(value_score, 2), "direction": "value" if value_score > 5 else "growth"},
        "growth": {"exposure": round(avg_growth, 2), "direction": "positive" if avg_growth > 5 else "neutral"},
        "volatility": {"exposure": round(avg_vol, 2), "direction": "high" if avg_vol > 3 else "low"},
        "size": {"exposure": round(size_score, 2), "direction": "large" if size_score > 5 else "small"},
    }


def _normalize_factors_cross_section(all_stocks: dict) -> None:
    """对候选池内所有股票的原始因子值做截面排名归一化。

    将每个因子的原始值转换为截面百分位排名（0-1），
    使得不同量纲的因子可以公平加权。
    直接修改 all_stocks 中的 stock 字典，添加 norm_ 前缀字段。
    """
    if len(all_stocks) < 3:
        # 候选太少，跳过归一化
        for stock in all_stocks.values():
            stock["norm_upside"] = stock.get("quality", 0.5)
            stock["norm_quality"] = stock.get("quality", 0.5)
        return

    # 收集各因子原始值
    factor_values = {
        "upside": [],
        "quality": [],
        "consensus_potential": [],
    }
    for stock in all_stocks.values():
        factor_values["upside"].append(stock.get("target_value") or 0)
        factor_values["quality"].append(stock.get("quality", 0.3))
        factor_values["consensus_potential"].append(stock.get("post_count", 1))

    # 计算百分位排名
    def _percentile_rank(values, x):
        """计算 x 在 values 中的百分位排名（0-1）。"""
        below = sum(1 for v in values if v < x)
        equal = sum(1 for v in values if v == x)
        return (below + 0.5 * equal) / len(values) if values else 0.5

    for stock in all_stocks.values():
        tv = stock.get("target_value") or 0
        q = stock.get("quality", 0.3)
        pc = stock.get("post_count", 1)
        stock["norm_upside"] = round(_percentile_rank(factor_values["upside"], tv), 3)
        stock["norm_quality"] = round(_percentile_rank(factor_values["quality"], q), 3)
        stock["norm_consensus"] = round(_percentile_rank(factor_values["consensus_potential"], pc), 3)


def _enrich_and_score(stocks_json: dict, verbose: bool = True) -> tuple[list[dict], dict]:
    """增强股票数据：获取实时行情，计算推荐指数。

    Returns:
        按推荐指数降序排列的增强股票列表。
    """
    # 收集所有有代码的股票
    all_stocks = {}
    for entry in stocks_json.get("quantitative", []):
        code = entry.get("code", "").strip()
        name = entry.get("name", "")
        if name and _is_a_share_candidate(entry):
            key = code if code else name
            # AI 置信度：1-5 分，映射到 0.2-1.0
            ai_confidence = entry.get("confidence", 3)
            if isinstance(ai_confidence, (int, float)) and 1 <= ai_confidence <= 5:
                confidence_weight = 0.2 + (ai_confidence - 1) * 0.2
            else:
                confidence_weight = 0.6

            if key not in all_stocks:
                _author = (entry.get("author") or "").strip()
                all_stocks[key] = {
                    "name": name, "code": code,
                    "logic": entry.get("logic", ""),
                    "target_str": entry.get("target", ""),
                    "target_value": _parse_target_value(entry.get("target", "")),
                    "target_aggressive": entry.get("target_aggressive", ""),
                    "target_moderate": entry.get("target_moderate", ""),
                    "target_conservative": entry.get("target_conservative", ""),
                    "risk_str": entry.get("risk", ""),
                    "moat_type": entry.get("moat", ""),
                    "moat_score": _parse_moat_score(entry.get("moat_score")),
                    "management": entry.get("management", ""),
                    "source": entry.get("source", ""),
                    "category": "quantitative",
                    "sector": entry.get("sector", ""),
                    "post_count": 1,
                    "authors": {_author} if _author else set(),
                    "quality": _assess_quality(entry.get("target", "")),
                    "foreign_research": bool(entry.get("foreign_research")),
                    "source_note": entry.get("source_note", ""),
                    "ai_confidence": confidence_weight,
                }
            else:
                # 合并重复股票
                existing = all_stocks[key]
                existing["post_count"] += 1
                _author = (entry.get("author") or "").strip()
                if _author:
                    existing.setdefault("authors", set()).add(_author)
                if not existing["target_str"] and entry.get("target"):
                    existing["target_str"] = entry.get("target", "")
                    existing["target_value"] = _parse_target_value(entry.get("target", ""))
                    existing["quality"] = _assess_quality(entry.get("target", ""))
                existing["risk_str"] = _merge_text(
                    existing.get("risk_str", ""), entry.get("risk", "")
                )
                if entry.get("foreign_research"):
                    existing["foreign_research"] = True
                    existing["source_note"] = entry.get("source_note", "国外投行研报")

    for entry in stocks_json.get("elastic", []):
        code = entry.get("code", "").strip()
        name = entry.get("name", "")
        sector = entry.get("sector", "")
        if name and _is_a_share_candidate(entry):
            key = code if code else name
            ai_confidence = entry.get("confidence", 3)
            if isinstance(ai_confidence, (int, float)) and 1 <= ai_confidence <= 5:
                confidence_weight = 0.2 + (ai_confidence - 1) * 0.2
            else:
                confidence_weight = 0.5

            if key not in all_stocks:
                _author = (entry.get("author") or "").strip()
                all_stocks[key] = {
                    "name": name, "code": code,
                    "logic": entry.get("logic", ""),
                    "target_str": "", "target_value": None,
                    "target_aggressive": entry.get("target_aggressive", ""),
                    "target_moderate": entry.get("target_moderate", ""),
                    "target_conservative": entry.get("target_conservative", ""),
                    "risk_str": entry.get("risk", ""),
                    "moat_type": entry.get("moat", ""),
                    "moat_score": _parse_moat_score(entry.get("moat_score")),
                    "management": entry.get("management", ""),
                    "source": entry.get("source", ""),
                    "category": "elastic",
                    "sector": sector,
                    "post_count": 1,
                    "authors": {_author} if _author else set(),
                    "quality": 0.3,  # 定性推荐，信息质量较低
                    "foreign_research": bool(entry.get("foreign_research")),
                    "source_note": entry.get("source_note", ""),
                    "ai_confidence": confidence_weight,
                }
            else:
                all_stocks[key]["post_count"] += 1
                _author = (entry.get("author") or "").strip()
                if _author:
                    all_stocks[key].setdefault("authors", set()).add(_author)
                if sector and not all_stocks[key]["sector"]:
                    all_stocks[key]["sector"] = sector
                all_stocks[key]["risk_str"] = _merge_text(
                    all_stocks[key].get("risk_str", ""), entry.get("risk", "")
                )
                if entry.get("foreign_research"):
                    all_stocks[key]["foreign_research"] = True
                    all_stocks[key]["source_note"] = entry.get("source_note", "国外投行研报")

    # 用细分板块表回填量化标的的赛道，便于趋势评分和板块风险匹配
    for sector_entry in stocks_json.get("sectors", []):
        if not isinstance(sector_entry, dict):
            continue
        sector_name = sector_entry.get("sector", "")
        stocks_text = _sector_stocks_to_text(sector_entry.get("stocks"))
        if not sector_name or not stocks_text:
            continue
        for stock in all_stocks.values():
            if not stock.get("sector") and stock.get("name") in stocks_text:
                stock["sector"] = sector_name

        for entry in _split_sector_stock_entries(sector_entry):
            if not _is_a_share_candidate(entry):
                continue
            code = entry.get("code", "").strip()
            name = entry.get("name", "")
            key = code if code else name
            if not key or key in all_stocks:
                continue
            _author = (entry.get("author") or "").strip()
            all_stocks[key] = {
                "name": name,
                "code": code,
                "logic": entry.get("logic", ""),
                "target_str": "",
                "target_value": None,
                "risk_str": entry.get("risk", ""),
                "source": entry.get("source", ""),
                "category": "elastic",
                "sector": entry.get("sector", ""),
                "post_count": 1,
                "authors": {_author} if _author else set(),
                "quality": 0.25,
                "foreign_research": bool(entry.get("foreign_research")),
                "source_note": entry.get("source_note", ""),
            }

    if not all_stocks:
        return [], {}

    # 用 sector_aliases 关键词从逻辑文本中推断板块
    for stock in all_stocks.values():
        if stock.get("sector"):
            continue
        text = f"{stock.get('logic', '')} {stock.get('target_str', '')} {stock.get('source', '')}"
        if not text.strip():
            continue
        for alias, canonical in sector_aliases.items():
            if len(alias) >= 2 and alias in text:
                stock["sector"] = canonical
                break

    if verbose:
        no_sector = sum(1 for s in all_stocks.values() if not s.get("sector"))
        if no_sector:
            print(f"  板块推断: {len(all_stocks) - no_sector}/{len(all_stocks)} 只有关联板块，{no_sector} 只无板块", flush=True)

    # 批量获取价格
    valid_codes = [s["code"] for s in all_stocks.values() if s["code"] and s["code"].isdigit() and len(s["code"]) == 6]
    if valid_codes and verbose:
        print(f"  获取 {len(valid_codes)} 只 A 股实时行情...", flush=True)

    prices = {}
    changes_5d = {}
    technicals = {}
    external_market = {}
    if valid_codes:
        from price_fetcher import (
            fetch_5day_changes,
            fetch_market_environment,
            fetch_prices,
            fetch_technical_indicators,
        )
        prices = fetch_prices(valid_codes)
        changes_5d = fetch_5day_changes(valid_codes)
        technicals = fetch_technical_indicators(valid_codes)
        external_market = fetch_market_environment()

    if verbose and prices:
        print(f"  成功获取 {len(prices)} 只股票行情", flush=True)
    if verbose and changes_5d:
        print(f"  成功获取 {len(changes_5d)} 只股票 5 日涨跌幅", flush=True)
    if verbose and technicals:
        print(f"  成功获取 {len(technicals)} 只股票技术指标", flush=True)

    for stock in all_stocks.values():
        code = stock.get("code", "")
        stock["change_5d"] = changes_5d.get(code) if code else None

    # 计算板块热度（sectors 中的 stocks 字符串被提及的总字符数作为代理）
    sector_heat = {}
    for entry in stocks_json.get("sectors", []):
        if not isinstance(entry, dict):
            continue
        sector_name = entry.get("sector", "")
        stocks_str = _sector_stocks_to_text(entry.get("stocks"))
        if sector_name:
            sector_heat[sector_name] = len(stocks_str)

    # 加载评分配置权重（支持市场状态动态调整 + 自适应权重）
    scoring = _load_scoring_config()

    # 尝试加载自适应权重
    adaptive_weights = None
    try:
        from adaptive_weights import get_latest_weights
        adaptive_weights = get_latest_weights()
    except Exception:
        pass

    # 市场状态检测
    market_regime_config = {}
    try:
        from market_regime import detect_market_regime, get_scoring_weights
        market_regime_config = detect_market_regime(
            market=external_market if external_market.get("level") else {},
            breadth={},
            external_market=external_market,
        )
        regime_weights = get_scoring_weights(market_regime_config)
        if verbose:
            print(f"  市场状态: {market_regime_config.get('label', '未知')}（{market_regime_config.get('score', 0)}分）", flush=True)
    except Exception:
        regime_weights = {}

    # 优先级：自适应权重 > 市场状态权重 > 静态配置
    if adaptive_weights:
        w_upside = adaptive_weights.get("upside", scoring.get("upside_weight", 0.22))
        w_quality = adaptive_weights.get("quality", scoring.get("quality_weight", 0.16))
        w_consensus = adaptive_weights.get("consensus", scoring.get("consensus_weight", 0.12))
        w_sector = adaptive_weights.get("sector", scoring.get("sector_weight", 0.12))
        w_trend = adaptive_weights.get("trend", scoring.get("trend_weight", 0.08))
        w_fundamentals = adaptive_weights.get("fundamentals", scoring.get("fundamentals_weight", 0.08))
        w_capital_flow = adaptive_weights.get("capital_flow", scoring.get("capital_flow_weight", 0.08))
        w_volume_confirm = adaptive_weights.get("volume_confirm", scoring.get("volume_confirm_weight", 0.07))
        w_logic_adj = adaptive_weights.get("logic", scoring.get("logic_weight", 0.07))
        if verbose:
            print(f"  权重来源: 自适应（IC 驱动）", flush=True)
    else:
        w_upside = regime_weights.get("upside", scoring.get("upside_weight", 0.22))
        w_quality = regime_weights.get("quality", scoring.get("quality_weight", 0.16))
        w_consensus = regime_weights.get("consensus", scoring.get("consensus_weight", 0.12))
        w_sector = regime_weights.get("sector", scoring.get("sector_weight", 0.12))
        w_trend = regime_weights.get("trend", scoring.get("trend_weight", 0.08))
        w_fundamentals = regime_weights.get("fundamentals", scoring.get("fundamentals_weight", 0.08))
        w_capital_flow = regime_weights.get("capital_flow", scoring.get("capital_flow_weight", 0.08))
        w_volume_confirm = regime_weights.get("volume_confirm", scoring.get("volume_confirm_weight", 0.07))
        w_logic_adj = regime_weights.get("logic", scoring.get("logic_weight", 0.07))
        if regime_weights:
            if verbose:
                print(f"  权重来源: 市场状态（{market_regime_config.get('label', '未知')}）", flush=True)
        else:
            if verbose:
                print(f"  权重来源: 静态配置", flush=True)

    # 获取龙虎榜数据（用于资金流评分）
    lhb_code_map = {}
    try:
        from market_review import fetch_lhb_details
        lhb_data = fetch_lhb_details(max_days=5)
        for row in lhb_data.get("rows", []):
            lhb_code = (row.get("code") or "").strip()
            if lhb_code:
                lhb_code_map[lhb_code] = row
        if verbose and lhb_code_map:
            print(f"  龙虎榜数据: {len(lhb_code_map)} 只近期上榜股票", flush=True)
    except Exception as exc:
        if verbose:
            print(f"  龙虎榜数据获取失败（不影响主流程）: {exc}", flush=True)

    # 行业趋势检测
    sector_aliases = scoring.get("sector_aliases", {})
    trend_config = scoring.get("trend", {})
    trend_scores, sector_groups, sector_logic_map = _detect_sector_trends(
        all_stocks, stocks_json.get("sectors", []),
        sector_heat, sector_aliases, trend_config,
    )

    # 构建作者可信度画像
    author_stats = _build_author_stats(all_stocks)

    # 计算时间加权因子
    for stock in all_stocks.values():
        stock["recency_weight"] = _compute_recency_weight(stock)
        stock["author_credibility"] = _get_author_credibility(stock, author_stats)
    if verbose and trend_scores:
        trending = [(s, ts) for s, ts in trend_scores.items() if ts >= 5.0]
        if trending:
            trending.sort(key=lambda x: x[1], reverse=True)
            names = ", ".join(f"{s}({ts})" for s, ts in trending)
            print(f"  行业趋势检测: {names}", flush=True)

    # 因子截面归一化：对所有候选股票的因子值做截面排名
    _normalize_factors_cross_section(all_stocks)

    # 聪明钱评分：获取个股资金流向（用于个股级聪明钱信号）
    stock_money_flow = {}
    try:
        from price_fetcher import fetch_money_flow
        valid_codes_for_flow = [s["code"] for s in all_stocks.values() if s["code"]]
        if valid_codes_for_flow:
            stock_money_flow = fetch_money_flow(valid_codes_for_flow[:50])  # 限制数量避免超时
            if verbose:
                print(f"  个股资金流向: {len(stock_money_flow)} 只", flush=True)
    except Exception as exc:
        if verbose:
            print(f"  个股资金流向获取失败: {exc}", flush=True)

    # 计算评分
    enriched = []
    for key, stock in all_stocks.items():
        code = stock["code"]
        price_info = prices.get(code) if code else None

        current_price = price_info["price"] if price_info else None
        pe = price_info["pe"] if price_info else None
        pb = price_info["pb"] if price_info else None
        market_cap = price_info["market_cap_yi"] if price_info else None
        change_5d = changes_5d.get(code) if code else None
        technical = technicals.get(code, {}) if code else {}

        # 上涨空间计算
        upside_pct = None
        if current_price and current_price > 0 and stock["target_value"]:
            target = stock["target_value"]
            # 判断目标值是"元"还是"亿"（市值目标）
            target_str = stock["target_str"]
            if "亿" in target_str or "e" in target_str.lower():
                # 市值目标：上涨空间 = (目标市值 / 当前市值 - 1) * 100
                if market_cap and market_cap > 0:
                    upside_pct = round((target / market_cap - 1) * 100, 1)
            elif "元" in target_str or "块" in target_str:
                # 价格目标：上涨空间 = (目标价 / 当前价 - 1) * 100
                upside_pct = round((target / current_price - 1) * 100, 1)

        # 推荐指数计算（1-10 分制）
        # 1. 目标弹性得分（0-10）：目标价/目标市值只参与内部评分，不在报告中直接展示上涨空间。
        upside_score = 0
        if upside_pct is not None:
            # 采用非线性映射，避免 30% 以上全部挤到满分。
            upside_score = min(10, max(0, 10 * (1 - math.exp(-upside_pct / 45))))

        # 2. 信息质量得分（0-10）
        quality_score = stock["quality"] * 10

        # 3. 分析师共识得分（0-10）— 时间加权 + 作者可信度 + 独立作者计数
        post_count = stock["post_count"]
        unique_authors = len(stock.get("authors", set()))
        recency_weight = stock.get("recency_weight", 1.0)
        author_cred = stock.get("author_credibility", 0.5)

        # 基础共识分（按独立作者数计分，避免同一作者多篇重复加分）
        if unique_authors >= 4:
            base_consensus = 8.5
        elif unique_authors == 3:
            base_consensus = 7.0
        elif unique_authors == 2:
            base_consensus = 5.5
        elif post_count >= 3:
            # 同一作者多篇推荐：给基础分但不额外加成
            base_consensus = 3.5
        elif post_count >= 2:
            base_consensus = 3.0
        else:
            base_consensus = 2.0

        # 时间加权：最近提及权重更高
        consensus_score = base_consensus * recency_weight

        # 作者可信度加成：高可信度作者的提及更有价值
        consensus_score = consensus_score * (0.7 + 0.3 * author_cred)

        consensus_score = max(0, min(10, consensus_score))

        # 4. 板块热度得分（0-10）
        sector_score = 0
        if stock["sector"]:
            heat = sector_heat.get(stock["sector"], 0)
            sector_score = min(10, 1.5 + math.sqrt(max(0, heat)) / 1.8)

        # 5. 行业趋势得分（0-10）
        trend_score = 0.0
        norm_sec = _normalize_sector_name(stock.get("sector", ""), sector_aliases)
        if norm_sec and norm_sec in trend_scores:
            trend_score = trend_scores[norm_sec]

        # 6. 公司基本面得分（0-10）
        fundamentals_score = _fundamentals_score(pe, pb, market_cap)

        # 7. 资金流得分（0-10）
        cap_flow_score = _capital_flow_score(code, lhb_code_map)

        # 8. 量价确认得分（0-10）
        vol_confirm_score = _volume_confirm_score(technical)

        logic_score = _sentiment_score(stock.get("logic", ""))
        target_precision = _target_precision_score(stock.get("target_str", ""))

        base_score = (
            w_upside * upside_score
            + w_quality * quality_score
            + w_consensus * consensus_score
            + w_sector * sector_score
            + w_trend * trend_score
            + w_fundamentals * fundamentals_score
            + w_capital_flow * cap_flow_score
            + w_volume_confirm * vol_confirm_score
        )
        total_score = _calibrate_recommendation_score(
            base_score=base_score,
            logic_score=logic_score,
            target_precision=target_precision,
            post_count=stock["post_count"],
            category=stock["category"],
            unique_authors=unique_authors,
        )

        # P2 调整：护城河评分加成（宽护城河 = 更强确定性\n")
        moat_score = stock.get("moat_score", 5.0)
        if moat_score >= 8.0:
            total_score = round(min(10.0, total_score + 0.5), 1)
        elif moat_score >= 6.0:
            total_score = round(min(10.0, total_score + 0.2), 1)

        # P2 调整：长期趋势/涨价预期加分
        lt_trend = _long_term_trend_score(
            stock.get("logic", ""),
            stock.get("target_str", ""),
            stock.get("risk_str", ""),
        )
        if lt_trend >= 7.0:
            total_score = round(min(10.0, total_score + 1.5), 1)
        elif lt_trend >= 5.0:
            total_score = round(min(10.0, total_score + 0.8), 1)

        # P2 调整：负面信号扣分
        neg_penalty, neg_signals = _detect_negative_signals(
            stock.get("logic", ""), stock.get("risk_str", ""), stock.get("source", "")
        )
        if neg_penalty < 0:
            total_score = max(1.0, total_score + neg_penalty)

        # P2 调整：信息新鲜度衰减（超过半衰期的推荐降分）
        time_decay = _time_decay_factor(
            stock.get("generated_at", ""), stock.get("opportunity_type", "")
        )
        if time_decay < 0.8:
            total_score = round(max(1.0, total_score * (0.7 + 0.3 * time_decay)), 1)

        # P2 调整：作者可信度加成
        author_bonus = _author_credibility_score(stock.get("source", ""))
        if author_bonus > 0:
            total_score = round(min(10.0, total_score + author_bonus * 0.3), 1)

        # P2 调整：聪明钱信号调整 — 主力净流入
        smart_adj = _smart_money_adjustment(stock, stock_money_flow)
        if smart_adj != 0:
            total_score = round(max(1.0, min(10.0, total_score + smart_adj)), 1)

        # 生成星级
        stars = _score_to_stars(total_score)
        price_available = price_info is not None

        # AI 置信度加权：低置信度的股票得分打折
        ai_conf = stock.get("ai_confidence", 0.6)
        if ai_conf < 0.5:
            total_score = round(total_score * (0.7 + 0.3 * ai_conf), 1)

        stock_view = {
            **stock,
            "negative_signals": neg_signals,
            "negative_penalty": neg_penalty,
            "time_decay": time_decay,
            "author_bonus": author_bonus,
            "current_price": current_price,
            "pe": pe,
            "pb": pb,
            "market_cap_yi": market_cap,
            "change_5d": change_5d,
            "upside_pct": upside_pct,
            "score": total_score,
            "stars": stars,
            "price_available": price_available,
            "trend_score": round(trend_score, 1),
            "trending_sector": norm_sec if trend_score >= 5.0 else "",
            "fundamentals_score": round(fundamentals_score, 1),
            "moat_type": stock.get("moat_type", ""),
            "moat_score": stock.get("moat_score", 5.0),
            "management": stock.get("management", ""),
            "target_aggressive": stock.get("target_aggressive", ""),
            "target_moderate": stock.get("target_moderate", ""),
            "target_conservative": stock.get("target_conservative", ""),
            "technical": technical,
        }
        technical_score, technical_view = _technical_buy_score(stock_view)
        stock_view["technical_score"] = technical_score
        stock_view["technical_view"] = technical_view
        risk_display = _build_stock_risk(
            stock_view, stocks_json.get("risks", []), sector_aliases
        )
        stock_view["risk_display"] = risk_display
        stock_view["buy_score"] = _buy_score(stock_view)
        stock_view["entry_ref"] = _technical_buy_reference(stock_view)
        stock_view["action"] = _selection_action(stock_view)
        stock_view["trade_period"] = _trade_period(stock_view)
        stock_view["exit_trigger"] = _exit_trigger(stock_view)

        enriched.append({
            **stock_view,
            "score_detail": {
                "upside": round(upside_score, 1),
                "quality": round(quality_score, 1),
                "consensus": round(consensus_score, 1),
                "sector": round(sector_score, 1),
                "trend": round(trend_score, 1),
                "fundamentals": round(fundamentals_score, 1),
                "capital_flow": round(cap_flow_score, 1),
                "volume_confirm": round(vol_confirm_score, 1),
                "logic": round(logic_score, 1),
                "target": round(target_precision, 1),
                "long_term_trend": round(lt_trend, 1),
            },
        })

    # 板块拥挤度惩罚 — 同板块多标的时后排降分（避免报告变成板块堆叠）
    _apply_crowding_penalty(enriched)

    # 按推荐指数降序排列
    market_filter = _market_filter(enriched, external_market)
    for stock in enriched:
        stock["market_filter"] = market_filter
        stock["buy_score"] = _buy_score(stock)
        stock["entry_ref"] = _technical_buy_reference(stock)
        stock["action"] = _selection_action(stock)
        stock["trade_period"] = _trade_period(stock)
        stock["exit_trigger"] = _exit_trigger(stock)
        _apply_expert_decision_fields(stock)

    # 按 buy_score 排序（综合了逻辑推荐度 + 技术买点质量）
    enriched.sort(key=lambda x: (x.get("buy_score", 0), x.get("score", 0)), reverse=True)

    # 因子正交化：对高相关因子组做降权，避免重复计入相同风险
    _apply_factor_orthogonalization(enriched)

    # 计算组合风格暴露
    style_exposure = _calculate_style_exposure(enriched)

    # 构建趋势数据供报告层使用
    trend_data = {
        "scores": trend_scores,
        "groups": sector_groups,
        "logic_map": sector_logic_map,
        "market_filter": market_filter,
        "market_regime": market_regime_config,
        "style_exposure": style_exposure,
    }
    return enriched, trend_data


_NEGATIVE_SIGNAL_KEYWORDS = [
    # 财务风险
    "立案", "处罚", "退市", "ST", "*ST", "暂停上市", "终止上市",
    "亏损", "暴雷", "爆雷", "计提", "减值", "商誉",
    # 经营风险
    "减持", "清仓减持", "大股东减持", "质押", "冻结", "诉讼", "仲裁",
    "违规", "造假", "财务造假", "信披违规",
    # 行业风险
    "产能过剩", "价格战", "政策收紧", "监管趋严",
    # 技术面风险
    "跌停", "闪崩", "断崖", "崩盘",
]


def _apply_crowding_penalty(enriched: list[dict]) -> None:
    """对同板块后排标的施加拥挤度惩罚。

    同一板块内按得分排序，排名第 3 及以后的标的按位置递减扣分。
    避免报告被同一板块的低质量标的堆叠。
    """
    from collections import defaultdict
    sector_groups = defaultdict(list)
    for stock in enriched:
        sector = stock.get("trending_sector") or stock.get("sector", "")
        if sector:
            sector_groups[sector].append(stock)

    for sector, stocks in sector_groups.items():
        if len(stocks) <= 2:
            continue
        # 按当前得分排序
        stocks.sort(key=lambda x: x.get("score", 0), reverse=True)
        for i, stock in enumerate(stocks):
            if i >= 2:
                # 第3名 -0.3, 第4名 -0.6, 第5名 -0.9...
                penalty = -0.3 * (i - 1)
                stock["score"] = round(max(1.0, stock.get("score", 5.0) + penalty), 1)
                stock["crowding_penalty"] = penalty
                # 更新星级
                stock["stars"] = _score_to_stars(stock["score"])


def _compute_crowding_penalty(stock: dict, sector_rank: int) -> float:
    """计算单只股票的拥挤度惩罚值。"""
    if sector_rank <= 2:
        return 0.0
    return -0.3 * (sector_rank - 2)


def _detect_negative_signals(logic: str, risk_str: str, source: str = "") -> tuple[float, list[str]]:
    """检测负面信号，返回扣分值和信号列表。

    Returns:
        (penalty, signals): penalty 为负分扣减值（0 到 -3），signals 为检测到的负面信号列表。
    """
    text = f"{logic} {risk_str} {source}"
    detected = []
    for kw in _NEGATIVE_SIGNAL_KEYWORDS:
        if kw in text:
            detected.append(kw)
    if not detected:
        return 0.0, []
    # 每个负面信号 -0.5，最多 -3
    penalty = max(-3.0, -0.5 * len(detected))
    return penalty, detected[:5]


def _time_decay_factor(generated_at: str, opportunity_type: str = "") -> float:
    """计算信息新鲜度衰减因子（0.0 ~ 1.0）。

    事件驱动型机会半衰期 3 天，趋势驱动 14 天，研报驱动 30 天。
    """
    if not generated_at:
        return 0.5
    try:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        gen_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        if gen_time.tzinfo is None:
            gen_time = gen_time.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        days_elapsed = (now - gen_time).total_seconds() / 86400
    except Exception:
        return 0.5

    # 半衰期按机会类型区分
    if opportunity_type in ("事件驱动", "event"):
        half_life = 3.0
    elif opportunity_type in ("研报驱动", "research"):
        half_life = 30.0
    else:
        half_life = 14.0

    # 指数衰减
    factor = math.exp(-0.693 * days_elapsed / half_life)
    return round(max(0.1, min(1.0, factor)), 2)


def _author_credibility_score(source: str, author_stats: dict = None) -> float:
    """基于帖子来源计算作者可信度加成（0.0 ~ 1.5）。

    当前实现：基于来源帖子数量的简单加成。
    后续可扩展为基于历史推荐准确率的动态评分。
    """
    if not source:
        return 0.0
    # 提取来源帖子数量
    import re
    post_numbers = re.findall(r"帖子\s*(\d+)", source)
    count = len(post_numbers)
    if count >= 3:
        return 1.0
    if count >= 2:
        return 0.5
    return 0.0


def _build_author_stats(all_stocks: dict) -> dict:
    """构建作者可信度统计。

    统计每个来源帖子被提及的次数和涉及的股票数，
    用于后续作者可信度评分。
    """
    source_counts = {}
    for stock in all_stocks.values():
        source = stock.get("source", "")
        if not source:
            continue
        import re
        post_numbers = re.findall(r"帖子\s*(\d+)", source)
        for num in post_numbers:
            key = f"帖子{num}"
            if key not in source_counts:
                source_counts[key] = {"mentions": 0, "stocks": set()}
            source_counts[key]["mentions"] += 1
            source_counts[key]["stocks"].add(stock.get("name", ""))

    # 转为可序列化格式
    stats = {}
    for key, val in source_counts.items():
        stats[key] = {
            "mentions": val["mentions"],
            "stock_count": len(val["stocks"]),
        }
    return stats


def _compute_recency_weight(stock: dict) -> float:
    """计算时间加权因子（0.5 ~ 1.0）。

    最近 3 天内的提及权重为 1.0，之后按半衰期 7 天衰减。
    """
    import re
    from datetime import datetime, timedelta
    source = stock.get("source", "")
    post_numbers = re.findall(r"帖子\s*(\d+)", source)
    if not post_numbers:
        return 0.7  # 无来源信息给中间权重

    # 简化：用 post_count 作为时间代理
    # 多次提及 = 近期持续关注，权重更高
    post_count = stock.get("post_count", 1)
    if post_count >= 4:
        return 1.0
    elif post_count >= 3:
        return 0.9
    elif post_count >= 2:
        return 0.8
    return 0.7


def _get_author_credibility(stock: dict, author_stats: dict) -> float:
    """获取作者可信度（0.0 ~ 1.0）。

    基于独立作者数和来源质量：
    - 多位独立作者推荐 = 真正的共识
    - 量化目标类推荐 = 有明确研究 = 更高可信度
    - 国外投行研报加分
    """
    base = 0.5

    # 量化目标类推荐加分
    if stock.get("category") == "quantitative" and stock.get("target_str"):
        base += 0.2

    # 独立作者数加成（比单纯 post_count 更准确）
    unique_authors = len(stock.get("authors", set()))
    if unique_authors >= 3:
        base += 0.2
    elif unique_authors == 2:
        base += 0.12
    elif stock.get("post_count", 1) >= 2:
        # 同一作者多篇只给少量加成
        base += 0.05

    # 国外投行研报加分
    if stock.get("foreign_research"):
        base += 0.1

    return min(1.0, base)


def _capital_flow_score(code: str, lhb_code_map: dict) -> float:
    """基于龙虎榜数据计算资金流得分（0-10）。

    - 近5日龙虎榜净买入：+3 分
    - 龙虎榜成交占比 > 10%：+2 分（机构参与度高）
    - 基础分 5 分（中性）
    """
    score = 5.0
    row = lhb_code_map.get(code)
    if not row:
        return score
    net_yi = row.get("net_yi") or 0
    deal_ratio = row.get("deal_ratio") or 0
    if net_yi > 0:
        score += 3.0
    elif net_yi < 0:
        score -= 2.0
    if deal_ratio > 10:
        score += 2.0
    elif deal_ratio > 5:
        score += 1.0
    return max(0.0, min(10.0, score))


def _smart_money_adjustment(stock: dict, money_flow: dict = None) -> float:
    """基于主力净流入的个股评分调整（-1.5 ~ +1.5）。

    仅使用个股级信号（主力净流入）。

    Returns:
        分数调整值（-1.5 到 +1.5）。
    """
    adjustment = 0.0

    # 个股级信号：主力净流入
    code = stock.get("code", "")
    flow = (money_flow or {}).get(code, {})
    main_net = flow.get("main_net_inflow", 0)
    if main_net > 1.0:
        adjustment += 0.5  # 主力大幅净流入
    elif main_net > 0.3:
        adjustment += 0.25
    elif main_net < -1.0:
        adjustment -= 0.5
    elif main_net < -0.3:
        adjustment -= 0.25

    return max(-1.5, min(1.5, adjustment))


def _fundamentals_score(pe: Optional[float], pb: Optional[float], market_cap: Optional[float]) -> float:
    """基于 PE/PB/市值的基本面评分（0-10）。

    评分逻辑：
    - PE 越低越好（盈利能力强），负 PE（亏损）得最低分
    - PB 越低越好（资产扎实），<1 可能破净
    - 大市值加分（流动性好、机构关注度高）
    """
    score = 5.0

    # PE 评分（越低越好）
    if pe is not None:
        if pe <= 0:
            score -= 2.0  # 亏损
        elif pe <= 15:
            score += 2.5  # 低估
        elif pe <= 25:
            score += 1.5
        elif pe <= 40:
            score += 0.5
        elif pe <= 60:
            score -= 0.5
        else:
            score -= 1.5  # 高估

    # PB 评分（越低越好）
    if pb is not None:
        if pb <= 0:
            score -= 1.0  # 异常
        elif pb <= 1:
            score += 1.5  # 破净或接近破净
        elif pb <= 2:
            score += 1.0
        elif pb <= 4:
            score += 0.0
        elif pb <= 6:
            score -= 0.5
        else:
            score -= 1.0  # 高 PB

    # 市值评分（大市值 = 流动性好 + 机构关注）
    if market_cap is not None:
        if market_cap >= 1000:
            score += 1.0  # 大盘股
        elif market_cap >= 300:
            score += 0.5  # 中大盘
        elif market_cap >= 100:
            score += 0.0  # 中盘
        elif market_cap >= 50:
            score -= 0.3  # 小盘
        else:
            score -= 0.8  # 微盘，流动性风险

    return max(0.0, min(10.0, score))


def _volume_confirm_score(technical: dict) -> float:
    """量价确认信号评分（0-10）。

    评估成交量对价格趋势的确认程度：
    - 放量上涨 = 趋势确认
    - 缩量上涨 / 放量下跌 = 趋势不确认
    - 温和放量 + 均线附近 = 最佳建仓点
    """
    if not technical:
        return 5.0

    score = 5.0
    change_5d = technical.get("change_5d")
    volume_ratio = technical.get("volume_ratio")
    ma_bullish = technical.get("ma_bullish", False)
    above_ma20 = technical.get("above_ma20", False)

    # 量价配合
    if change_5d is not None and volume_ratio is not None:
        if change_5d > 3 and volume_ratio > 1.2:
            score += 2.0  # 放量上涨，趋势确认
        elif change_5d > 3 and volume_ratio < 0.8:
            score -= 1.5  # 缩量上涨，上涨不稳固
        elif change_5d < -3 and volume_ratio > 1.5:
            score -= 1.5  # 放量下跌
        elif change_5d < -3 and volume_ratio < 0.8:
            score += 0.5  # 缩量下跌，恐慌宣泄
        elif abs(change_5d) <= 3 and 0.8 <= volume_ratio <= 1.3:
            score += 1.0  # 温和整理

    # 均线确认
    if ma_bullish:
        score += 1.5
    elif above_ma20:
        score += 0.5

    return max(0.0, min(10.0, score))


def _sentiment_score(text: str) -> float:
    """对投资逻辑文本做情感分析评分（0-10）。

    基于关键词判断文本的看多/看空程度。
    """
    if not text:
        return 5.0

    text_lower = text.lower()

    # 看多关键词
    bullish_kw = [
        "看好", "看涨", "推荐", "买入", "增持", "强推", "强烈推荐",
        "突破", "反弹", "上行", "上涨", "空间", "潜力", "景气",
        "龙头", "白马", "成长", "加速", "超预期", "量价齐升",
        "供不应求", "产能紧缺", "订单饱满", "景气上行", "业绩增长",
        "边际改善", "拐点", "戴维斯双击", "黄金赛道", "高景气",
    ]
    # 看空关键词
    bearish_kw = [
        "看空", "看跌", "回避", "卖出", "减持", "警惕", "风险",
        "下行", "下跌", "回调", "高估", "泡沫", "透支", "过剩",
        "暴雷", "爆雷", "亏损", "减持", "减持新规", "政策收紧",
        "产能过剩", "价格战", "不及预期", "戴维斯双杀",
    ]

    bull_count = sum(1 for kw in bullish_kw if kw in text_lower)
    bear_count = sum(1 for kw in bearish_kw if kw in text_lower)

    # 净情感得分
    net_sentiment = bull_count - bear_count

    # 映射到 0-10
    if net_sentiment >= 4:
        return 9.0
    elif net_sentiment >= 2:
        return 7.5
    elif net_sentiment >= 1:
        return 6.5
    elif net_sentiment == 0:
        return 5.0
    elif net_sentiment >= -1:
        return 4.0
    elif net_sentiment >= -3:
        return 3.0
    else:
        return 2.0


    return max(0.0, min(10.0, score))


def _long_term_trend_score(logic_text: str, target_str: str = "", risk_str: str = "") -> float:
    """评估长期趋势和涨价预期（0-10）。

    关键词命中越多、越核心（涨价/供不应求/景气上行），分数越高。
    有明确量化目标价的加分；有降价/过剩风险词扣分。
    """
    text = f"{logic_text} {target_str}"
    text_lower = text.lower()

    # 基础分
    score = 3.0

    # 涨价/提价类关键词（高权重）
    price_up_kw = ["涨价", "提价", "价格上行", "价格上涨", "价格回升", "价格修复",
                    "涨价预期", "提价预期", "价格弹性", "量价齐升"]
    price_hits = sum(1 for kw in price_up_kw if kw in text_lower)
    if price_hits >= 2:
        score += 3.0
    elif price_hits == 1:
        score += 2.0

    # 供需紧张类关键词
    supply_kw = ["供不应求", "供需紧张", "供需紧平衡", "产能紧缺", "产能紧张",
                  "库存低位", "低库存", "去库存"]
    supply_hits = sum(1 for kw in supply_kw if kw in text_lower)
    if supply_hits >= 2:
        score += 2.5
    elif supply_hits == 1:
        score += 1.5

    # 景气向上类关键词
    boom_kw = ["景气上行", "景气回升", "景气度提升", "高景气", "景气周期",
               "需求回暖", "需求复苏", "需求爆发", "需求旺盛"]
    boom_hits = sum(1 for kw in boom_kw if kw in text_lower)
    if boom_hits >= 2:
        score += 2.0
    elif boom_hits == 1:
        score += 1.0

    # 产能扩张 / 订单增长
    expand_kw = ["扩产", "产能扩张", "新增产能", "产能释放", "产能爬坡",
                 "订单饱满", "订单增长", "订单加速"]
    expand_hits = sum(1 for kw in expand_kw if kw in text_lower)
    if expand_hits >= 2:
        score += 1.5
    elif expand_hits == 1:
        score += 0.8

    # 国产替代 / 自主可控
    alt_kw = ["国产替代", "自主可控", "进口替代", "国产化"]
    alt_hits = sum(1 for kw in alt_kw if kw in text_lower)
    if alt_hits >= 1:
        score += 1.0

    # 有明确目标价/目标市值加分
    if target_str:
        if re.search(r"\d+[\.\d]*\s*(?:元|块)", target_str):
            score += 1.0
        if re.search(r"\d+[\.\d]*\s*(?:亿|[eE]\b)", target_str):
            score += 0.8

    # 负面风险词扣分
    risk_lower = risk_str.lower()
    risk_penalty_kw = ["降价", "价格战", "产能过剩", "库存高企", "需求疲软", "供过于求"]
    risk_hits = sum(1 for kw in risk_penalty_kw if kw in risk_lower)
    score -= risk_hits * 1.0

    return round(max(0.0, min(10.0, score)), 1)
    """基于关键词分析板块逻辑文本的情感倾向。

    起始 5 分（中性），每个正面关键词 +0.8，负面 -0.8，结果截断至 [0, 10]。
    """
    if not logic_text:
        return 5.0
    score = 5.0
    text_lower = logic_text.lower()
    for kw in _POSITIVE_LOGIC_KW:
        if kw in text_lower:
            score += 0.8
    for kw in _NEGATIVE_LOGIC_KW:
        if kw in text_lower:
            score -= 0.8
    return max(0.0, min(10.0, score))


def _detect_sector_trends(
    all_stocks: dict,
    sectors_list: list[dict],
    sector_heat_raw: dict,
    sector_aliases: dict,
    trend_config: dict,
) -> dict:
    """检测行业趋势：按标准化板块分组，计算 0-10 趋势分数。

    四个信号加权：
      1. 价格动量 — 板块内股票平均 5 日涨跌幅
      2. 板块规模 — 板块内标的数量
      3. 讨论强度 — 板块在"细分板块机会"中被讨论的热度
      4. 逻辑情感 — AI 对板块逻辑的正负面描述

    板块内股票数 < min_stocks_for_trend 时返回 0（不构成趋势）。
    返回 (trend_scores, sector_groups, sector_logic_map) 三元组。
    """
    min_stocks = trend_config.get("min_stocks_for_trend", 2)
    w_momentum = trend_config.get("momentum_weight", 0.35)
    w_size = trend_config.get("size_weight", 0.25)
    w_discussion = trend_config.get("discussion_weight", 0.25)
    w_logic = trend_config.get("logic_weight", 0.15)
    max_score = trend_config.get("max_trend_score", 10)

    # 1. 按标准化板块名分组股票
    sector_groups: dict[str, list] = {}
    for key, stock in all_stocks.items():
        raw_sector = stock.get("sector", "")
        norm = _normalize_sector_name(raw_sector, sector_aliases)
        if not norm:
            continue
        if norm not in sector_groups:
            sector_groups[norm] = []
        sector_groups[norm].append(stock)

    # 2. 标准化板块热度键名
    norm_heat: dict[str, int] = {}
    for raw_name, heat_val in sector_heat_raw.items():
        norm = _normalize_sector_name(raw_name, sector_aliases)
        if norm:
            norm_heat[norm] = norm_heat.get(norm, 0) + heat_val

    # 3. 构建板块逻辑映射（标准化 + 合并同板块逻辑文本）
    sector_logic_map: dict[str, str] = {}
    for entry in sectors_list:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("sector", "")
        norm = _normalize_sector_name(raw, sector_aliases)
        if norm:
            new_logic = entry.get("logic", "")
            if new_logic:
                existing = sector_logic_map.get(norm, "")
                sector_logic_map[norm] = (
                    existing + "; " + new_logic if existing else new_logic
                )

    # 4. 计算每个板块的趋势分数
    trend_scores: dict[str, float] = {}
    for sector_name, stocks in sector_groups.items():
        if len(stocks) < min_stocks:
            trend_scores[sector_name] = 0.0
            continue

        # 4a. 价格动量：平均 5 日涨跌幅缩放至 0-10
        changes = [
            s.get("change_5d") for s in stocks
            if s.get("change_5d") is not None
        ]
        if changes:
            avg_change = sum(changes) / len(changes)
            # 5% 平均涨幅 → 10 分, 0% → 5 分, -2.5% → 0 分
            momentum_score = min(max_score, max(0, (avg_change + 2.5) * 1.33))
        else:
            momentum_score = 0

        # 4b. 板块规模：3 只 → 10 分
        size_score = min(max_score, len(stocks) * 3.33)

        # 4c. 讨论强度：200 字符 → 10 分
        heat_val = norm_heat.get(sector_name, 0)
        discussion_score = min(max_score, heat_val / 20)

        # 4d. 逻辑情感
        logic_text = sector_logic_map.get(sector_name, "")
        logic_score = _sentiment_score(logic_text)

        trend_score = (
            w_momentum * momentum_score
            + w_size * size_score
            + w_discussion * discussion_score
            + w_logic * logic_score
        )
        trend_scores[sector_name] = round(min(max_score, trend_score), 1)

    return trend_scores, sector_groups, sector_logic_map


def _trend_signal_desc(trend_score: float, changes, stock_count: int) -> str:
    """生成趋势信号的人类可读解读。"""
    if isinstance(changes, list) and changes:
        avg = sum(changes) / len(changes)
    else:
        avg = 0
    parts = []
    if avg > 2:
        parts.append("集体上涨")
    elif avg > 0:
        parts.append("温和上行")
    elif avg < -2:
        parts.append("短期回调")
    else:
        parts.append("横盘整理")
    if stock_count >= 3:
        parts.append(f"{stock_count}只标的受关注")
    if trend_score >= 8:
        parts.append("多重信号共振")
    return "，".join(parts) if parts else "关注中"


def _fmt_change(change_pct) -> str:
    """格式化涨跌幅，正数带 + 号，无数据显示 -。"""
    if change_pct is None:
        return "-"
    return f"{change_pct:+.2f}%"


def _fmt_market_cap(market_cap_yi) -> str:
    """格式化当前市值（单位：亿元）。"""
    if market_cap_yi is None:
        return "-"
    if market_cap_yi >= 10000:
        return f"{market_cap_yi / 10000:.2f}万亿"
    if market_cap_yi >= 1000:
        return f"{market_cap_yi:.0f}亿"
    if market_cap_yi >= 100:
        return f"{market_cap_yi:.1f}亿"
    return f"{market_cap_yi:.2f}亿"


def _target_precision_score(target_str: str) -> float:
    """评估目标参考的可执行程度（0-10）。"""
    if not target_str:
        return 2.0
    score = 2.0
    if re.search(r"\d+[\.\d]*\s*(?:元|块)", target_str):
        score += 3.5
    if re.search(r"\d+[\.\d]*\s*(?:亿|[eE]\b)", target_str):
        score += 3.0
    if re.search(r"(?:PE|PB|PS|估值|倍)", target_str, re.IGNORECASE):
        score += 1.5
    if re.search(r"(?:增速|增长|利润|营收|收入|EPS|业绩)", target_str, re.IGNORECASE):
        score += 1.0
    return min(10.0, score)


def _calibrate_recommendation_score(
    base_score: float,
    logic_score: float,
    target_precision: float,
    post_count: int,
    category: str,
    unique_authors: int = 0,
) -> float:
    """把原始加权分映射为更有区分度的推荐指数。"""
    category_bonus = 0.25 if category == "quantitative" else 0.0
    # 共识加成：独立作者数权重大于帖子数
    author_nudge = min(1.0, max(0, unique_authors - 1) * 0.35)
    post_nudge = min(0.3, max(0, post_count - unique_authors) * 0.1)
    consensus_nudge = author_nudge + post_nudge
    logic_delta = (logic_score - 5.0) * 0.10
    target_delta = (target_precision - 5.0) * 0.08
    calibrated = base_score + category_bonus + consensus_nudge + logic_delta + target_delta
    return round(max(1.0, min(10.0, calibrated)), 1)


def _score_label(score: float) -> str:
    """推荐指数分层标签，比星级更细。"""
    if score >= 9.2:
        return "S级/重点跟踪"
    if score >= 8.5:
        return "A+/强优先"
    if score >= 7.8:
        return "A/优先"
    if score >= 7.0:
        return "B+/积极观察"
    if score >= 6.2:
        return "B/观察"
    if score >= 5.4:
        return "C+/等待催化"
    if score >= 4.6:
        return "C/低优先"
    return "D/谨慎"


def _format_score_display(stock: dict) -> str:
    """展示推荐指数和关键驱动项。"""
    detail = stock.get("score_detail", {})
    drivers = [
        ("目标", detail.get("target", 0)),
        ("逻辑", detail.get("logic", 0)),
        ("趋势", detail.get("trend", 0)),
        ("共识", detail.get("consensus", 0)),
    ]
    top_drivers = sorted(drivers, key=lambda x: x[1], reverse=True)[:2]
    driver_str = " / ".join(f"{name}{value:.1f}" for name, value in top_drivers if value)
    suffix = f"<br><small>{driver_str}</small>" if driver_str else ""
    return f"**{stock['score']:.1f}** · {_score_label(stock['score'])}{suffix}"


def _fmt_price(value) -> str:
    """格式化价格区间中的单价。"""
    if value is None:
        return "-"
    return f"{value:.1f}元" if value >= 100 else f"{value:.2f}元"


def _technical_buy_score(stock: dict) -> tuple[float, str]:
    """根据技术指标给出当前买点分和简短信号。"""
    tech = stock.get("technical") or {}
    if not tech:
        return 5.0, "技术数据不足，按逻辑观察"

    score = 5.0
    notes = []
    distance = tech.get("distance_ma20_pct")
    position = tech.get("position_20d")
    change_5d = tech.get("change_5d")
    change_20d = tech.get("change_20d")
    volume_ratio = tech.get("volume_ratio")

    if tech.get("ma_bullish"):
        score += 1.4
        notes.append("均线多头")
    elif tech.get("above_ma20"):
        score += 0.7
        notes.append("站上20日线")
    elif tech.get("above_ma10"):
        score += 0.3
        notes.append("站上10日线")
    else:
        score -= 1.0
        notes.append("弱于20日线")

    if distance is not None:
        if -2 <= distance <= 5:
            score += 1.2
            notes.append("贴近20日线")
        elif 5 < distance <= 10:
            score += 0.2
            notes.append("略偏离均线")
        elif distance > 10:
            score -= 1.4
            notes.append("短线偏离大")
        elif distance < -5:
            score -= 0.8
            notes.append("趋势待修复")

    if change_5d is not None:
        if -3 <= change_5d <= 4:
            score += 0.8
            notes.append("短线未过热")
        elif change_5d > 8:
            score -= 1.6
            notes.append("5日涨幅过热")
        elif change_5d < -6:
            score -= 0.8
            notes.append("短线走弱")

    if position is not None:
        if 25 <= position <= 70:
            score += 0.7
            notes.append("区间位置适中")
        elif position > 85:
            score -= 1.0
            notes.append("接近20日高位")
        elif position < 15:
            score -= 0.2
            notes.append("低位待确认")

    if volume_ratio is not None:
        if 1.05 <= volume_ratio <= 1.8 and (change_5d is None or change_5d >= -2):
            score += 0.5
            notes.append("温和放量")
        elif volume_ratio > 2.5:
            score -= 0.5
            notes.append("量能异常放大")

    # 量价背离检测：价格突破但量能不足 → 假突破风险
    if change_5d is not None and volume_ratio is not None:
        if change_5d > 5 and volume_ratio < 0.8:
            score -= 1.0
            notes.append("量价背离：缩量上涨")
        elif change_5d < -5 and volume_ratio > 1.5:
            score -= 0.5
            notes.append("放量下跌")
        elif change_5d > 3 and volume_ratio > 1.8:
            score += 0.4
            notes.append("放量上涨确认")

    if change_20d is not None and change_20d < -12:
        score -= 0.5
        notes.append("20日趋势偏弱")

    # RSI 信号
    rsi = tech.get("rsi_14")
    if rsi is not None:
        if rsi <= 30:
            score += 1.2
            notes.append(f"RSI超卖({rsi:.0f})")
        elif rsi <= 40:
            score += 0.5
            notes.append(f"RSI偏低({rsi:.0f})")
        elif rsi >= 80:
            score -= 1.4
            notes.append(f"RSI超买({rsi:.0f})")
        elif rsi >= 70:
            score -= 0.5
            notes.append(f"RSI偏高({rsi:.0f})")

    # MACD 信号
    macd_hist = tech.get("macd_hist")
    macd_line = tech.get("macd_line")
    macd_signal = tech.get("macd_signal")
    if macd_hist is not None and macd_line is not None:
        if macd_hist > 0 and macd_line > 0:
            score += 0.8
            notes.append("MACD多头")
        elif macd_hist > 0 and macd_line <= 0:
            score += 0.4
            notes.append("MACD金叉")
        elif macd_hist < 0 and macd_line < 0:
            score -= 0.8
            notes.append("MACD空头")
        elif macd_hist < 0 and macd_line >= 0:
            score -= 0.4
            notes.append("MACD死叉")

    if not notes:
        notes.append("技术面中性")
    unique_notes = []
    for note in notes:
        if note not in unique_notes:
            unique_notes.append(note)
    return round(max(1.0, min(10.0, score)), 1), "；".join(unique_notes[:4])


def _buy_score(stock: dict) -> float:
    """综合逻辑推荐和技术买点，衡量当前是否适合出手。"""
    score = stock.get("score", 0)
    technical_score = stock.get("technical_score", 5.0)
    risk_text = stock.get("risk_display", "")
    tech = stock.get("technical") or {}
    market_filter = stock.get("market_filter") or {}
    penalty = 0.0
    if any(kw in risk_text for kw in ("回避", "看空", "退市", "重大利空")):
        penalty += 2.0
    if stock.get("change_5d") is not None and stock["change_5d"] > 10:
        penalty += 0.8
    if _is_overheated(stock):
        penalty += 1.2
    if tech.get("distance_ma20_pct") is not None and tech["distance_ma20_pct"] < -6:
        penalty += 0.5
    market_penalty = market_filter.get("buy_penalty", 0.0)
    market_bonus = market_filter.get("buy_bonus", 0.0)
    credibility = _source_credibility_score(stock)
    raw = score * 0.52 + technical_score * 0.36 + credibility * 0.12
    return round(max(1.0, min(10.0, raw + market_bonus - market_penalty - penalty)), 1)


def _is_overheated(stock: dict) -> bool:
    """判断是否短线过热，过热票不能进入立即买入。"""
    tech = stock.get("technical") or {}
    change_5d = tech.get("change_5d", stock.get("change_5d"))
    position = tech.get("position_20d")
    distance = tech.get("distance_ma20_pct")
    return (
        (change_5d is not None and change_5d > 10)
        or (position is not None and position > 85)
        or (distance is not None and distance > 10)
    )


def _is_in_uptrend(stock: dict) -> bool:
    """判断股票是否处于上升趋势。

    条件（满足任一即可）：
    - 均线多头排列（ma5 >= ma10 >= ma20）
    - 站上 5 日线且 5 日涨跌幅为正
    """
    tech = stock.get("technical") or {}
    if not tech:
        return False
    if tech.get("ma_bullish"):
        return True
    if tech.get("above_ma5") and tech.get("change_5d") is not None and tech["change_5d"] > 0:
        return True
    return False


def _is_sector_rising(stock: dict) -> bool:
    """判断所属板块整体是否上涨（趋势分数 >= 5.0）。"""
    trend_score = stock.get("trend_score", 0)
    return trend_score >= 5.0


def _is_near_ma5(stock: dict, tolerance_pct: float = 3.0, atr_tolerance: float = 1.5) -> bool:
    """判断当前股价是否在 5 日均线附近。

    优先使用 ATR 归一化距离（距5日线不超过 1.5 倍 ATR），
    无 ATR 数据时回退到固定百分比（默认 ±3%）。

    Args:
        stock: 增强后的股票数据。
        tolerance_pct: 回退方案的容忍偏离百分比。
        atr_tolerance: ATR 归一化容忍倍数。
    """
    tech = stock.get("technical") or {}
    # 优先使用 ATR 归一化
    distance_atr = tech.get("distance_ma5_atr")
    if distance_atr is not None:
        return abs(distance_atr) <= atr_tolerance
    # 回退到固定百分比
    distance_pct = tech.get("distance_ma5_pct")
    if distance_pct is None:
        return False
    return abs(distance_pct) <= tolerance_pct


def _apply_portfolio_constraints(passed: list[dict], max_per_sector: int = 3) -> list[dict]:
    """行业集中度控制：同一板块最多保留 max_per_sector 只，按得分降序保留。

    同时使用 portfolio_builder.apply_sector_cap 做仓位占比限制，
    确保单一行业仓位不超过 25%。

    Args:
        passed: 通过趋势精选的股票列表（已按得分降序）。
        max_per_sector: 每个板块最多保留的股票数。

    Returns:
        通过行业集中度检查的股票列表。
    """
    # 第一步：数量限制
    sector_count = {}
    result = []
    for stock in passed:
        sector = stock.get("sector") or "未分类"
        sector_count[sector] = sector_count.get(sector, 0) + 1
        if sector_count[sector] <= max_per_sector:
            stock.pop("_sector_limited", None)
            result.append(stock)
        else:
            stock["_filter_reason"] = f"板块'{sector}'已选{max_per_sector}只，集中度限制"
            stock["_sector_limited"] = True

    # 第二步：仓位占比限制（通过 portfolio_builder）
    try:
        from portfolio_builder import apply_sector_cap
        result = apply_sector_cap(
            result,
            max_per_sector=max_per_sector,
            max_sector_pct=0.25,
            total_slots=8,
            verbose=False,
        )
    except ImportError:
        pass

    return result


def _apply_liquidity_filter(
    stocks: list[dict],
    min_amount_yi: float = 0.5,
    min_market_cap_yi: float = 20.0,
) -> list[dict]:
    """流动性过滤：剔除日均成交额过低或流通市值过小的标的。

    Args:
        stocks: 股票列表。
        min_amount_yi: 最低日均成交额（亿元），默认 0.5 亿。
        min_market_cap_yi: 最低流通市值（亿元），默认 20 亿。

    Returns:
        通过流动性检查的股票列表。
    """
    result = []
    for stock in stocks:
        reasons = []
        market_cap = stock.get("market_cap_yi")
        # 成交额：用 technical.volume_ratio * 近5日均成交额近似
        tech = stock.get("technical") or {}
        turnover = stock.get("turnover_rate")
        price = stock.get("current_price")

        # 市值检查
        if market_cap is not None and market_cap < min_market_cap_yi:
            reasons.append(f"市值{market_cap:.1f}亿<{min_market_cap_yi}亿")

        # 换手率极低 + 小市值 = 流动性差
        if turnover is not None and turnover < 0.5 and (market_cap or 0) < 50:
            reasons.append(f"换手率{turnover:.2f}%过低，流动性不足")

        if reasons:
            stock["_filter_reason"] = "；".join(reasons)
        else:
            stock.pop("_filter_reason", None)
            result.append(stock)
    return result


def _estimate_slippage(stock: dict) -> dict:
    """估算滑点和冲击成本。

    基于个股流动性（换手率、市值）估算：
    - 小盘低换手：滑点大（0.3-0.5%）
    - 大盘高换手：滑点小（0.05-0.1%）

    Returns:
        增加 slippage_pct 和 impact_cost 字段的 stock dict。
    """
    tech = stock.get("technical") or {}
    turnover = stock.get("turnover_rate") or 1.0
    market_cap = stock.get("market_cap_yi") or 100
    vol_ratio = tech.get("volume_ratio") or 1.0

    # 基础滑点：与换手率成反比
    if turnover >= 3:
        base_slip = 0.05
    elif turnover >= 1.5:
        base_slip = 0.10
    elif turnover >= 0.8:
        base_slip = 0.15
    elif turnover >= 0.3:
        base_slip = 0.25
    else:
        base_slip = 0.40

    # 市值调整：小盘加滑点
    if market_cap < 30:
        base_slip += 0.15
    elif market_cap < 80:
        base_slip += 0.08
    elif market_cap < 200:
        base_slip += 0.03

    # 放量时滑点减小
    if vol_ratio > 1.5:
        base_slip *= 0.8

    slippage_pct = round(base_slip, 2)

    # 冲击成本：假设买入 50 万，占日成交额的比例
    # 日成交额 = 市值 * 换手率 / 100
    daily_amount = market_cap * turnover / 100 if market_cap and turnover else 1
    impact_ratio = 0.5 / max(daily_amount, 0.01)  # 50万 / 日成交额（亿）
    impact_cost = round(min(1.0, impact_ratio * 0.1), 3)  # 简化模型

    stock["slippage_pct"] = slippage_pct
    stock["impact_cost_pct"] = impact_cost
    stock["total_cost_pct"] = round(slippage_pct + impact_cost, 2)
    return stock


def _filter_trending_near_ma5(
    enriched: list[dict],
    score_threshold: float = 5.0,
    ma5_tolerance: float = 3.0,
    atr_tolerance: float = 1.5,
) -> tuple[list[dict], list[dict]]:
    """筛选处于上升趋势、板块上涨、得分达标且价格在5日均线附近的股票。

    使用打分制替代刚性门槛：每个条件贡献分数，总分达标即可通过。
    打分制比硬性门槛更灵活，避免"四个条件同时满足"导致的无票问题。

    各条件权重：
    - 得分达标（40分）：得分越高分越高
    - 上升趋势（25分）：均线多头/站上均线
    - 板块上涨（20分）：板块趋势分
    - 5日均线附近（15分）：距5日线距离

    总分 >= 55 分即可通过（满分 100）。

    Args:
        enriched: 增强后的股票列表。
        score_threshold: 推荐指数最低分。
        ma5_tolerance: 无 ATR 时回退的固定百分比容忍度。
        atr_tolerance: ATR 归一化容忍倍数（距5日线不超过 N 倍 ATR）。

    Returns:
        (passed, filtered): 通过筛选的股票列表和被过滤的股票列表。
    """
    passed = []
    filtered = []
    for stock in enriched:
        reasons = []
        filter_score = 0

        # 条件1：得分达标（40分）
        score = stock.get("score", 0)
        if score >= score_threshold:
            # 得分越高分越高，超过阈值后每分加 4 分
            filter_score += min(40, 20 + (score - score_threshold) * 4)
        elif score >= score_threshold * 0.7:
            # 接近阈值也给部分分
            filter_score += max(0, (score / score_threshold) * 20)
            reasons.append(f"得分{score:.1f}略低于{score_threshold}")
        else:
            reasons.append(f"得分{score:.1f}<{score_threshold}")

        # 条件2：上升趋势（25分）
        tech = stock.get("technical") or {}
        if tech.get("ma_bullish"):
            filter_score += 25
        elif tech.get("above_ma20"):
            filter_score += 18
        elif tech.get("above_ma10"):
            filter_score += 10
        elif _is_in_uptrend(stock):
            filter_score += 8
        else:
            reasons.append("未处于上升趋势")

        # 条件3：板块上涨（20分）
        trend_score = stock.get("trend_score", 0)
        if trend_score >= 7.0:
            filter_score += 20
        elif trend_score >= 5.0:
            filter_score += 15
        elif trend_score >= 3.0:
            filter_score += 8
            reasons.append(f"板块趋势偏弱({trend_score:.1f})")
        else:
            reasons.append(f"板块趋势不足({trend_score:.1f})")

        # 条件4：5日均线附近（15分）
        if _is_near_ma5(stock, ma5_tolerance, atr_tolerance):
            filter_score += 15
        else:
            d_atr = tech.get("distance_ma5_atr")
            d_pct = tech.get("distance_ma5_pct")
            if d_atr is not None:
                if abs(d_atr) <= atr_tolerance * 1.5:
                    filter_score += 8  # 接近但未完全达标
                    reasons.append(f"距5日线{d_atr:+.1f}倍ATR（略偏）")
                else:
                    reasons.append(f"距5日线{d_atr:+.1f}倍ATR(超{atr_tolerance})")
            elif d_pct is not None:
                if abs(d_pct) <= ma5_tolerance * 1.5:
                    filter_score += 8
                    reasons.append(f"偏离5日线{d_pct:+.1f}%（略偏）")
                else:
                    reasons.append(f"偏离5日线{d_pct:+.1f}%")
            else:
                reasons.append("无5日线数据")

        # 附加分：高分股（>7.5）额外加分
        if score >= 7.5:
            filter_score += 5
        elif score >= 6.5:
            filter_score += 2

        # 附加分：技术买点好
        technical_score = stock.get("technical_score", 5.0)
        if technical_score >= 7.0:
            filter_score += 5

        stock["_filter_score"] = filter_score

        # 通过阈值：55 分（满分 100）
        if filter_score >= 55:
            stock.pop("_filter_reason", None)
            passed.append(stock)
        else:
            stock["_filter_reason"] = "；".join(reasons[:3]) if reasons else f"综合得分{filter_score:.0f}/100"
            filtered.append(stock)

    return passed, filtered


def _filter_reason_summary(filtered: list[dict]) -> str:
    """汇总被过滤股票的主要原因分布。"""
    if not filtered:
        return "无"
    from collections import Counter
    counter = Counter()
    for stock in filtered:
        reason = stock.get("_filter_reason", "")
        for part in reason.split("；"):
            part = part.strip()
            if part:
                # 取原因类别（去掉数值细节）
                key = part.split("(")[0].split(":")[0].strip()
                counter[key] += 1
    return "、".join(f"{reason}({count}只)" for reason, count in counter.most_common(4))


def _market_filter(enriched: list[dict], external_market: dict = None) -> dict:
    """综合主要指数与候选池技术面生成市场环境过滤。"""
    external_market = external_market or {}
    techs = [s.get("technical") or {} for s in enriched if s.get("technical")]
    if not techs:
        if external_market:
            return external_market
        return {
            "level": "未知",
            "desc": "技术样本不足，买入档位不做市场过滤",
            "buy_penalty": 0.0,
            "buy_bonus": 0.0,
        }
    total = len(techs)
    above20 = sum(1 for t in techs if t.get("above_ma20")) / total
    bullish = sum(1 for t in techs if t.get("ma_bullish")) / total
    overheated = sum(
        1 for t in techs
        if (t.get("change_5d") is not None and t["change_5d"] > 10)
        or (t.get("position_20d") is not None and t["position_20d"] > 85)
        or (t.get("distance_ma20_pct") is not None and t["distance_ma20_pct"] > 10)
    ) / total

    if above20 >= 0.62 and bullish >= 0.35 and overheated <= 0.35:
        pool_level = "偏强"
        pool_penalty = 0.0
        pool_bonus = 0.2
    elif above20 < 0.42 or bullish < 0.18:
        pool_level = "偏弱"
        pool_penalty = 0.7
        pool_bonus = 0.0
    elif overheated > 0.45:
        pool_level = "过热"
        pool_penalty = 0.5
        pool_bonus = 0.0
    else:
        pool_level = "中性"
        pool_penalty = 0.2
        pool_bonus = 0.0

    ext_level = external_market.get("level", "")
    if ext_level:
        priority = {"偏弱": 4, "过热": 3, "中性": 2, "未知": 1, "偏强": 0}
        level = ext_level if priority.get(ext_level, 1) >= priority.get(pool_level, 1) else pool_level
        penalty = max(pool_penalty, external_market.get("buy_penalty", 0.0))
        bonus = min(pool_bonus, external_market.get("buy_bonus", 0.0))
        desc = (
            f"大盘：{external_market.get('desc', '无指数数据')}；"
            f"候选池：站上20日线占比{above20:.0%}，均线多头占比{bullish:.0%}，"
            f"短线过热占比{overheated:.0%}"
        )
    else:
        level = pool_level
        penalty = pool_penalty
        bonus = pool_bonus
        desc = (
            f"候选池站上20日线占比{above20:.0%}，均线多头占比{bullish:.0%}，"
            f"短线过热占比{overheated:.0%}"
        )

    return {
        "level": level,
        "desc": desc,
        "buy_penalty": penalty,
        "buy_bonus": bonus,
        "index_level": ext_level or "",
        "candidate_level": pool_level,
    }


def _source_credibility_score(stock: dict) -> float:
    """来源可信度评分，补强多独立作者提及和研报型机会。"""
    score = 5.0
    unique_authors = len(stock.get("authors", set()))
    post_count = stock.get("post_count", 1) or 1
    # 独立作者数权重大于帖子数
    if unique_authors >= 4:
        score += 3.0
    elif unique_authors >= 3:
        score += 2.2
    elif unique_authors >= 2:
        score += 1.5
    elif post_count >= 3:
        score += 0.8
    elif post_count >= 2:
        score += 0.5
    else:
        score += 0.3
    if stock.get("category") == "quantitative" or stock.get("target_str"):
        score += 1.0
    if stock.get("foreign_research") or "研报" in (stock.get("logic") or ""):
        score += 0.8
    if stock.get("source"):
        score += 0.3
    if stock.get("category") == "elastic" and not stock.get("target_str"):
        score -= 0.4
    return round(max(1.0, min(10.0, score)), 1)


def _buy_bucket(stock: dict) -> str:
    """三档买入状态。"""
    risk_text = stock.get("risk_display", "")
    if any(kw in risk_text for kw in ("回避", "看空", "退市", "重大利空")):
        return "只观察"
    if stock.get("market_filter", {}).get("level") in {"偏弱", "过热"}:
        return "只观察"
    if _is_overheated(stock):
        return "等回踩买"
    if stock.get("buy_score", 0) >= 7.4 and stock.get("score", 0) >= 7.0:
        return "立即可买"
    if stock.get("buy_score", 0) >= 6.2 and stock.get("score", 0) >= 6.2:
        return "等回踩买"
    return "只观察"


def _technical_buy_reference(stock: dict) -> str:
    """结合技术指标生成买点参考（含成本估算）。"""
    current = stock.get("current_price")
    tech = stock.get("technical") or {}
    buy_score = stock.get("buy_score", 0)
    distance = tech.get("distance_ma20_pct")
    ma20 = tech.get("ma20")
    total_cost = stock.get("total_cost_pct", 0)

    if not current or current <= 0:
        return stock.get("entry_ref", "缺少行情，先观察")

    bucket = _buy_bucket(stock)
    if bucket == "只观察":
        return "先观察，不急买；等趋势、量能或风险改善"

    cost_note = f"（成本约{total_cost:.1f}%）" if total_cost > 0.2 else ""

    if buy_score >= 7.5:
        if distance is not None and distance > 7:
            return f"不追涨；回踩 {_fmt_price(current * 0.96)} 附近再分批{cost_note}"
        if ma20:
            return f"分批低吸；优先看 {_fmt_price(max(current * 0.97, ma20))} 附近承接{cost_note}"
        return stock.get("entry_ref", "-")
    if buy_score >= 6.5:
        if ma20:
            return f"等回踩20日线附近（约 {_fmt_price(ma20)}）或放量站稳再买{cost_note}"
        return f"等回踩或放量确认{cost_note}"
    if buy_score >= 5.5:
        return "先观察，不急买；等技术面修复"
    return "暂不买入，等待趋势和风险改善"


def _trade_advice(stock: dict) -> str:
    """给出更明确的买卖/持有建议。"""
    risk_text = stock.get("risk_display", "")
    if any(kw in risk_text for kw in ("回避", "看空", "退市", "重大利空")):
        return "卖出/回避"
    score = stock.get("score", 0)
    tech_score = stock.get("technical_score", 0)
    bucket = _buy_bucket(stock)
    if bucket == "立即可买":
        return "立即可买"
    if bucket == "等回踩买":
        return "等回踩买"
    if score >= 7.0 and tech_score < 5.5:
        return "持有等买点"
    if score < 5.4:
        return "暂不买入"
    return "只观察"


def _trade_period(stock: dict) -> str:
    """区分短线/波段/中线机会。"""
    tech = stock.get("technical") or {}
    logic = stock.get("logic", "")
    target = stock.get("target_str", "")
    if any(kw in logic for kw in ("涨停", "事件", "催化", "反弹", "突破")):
        return "短线"
    if target or stock.get("category") == "quantitative" or stock.get("trend_score", 0) >= 6.5:
        return "中线"
    if tech.get("ma_bullish") or stock.get("technical_score", 0) >= 6.5:
        return "波段"
    return "观察"


def _exit_trigger(stock: dict) -> str:
    """生成卖出/减仓触发条件（含止盈策略）。

    策略层级：
    1. 风险触发 → 直接回避
    2. 移动止盈 → 从最高点回撤触发
    3. 目标价止盈 → 到达目标价附近触发
    4. RSI + MACD 联动止盈 → 技术面超买触发
    5. 均线止损 → 跌破均线触发
    """
    current = stock.get("current_price")
    tech = stock.get("technical") or {}
    ma20 = tech.get("ma20")
    ma10 = tech.get("ma10")
    ma5 = tech.get("ma5")
    period = stock.get("trade_period", "")
    target_str = stock.get("target_str", "")
    target_value = stock.get("target_value")
    change_5d = stock.get("change_5d") or tech.get("change_5d")
    rsi = tech.get("rsi_14")
    macd_hist = tech.get("macd_hist")
    macd_line = tech.get("macd_line")
    position_20d = tech.get("position_20d")

    if not current:
        return "无行情数据；按原帖逻辑和风险事件人工复核"

    # 1. 风险触发
    if any(kw in stock.get("risk_display", "") for kw in ("回避", "看空", "退市", "重大利空")):
        return "风险触发，回避或清仓"

    # 2. RSI + MACD 联动止盈（超买信号）
    if rsi is not None and rsi >= 80:
        if macd_hist is not None and macd_hist < 0:
            return f"RSI超买({rsi:.0f})+MACD转弱，建议分批止盈；跌破{_fmt_price(current * 0.96)}减仓"
        return f"RSI超买({rsi:.0f})，冲高分批止盈；跌破{_fmt_price(current * 0.95)}减仓"

    # 3. 目标价止盈
    if target_value and target_str:
        if "元" in target_str or "块" in target_str:
            upside = (target_value / current - 1) * 100 if current > 0 else 0
            if upside <= 5:
                return f"已接近目标价{_fmt_price(target_value)}，分批止盈；跌破{_fmt_price(current * 0.95)}清仓"
            elif upside <= 15:
                return f"距目标价{_fmt_price(target_value)}仅{upside:.0f}%，逐步止盈；跌破{_fmt_price(current * 0.94)}减仓"
        elif "亿" in target_str or "e" in target_str.lower():
            market_cap = stock.get("market_cap_yi")
            if market_cap and market_cap > 0:
                cap_upside = (target_value / market_cap - 1) * 100
                if cap_upside <= 10:
                    return f"目标市值已接近，分批止盈；跌破{_fmt_price(current * 0.95)}减仓"

    # 4. 短线过热止盈
    if _is_overheated(stock):
        return "短线涨幅过热，冲高分批止盈；跌破5日线减仓"

    # 5. 均线止损
    if period == "短线":
        return f"跌破 {_fmt_price(current * 0.94)} 或放量转弱减仓"
    if period == "波段":
        ref = ma10 or current * 0.95
        return f"跌破10日线附近（约 {_fmt_price(ref)}）且放量，先减仓"
    if ma20:
        return f"跌破20日线附近（约 {_fmt_price(ma20)}）且两日未收回，降为观察"
    return f"跌破 {_fmt_price(current * 0.92)} 且逻辑未兑现，降为观察"


def _opportunity_type(stock: dict) -> str:
    """识别机会类型，便于匹配不同交易规则。"""
    text = " ".join([
        stock.get("logic", ""),
        stock.get("target_str", ""),
        stock.get("sector", ""),
        stock.get("source_note", ""),
    ])
    if any(kw in text for kw in ("研报", "目标价", "上调", "覆盖", "买入评级", "增持评级")):
        return "研报驱动"
    if any(kw in text for kw in ("事件", "催化", "政策", "订单", "涨价", "发布", "招标", "中标")):
        return "事件驱动"
    if any(kw in text for kw in ("反转", "拐点", "困境", "底部", "修复")):
        return "困境反转"
    if any(kw in text for kw in ("业绩", "利润", "营收", "增长", "增速", "EPS")):
        return "业绩驱动"
    if stock.get("trend_score", 0) >= 5.5 or any(
        kw in text for kw in ("主线", "趋势", "景气", "产业链", "赛道")
    ):
        return "趋势驱动"
    return "信息驱动"


def _repeat_strength(stock: dict) -> str:
    """把重复提及强度转成易读标签。"""
    count = stock.get("post_count", 1) or 1
    if count >= 6:
        return f"🔥强共识({count}次)"
    if count >= 4:
        return f"⭐高关注({count}次)"
    if count >= 3:
        return f"✅多次提及({count}次)"
    if count == 2:
        return "二次提及"
    return "单次提及"


def _exclusion_reason(stock: dict) -> str:
    """识别应剔除或暂不买入的原因。"""
    risk_text = stock.get("risk_display", "")
    tech = stock.get("technical") or {}
    market_cap = stock.get("market_cap_yi")
    pe = stock.get("pe")
    score = stock.get("score", 0)
    buy_score = stock.get("buy_score", 0)
    reasons = []

    if any(kw in risk_text for kw in ("退市", "ST", "重大利空", "回避", "看空")):
        reasons.append("存在明确回避/重大利空信号")
    if market_cap is not None and market_cap < 50:
        reasons.append("市值过小，流动性和波动风险高")
    if pe is not None and pe <= 0:
        reasons.append("盈利不稳定")
    if _is_overheated(stock):
        reasons.append("短线过热，不追高")
    if tech and not tech.get("above_ma20") and stock.get("technical_score", 0) < 5.0:
        reasons.append("技术趋势未修复")
    if score < 5.4:
        reasons.append("综合推荐指数偏低")
    if buy_score < 5.2 and score < 7.0:
        reasons.append("当前买点质量不足")

    return "；".join(reasons[:3])


def _position_advice(stock: dict) -> str:
    """给出初始仓位建议。"""
    tier = stock.get("decision_tier", "")
    market_level = stock.get("market_filter", {}).get("level", "")
    if stock.get("exclusion_reason"):
        return "0仓，仅跟踪"
    if tier == "可执行清单":
        if market_level == "偏强" and stock.get("buy_score", 0) >= 8.0:
            return "标准仓30%-40%，分2次"
        return "观察仓20%-30%，分批"
    if tier == "观察清单":
        return "观察仓0%-20%，等触发"
    return "不建仓"


def _add_trigger(stock: dict) -> str:
    """生成加仓或确认条件。"""
    tech = stock.get("technical") or {}
    ma10 = tech.get("ma10")
    ma20 = tech.get("ma20")
    if stock.get("exclusion_reason"):
        return "排除项解除后再评估"
    if stock.get("decision_tier") == "可执行清单":
        if ma10:
            return f"放量站稳10日线（约 {_fmt_price(ma10)}）可加"
        return "放量突破并维持强势可加"
    if ma20:
        return f"回踩20日线（约 {_fmt_price(ma20)}）不破或放量转强"
    return "出现明确催化或技术转强"


def _decision_tier(stock: dict) -> str:
    """最终三层决策清单。"""
    if stock.get("exclusion_reason"):
        return "剔除/暂不买入"
    if stock.get("market_filter", {}).get("level") in {"偏弱", "过热"}:
        if stock.get("score", 0) >= 7.8:
            return "观察清单"
        if stock.get("score", 0) >= 6.2:
            return "信息清单"
        return "剔除/暂不买入"
    if (
        stock.get("action") == "立即可买"
        and stock.get("buy_score", 0) >= 7.4
        and stock.get("score", 0) >= 7.0
    ):
        return "可执行清单"
    if stock.get("action") in {"等回踩买", "持有等买点", "只观察"} and stock.get("score", 0) >= 6.2:
        return "观察清单"
    return "信息清单"


def _score_breakdown(stock: dict) -> str:
    """把评分拆解成报告中可读的一句话。"""
    detail = stock.get("score_detail", {})
    parts = [
        f"逻辑{detail.get('logic', 0):.1f}",
        f"目标{detail.get('target', 0):.1f}",
        f"趋势{detail.get('trend', 0):.1f}",
        f"涨价{detail.get('long_term_trend', 0):.1f}",
        f"资金{detail.get('capital_flow', 0):.1f}",
        f"量价{detail.get('volume_confirm', 0):.1f}",
        f"基本面{detail.get('fundamentals', 0):.1f}",
        f"技术{stock.get('technical_score', 0):.1f}",
    ]
    market = stock.get("market_filter") or {}
    if market.get("level"):
        parts.append(f"环境{market.get('level')}")
    if stock.get("exclusion_reason"):
        parts.append("风险扣分")
    return " / ".join(parts)


def _apply_expert_decision_fields(stock: dict) -> None:
    """补齐专家交易决策字段。"""
    stock["source_credibility"] = _source_credibility_score(stock)
    stock["opportunity_type"] = _opportunity_type(stock)
    stock["repeat_strength"] = _repeat_strength(stock)
    stock["exclusion_reason"] = _exclusion_reason(stock)
    stock["decision_tier"] = _decision_tier(stock)
    stock["position_advice"] = _position_advice(stock)
    stock["add_trigger"] = _add_trigger(stock)
    stock["score_breakdown"] = _score_breakdown(stock)


def _entry_reference(stock: dict) -> str:
    """根据当前行情和评分生成买入参考，不单列当前价。"""
    current = stock.get("current_price")
    score = stock.get("score", 0)
    change_5d = stock.get("change_5d")
    target = stock.get("target_value")
    target_str = stock.get("target_str", "")

    if current and current > 0:
        if score >= 8.5:
            if change_5d is not None and change_5d > 8:
                return f"不追高；回踩 {_fmt_price(current * 0.94)}-{_fmt_price(current * 0.97)} 再评估"
            return f"分批关注 {_fmt_price(current * 0.97)}-{_fmt_price(current)}；回撤 {_fmt_price(current * 0.94)} 附近加观察"
        if score >= 7.0:
            return f"等回踩 {_fmt_price(current * 0.92)}-{_fmt_price(current * 0.96)} 或催化确认"
        if score >= 5.4:
            return f"仅低吸观察 {_fmt_price(current * 0.88)}-{_fmt_price(current * 0.93)}"
        return "暂不主动买入，等风险释放后再看"

    if target and ("元" in target_str or "块" in target_str):
        return f"无实时行情；目标价倒推 {_fmt_price(target * 0.65)}-{_fmt_price(target * 0.75)} 优先"

    return "缺少行情或价格目标，先做逻辑观察"


def _selection_action(stock: dict) -> str:
    """给快速选股表使用的动作标签。"""
    return _trade_advice(stock)


def _build_stock_risk(stock: dict, risks_list: list[dict], sector_aliases: dict) -> str:
    """汇总个股风险：AI 提取风险 + 全局风险匹配 + 规则兜底。"""
    parts = []

    def add(text: str) -> None:
        text = (text or "").strip("；; \n")
        if text and all(text not in p and p not in text for p in parts):
            parts.append(text)

    add(stock.get("risk_str", ""))

    name = stock.get("name", "")
    raw_sector = stock.get("sector", "")
    norm_sector = _normalize_sector_name(raw_sector, sector_aliases)
    for risk in risks_list:
        target = risk.get("target", "")
        desc = risk.get("desc", "")
        r_type = risk.get("type", "")
        matched_name = name and name in target
        matched_sector = raw_sector and raw_sector in target
        matched_norm_sector = norm_sector and (norm_sector in target or target in norm_sector)
        if matched_name or matched_sector or matched_norm_sector:
            add(f"{r_type}: {desc}" if r_type and desc else desc or r_type)

    for inferred in _infer_stock_risks(stock, norm_sector):
        add(inferred)

    if not parts:
        add("未见明确利空；重点跟踪业绩兑现和板块波动")
    return "；".join(parts[:3])


def _infer_stock_risks(stock: dict, norm_sector: str) -> list[str]:
    """用行情和文本信号补充潜在利空，作为没有明确风险时的兜底。"""
    risks = []
    if not stock.get("target_str"):
        risks.append("缺少量化目标，买点需等待催化确认")
    if not stock.get("price_available"):
        risks.append("缺少行情数据，买入区间需人工核验")

    pe = stock.get("pe")
    if pe is not None:
        if pe <= 0:
            risks.append("盈利尚不稳定，业绩兑现风险较高")
        elif pe > 60:
            risks.append("估值偏高，业绩不及预期可能压制估值")

    market_cap = stock.get("market_cap_yi")
    if market_cap is not None and market_cap < 80:
        risks.append("小市值波动和流动性风险")

    change_5d = stock.get("change_5d")
    if change_5d is not None:
        if change_5d > 8:
            risks.append("短期涨幅偏大，追高风险")
        elif change_5d < -8:
            risks.append("短期走势偏弱，需防继续回撤")

    upside_pct = stock.get("upside_pct")
    if upside_pct is not None and upside_pct < 10:
        risks.append("目标弹性有限，赔率不足")

    if "新能源" in norm_sector:
        risks.append("产业链价格和政策节奏波动")
    elif "半导体" in norm_sector or "芯片" in norm_sector:
        risks.append("订单周期和国产替代进度不及预期")
    elif "AI" in norm_sector or "人工智能" in norm_sector:
        risks.append("算力投入节奏和应用兑现不及预期")

    return risks


def _emphasize_cell(text: str, fallback: str = "-") -> str:
    """突出核心逻辑和目标参考。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return fallback
    return f"**{cleaned}**"


def _format_three_scenario_targets(stock: dict) -> str:
    """格式化三情景目标价为紧凑展示。"""
    agg = (stock.get("target_aggressive") or "").strip()
    mod = (stock.get("target_moderate") or "").strip()
    con = (stock.get("target_conservative") or "").strip()

    if not (agg or mod or con):
        target_str = (stock.get("target_str") or "").strip()
        return f"**{target_str}**" if target_str else "-"

    parts = []
    if con:
        parts.append(f"🔴{con}")
    if mod:
        parts.append(f"🟡{mod}")
    if agg:
        parts.append(f"🟢{agg}")

    return " / ".join(parts) if parts else "-"


def _score_to_stars(score: float) -> str:
    """将 1-10 分数映射为星级。"""
    if score >= 9:
        return "★★★★★"
    elif score >= 7.5:
        return "★★★★☆"
    elif score >= 6:
        return "★★★☆☆"
    elif score >= 4:
        return "★★☆☆☆"
    else:
        return "★☆☆☆☆"


def _strip_json_block(markdown: str) -> str:
    """从 Markdown 中移除所有围栏代码块及其内容。

    覆盖多种格式：
    - ```json ... ```
    - ``` ... ```（无语言标识）
    - ```json{...}```（同一行无换行）
    - 裸 json\n{...}（AI 有时不输出代码围栏标记）
    """
    if not markdown:
        return ""
    # 移除 ```json ... ``` 多行代码块
    cleaned = re.sub(r"```[a-zA-Z]*\s*\n.*?```", "", markdown, flags=re.DOTALL)
    # 移除 ```json{...}``` 同一行无换行的代码块
    cleaned = re.sub(r"```[a-zA-Z]*\{.*?}```", "", cleaned, flags=re.DOTALL)
    # 移除裸 JSON 块：独占一行的 "json" 后跟 JSON 对象
    cleaned = re.sub(r"\njson\s*\n\{.*", "", cleaned, flags=re.DOTALL)
    # 移除可能残留的独立 ``` 及周围空白
    cleaned = re.sub(r"\n?\s*```\s*\n?", "\n", cleaned)
    # 移除 JSON 数据输出等无关章节标题
    cleaned = re.sub(r"^##?\s*JSON\s.*?\n", "", cleaned, flags=re.MULTILINE | re.IGNORECASE)
    return cleaned.strip()


def _trend_badge(stock: dict) -> str:
    """根据趋势分数返回视觉标记。"""
    ts = stock.get("trend_score", 0)
    sec = stock.get("trending_sector", "")
    if ts >= 7 and sec:
        return f"🔥 {sec}"
    elif ts >= 5 and sec:
        return f"📈 {sec}"
    elif ts >= 3:
        return "📈"
    return "-"


def _top_sector_line(trend_scores: dict) -> str:
    """生成主线板块摘要。"""
    trending = [(s, ts) for s, ts in trend_scores.items() if ts >= 5.0]
    if not trending:
        return "暂无明确共振主线"
    trending.sort(key=lambda x: x[1], reverse=True)
    return "、".join(f"{name}({score:.1f})" for name, score in trending[:3])


def _short_stock_names(stocks: list[dict], limit: int) -> str:
    """摘要中展示若干股票名。"""
    names = [_display_stock_name(s) for s in stocks[:limit]]
    return "、".join(names) if names else "无"


def _append_trader_summary(
    parts: list[str],
    enriched: list[dict],
    trend_scores: dict,
    market_filter: dict,
    style_exposure: dict = None,
) -> None:
    """报告顶部的交易员视角摘要。"""
    executable = [s for s in enriched if s.get("decision_tier") == "可执行清单"]
    watch = [s for s in enriched if s.get("decision_tier") == "观察清单"]
    executable.sort(key=lambda s: (s.get("buy_score", 0), s.get("score", 0)), reverse=True)
    watch.sort(key=lambda s: (s.get("score", 0), s.get("buy_score", 0)), reverse=True)

    market_level = market_filter.get("level", "未知") if market_filter else "未知"
    market_desc = market_filter.get("desc", "无市场环境数据") if market_filter else "无市场环境数据"
    parts.append("## 交易员视角摘要\n")
    parts.append(f"- 市场环境：**{market_level}**，{market_desc}")
    parts.append(f"- 今日可执行：**{_short_stock_names(executable, 3)}**")
    parts.append(f"- 等回踩观察：{_short_stock_names(watch, 5)}")
    parts.append(f"- 今日主线板块：{_top_sector_line(trend_scores)}")
    if market_level in {"偏弱", "过热"}:
        parts.append("- 操作纪律：市场环境不友好时，立即买入降级为轻仓或等待确认。")
    else:
        parts.append("- 操作纪律：只从可执行清单中选 1-3 只分批，观察清单等触发条件。")
    parts.append("")

    # 风格暴露
    style_exposure = style_exposure or {}
    if style_exposure:
        parts.append("### 风格暴露\n")
        for style, info in style_exposure.items():
            direction = info.get("direction", "")
            exposure = info.get("exposure", 0)
            parts.append(f"- **{style}**: {exposure:.1f}（{direction}）")
        parts.append("")


def _append_decision_tables(parts: list[str], enriched: list[dict]) -> None:
    """输出可执行/观察决策表，保持邮件篇幅紧凑。"""
    groups = [
        ("可执行清单（最多 3 只，优先考虑）", "可执行清单", 3),
        ("观察清单（逻辑较好，等待买点）", "观察清单", 8),
    ]
    for title, tier, limit in groups:
        stocks = [s for s in enriched if s.get("decision_tier") == tier]
        if tier == "可执行清单":
            stocks.sort(key=lambda s: (s.get("buy_score", 0), s.get("score", 0)), reverse=True)
        else:
            stocks.sort(key=lambda s: (s.get("score", 0), s.get("buy_score", 0)), reverse=True)
        if not stocks:
            continue

        parts.append(f"## {title}\n")
        parts.append(
            "| 股票名称 | 类型 | 仓位 | 买点/触发条件 | 加仓条件 | 止损/减仓 | 评分拆解 | 主要风险 |"
        )
        parts.append(
            "|----------|------|------|---------------|----------|-----------|----------|----------|"
        )
        for stock in stocks[:limit]:
            entry = stock.get("entry_ref", "-")
            parts.append(
                f"| {_display_stock_name(stock)} | {stock.get('opportunity_type', '-')} / "
                f"{stock.get('repeat_strength', '-')} | {stock.get('position_advice', '-')} | "
                f"{entry} | {stock.get('add_trigger', '-')} | {stock.get('exit_trigger', '-')} | "
                f"{stock.get('score_breakdown', '-')} | {stock.get('risk_display', '-')[:100]} |"
            )
        parts.append("")


def _append_mirror_test(parts: list[str], enriched: list[dict]) -> None:
    """镜子测试 + 反向思考板块（借鉴 AI Berkshire 框架）。

    parts.append("> **镜子测试**：如果你不能用 5 句话完整说清楚\"为什么要买这只股票\"，说明理解还不够深。")
    parts.append("> **反向思考**（芒格）：反过来想——这家公司可能失败的最大路径是什么？\n")
    """
    # 取 top 5 股票做镜子测试
    top_stocks = sorted(
        [s for s in enriched if s.get("score", 0) >= 5.0],
        key=lambda s: s.get("buy_score", 0),
        reverse=True,
    )[:5]

    if not top_stocks:
        return

    parts.append("## 镜子测试 & 反向思考\n")
    parts.append("> **镜子测试**：如果你不能用 5 句话完整说清楚\"为什么要买这只股票\"，说明理解还不够深。")
    parts.append("> **反向思考**（芒格）：反过来想——这家公司可能失败的最大路径是什么？\n")

    for stock in top_stocks:
        name = _display_stock_name(stock)
        logic = (stock.get("logic") or "").strip()
        risk = (stock.get("risk_display") or "").strip()
        moat = (stock.get("moat_type") or "").strip()
        score = stock.get("score", 0)

        # Mirror test: can we summarize in 5 sentences?
        logic_quality = "✅ 逻辑清晰" if len(logic) > 50 else "⚠️ 逻辑阐述不足"

        parts.append(f"### {name}（{score:.1f}分）\n")
        parts.append(f"**镜子测试：** {logic_quality}\n")
        if logic:
            # Show first 200 chars as the "5-sentence test"
            summary = logic[:200] + ("..." if len(logic) > 200 else "")
            parts.append(f"> {summary}\n")
        if moat:
            parts.append(f"**护城河：** {moat}\n")
        # Reverse thinking
        if risk:
            parts.append(f"**最大失败路径：** {risk[:200]}\n")
        else:
            parts.append(f"**最大失败路径：** 未明确记录，需补充反面分析\n")
        parts.append("")

    parts.append("---\n")
    parts.append("> ⚠️ **AI研究声明**：以上分析基于知识星球帖子内容，信息丰富度受限于帖子覆盖度。")
    parts.append("> 护城河评分和管理层评估由 AI 从文本中提取，可能存在偏差。")
    parts.append("> 投资确定性取决于生意本质，而非资料数量\n")


def _append_quick_reject(parts: list[str], enriched: list[dict]) -> None:
    """快速否决清单（借鉴 AI Berkshire 8 条红线）。

    在报告末尾列出需要特别警惕的高分但存在重大瑕疵的标的。
    """
    # 找出有明确负面信号的股票
    flagged = []
    for s in enriched:
        score = s.get("score", 0)
        risk = (s.get("risk_display") or "").lower()
        if score < 5.0:
            continue
        flags = []
        if any(kw in risk for kw in ("立案", "处罚", "退市", "st", "造假")):
            flags.append("诚信瑕疵")
        if any(kw in risk for kw in ("减持", "清仓减持", "大股东减持")):
            flags.append("大股东减持")
        if "护城河" in (s.get("moat_type") or "") and "无" in (s.get("moat_type") or ""):
            if score >= 7.0:
                flags.append("护城河薄弱但评分高（高估？）")
        if s.get("pe") and s.get("pe", 0) > 80 and s.get("score", 0) >= 7:
            flags.append("高估值需深度验证")
        if flags:
            flagged.append((s, flags))

    if not flagged:
        return

    parts.append("## ⚠️ 快速否决清单（高分但存在瑕疵）\n")
    parts.append("| 股票 | 评分 | 瑕疵标签 | 建议 |")
    parts.append("|------|------|---------|------|")
    for s, flags in flagged:
        name = _display_stock_name(s)
        score = s.get("score", 0)
        flag_str = "、".join(flags)
        action = "回避" if "诚信" in flag_str else "降权观察"
        parts.append(f"| {name} | {score:.1f} | {flag_str} | {action} |")
    parts.append("")


def _select_report_display_stocks(enriched: list[dict]) -> tuple[list[dict], dict]:
    """选择最终报告展示池，避免高分候选过少时报告失真。

    3 分以上仍是正式推荐阈值；当正式推荐过少时，补充展示 2 分以上的
    高分观察候选，并在报告中明确标注为观察池，不伪装成买入推荐。
    """
    sorted_stocks = sorted(
        enriched or [],
        key=lambda s: (s.get("buy_score", 0), s.get("score", 0)),
        reverse=True,
    )
    recommendations = [
        s for s in sorted_stocks
        if s.get("score", 0) >= REPORT_RECOMMENDATION_THRESHOLD
    ]
    display_stocks = recommendations
    mode = "recommendation"

    if (
        len(recommendations) < REPORT_MIN_RECOMMENDATIONS
        and len(sorted_stocks) > len(recommendations)
    ):
        observation_pool = [
            s for s in sorted_stocks
            if s.get("score", 0) >= REPORT_OBSERVATION_THRESHOLD
        ][:REPORT_MIN_VISIBLE_STOCKS]
        if len(observation_pool) > len(display_stocks):
            display_stocks = observation_pool
            mode = "adaptive_observation"

    meta = {
        "candidate_count": len(sorted_stocks),
        "recommendation_count": len(recommendations),
        "display_count": len(display_stocks),
        "threshold": REPORT_RECOMMENDATION_THRESHOLD,
        "observation_threshold": REPORT_OBSERVATION_THRESHOLD,
        "mode": mode,
    }
    return display_stocks, meta


def _append_report_filter_note(parts: list[str], meta: dict) -> None:
    """展示提取和过滤统计，让报告数量异常时有解释。"""
    candidate_count = meta.get("candidate_count", 0)
    recommendation_count = meta.get("recommendation_count", 0)
    display_count = meta.get("display_count", 0)
    threshold = meta.get("threshold", REPORT_RECOMMENDATION_THRESHOLD)
    observation_threshold = meta.get("observation_threshold", REPORT_OBSERVATION_THRESHOLD)

    parts.append("## 提取与过滤概览\n")
    parts.append(
        f"> 可评分候选 **{candidate_count}** 只；"
        f"{threshold:.0f} 分以上正式推荐 **{recommendation_count}** 只；"
        f"本报告展示 **{display_count}** 只。"
    )
    if meta.get("mode") == "adaptive_observation":
        parts.append(
            f"> 正式推荐数量偏少，已补充展示 {observation_threshold:.0f} 分以上高分观察候选。"
            "这些标的用于复核和等待买点，不等同于立即买入。"
        )
    parts.append("")


def _rebuild_report(enriched: list[dict], original_markdown: str, trend_data: dict = None) -> str:
    """用增强后的股票数据重建 Markdown 报告。

    新增：快速选股清单、行业趋势概览，并突出买入参考、风险点、核心逻辑和目标参考。
    """
    if trend_data is None:
        trend_data = {}
    style_exposure = trend_data.get("style_exposure", {})
    all_enriched = list(enriched or [])
    enriched, display_meta = _select_report_display_stocks(all_enriched)
    trend_data["display_meta"] = display_meta
    trend_scores = trend_data.get("scores", {})
    sector_groups = trend_data.get("groups", {})
    sector_logic_map = trend_data.get("logic_map", {})
    market_filter = trend_data.get("market_filter", {})
    # 先移除 JSON 代码块，避免泄露到最终输出
    original_markdown = _strip_json_block(original_markdown)
    parts = []

    # ── 市场状态自适应过滤参数 ──
    regime = trend_data.get("market_regime", {})
    score_threshold = regime.get("score_threshold", REPORT_RECOMMENDATION_THRESHOLD)
    max_per_sector = regime.get("max_per_sector", 3)

    # ── 简单过滤：仅保留得分达标的股票 ──
    passed = [s for s in enriched if s.get("score", 0) >= score_threshold]
    filtered_out = [s for s in enriched if s.get("score", 0) < score_threshold]
    passed.sort(key=lambda s: s.get("score", 0), reverse=True)

    # ── 组合层风控：行业集中度限制 ──
    passed = _apply_portfolio_constraints(passed, max_per_sector=max_per_sector)

    # ── 流动性过滤：剔除低流动性标的 ──
    passed = _apply_liquidity_filter(passed)

    # ── 滑点与冲击成本估算 ──
    for s in passed:
        _estimate_slippage(s)

    # ── 组合层风控：个股间相关性控制 ──
    try:
        from portfolio_builder import filter_by_correlation, select_allocation_method, format_portfolio_summary
        passed = filter_by_correlation(passed, max_corr=0.7)
        # 智能仓位分配（Kelly/风险平价/波动率反比自动选择）
        passed = select_allocation_method(passed, method="auto")
        portfolio_summary = format_portfolio_summary(passed, regime)
    except Exception:
        portfolio_summary = ""

    # 诊断统计
    total_scored = len(enriched)
    total_passed = len(passed)
    filter_meta = {
        "total_scored": total_scored,
        "total_passed": total_passed,
        "total_filtered": len(filtered_out),
        "score_threshold": 5.0,
        "ma5_tolerance": 3.0,
    }

    # 市场状态摘要
    if regime and regime.get("label"):
        from market_regime import format_regime_summary
        parts.append("## 📊 市场状态\n")
        parts.append(format_regime_summary(regime))
        parts.append("")

    # 组合概览
    if portfolio_summary:
        parts.append("## 💼 组合概览\n")
        parts.append(portfolio_summary)
        parts.append("")

    _append_trader_summary(parts, passed, trend_scores, market_filter, style_exposure)
    _append_decision_tables(parts, passed)
    _append_mirror_test(parts, passed)

    # 过滤统计
    parts.append("## 过滤概览\n")
    parts.append(
        f"> 可评分候选 **{total_scored}** 只；"
        f"评分 ≥{score_threshold:.0f} 分入选 **{total_passed}** 只。"
    )
    if filtered_out and total_passed == 0:
        parts.append(
            f"> 无股票达到评分阈值。被过滤的 {len(filtered_out)} 只中，"
            f"最高分 {max((s.get('score',0) for s in filtered_out), default=0):.1f}，"
            f"阈值 {score_threshold:.0f}。"
        )
    parts.append("")

    # ── -1. 当前最佳买入清单（仅从通过筛选的股票中选取）──
    buy_candidates = [
        s for s in passed
        if s.get("buy_score", 0) >= 6.5
        and s.get("action") in {"立即可买", "等回踩买"}
    ]
    buy_candidates.sort(
        key=lambda s: (s.get("buy_score", 0), s.get("score", 0)),
        reverse=True,
    )
    if buy_candidates:
        parts.append("## 最适合买入清单\n")
        if market_filter:
            parts.append(
                f"> 市场环境过滤：**{market_filter.get('level', '未知')}**，"
                f"{market_filter.get('desc', '无市场环境数据')}\n"
            )
        parts.append(
            "| 股票名称 | 机会类型 | 周期 | 加仓条件 | 卖出/减仓触发 | 推荐指数 | 核心逻辑 | 风险点 |"
        )
        parts.append(
            "|----------|----------|------|----------|----------------|----------|----------|--------|"
        )
        for i, stock in enumerate(buy_candidates[:8], 1):
            parts.append(
                f"| {_display_stock_name(stock)} | {stock.get('opportunity_type', '-')} | "
                f"{stock.get('trade_period', '-')} | "
                f"{stock.get('add_trigger', '-')} | "
                f"{stock.get('exit_trigger', '-')} | "
                f"{_format_score_display(stock)} | "
                f"{_emphasize_cell(stock.get('logic', '')[:70] if stock.get('logic') else '')} | "
                f"{stock.get('risk_display', '-')[:90]} |"
            )
        parts.append("")

    # ── 0. 快速选股总览（仅展示通过筛选的股票）──
    parts.append("## 快速选股清单（按推荐指数降序）\n")
    parts.append(
        "| 股票名称 | 机会类型 | 周期 | 当前市值 | 卖出/减仓触发 | 技术面 | 评分拆解 | 核心逻辑 | 目标参考 | 风险点 | 推荐指数 |"
    )
    parts.append(
        "|----------|----------|------|----------|----------------|--------|----------|----------|----------|--------|----------|"
    )

    if not passed:
        parts.append(
            "| - | 本次无股票通过筛选 | - | - | - | - | - | - | - | - | - |"
        )

    for stock in passed:
        name = _display_stock_name(stock)
        market_cap_str = _fmt_market_cap(stock.get("market_cap_yi"))
        target_str = _emphasize_cell(stock["target_str"])
        logic = _emphasize_cell(stock["logic"][:70] if stock["logic"] else "")
        risk = stock.get("risk_display", "-")[:70]
        score_str = _format_score_display(stock)

        parts.append(
            f"| {name} | "
            f"{stock.get('opportunity_type', '-')} | {stock.get('trade_period', '-')} | "
            f"{market_cap_str} | "
            f"{stock.get('exit_trigger', '-')} | {stock.get('technical_view', '-')} | "
            f"{stock.get('score_breakdown', '-')} | {logic} | {target_str} | "
            f"{risk} | {score_str} |"
        )

    parts.append("")

    if not passed and enriched:
        parts.append("## 过滤诊断\n")
        parts.append(
            f"- 共提取 {total_scored} 只可评分候选，但无一达到评分阈值 {score_threshold:.0f} 分。"
        )
        parts.append(
            f"- 最高分: {max((s.get('score',0) for s in enriched), default=0):.1f}，"
            f"平均分: {sum(s.get('score',0) for s in enriched)/len(enriched):.1f}。"
        )
        parts.append("")

    if not enriched:
        parts.append("## 提取诊断\n")
        parts.append(
            "- 股票 AI 批次已完成，但结构化 JSON 中没有可评分的 `quantitative` 或 `elastic` 个股。"
        )
        parts.append(
            "- 如果运行日志出现 `code=1059`，本次可能只抓到部分帖子；爬虫已改为 15 秒分页间隔并对 1059 冷却重试。"
        )
        parts.append(
            '- 若 AI 只在"细分板块机会"中列出核心标的，后续运行会自动把这些标的补入弹性候选并参与评分/同花顺同步。'
        )
        parts.append("")

    # ── 0.5. 行业趋势概览 ──
    trending = [(s, ts) for s, ts in trend_scores.items() if ts >= 5.0]
    if trending:
        trending.sort(key=lambda x: x[1], reverse=True)
        parts.append("## 🔥 行业趋势概览\n")
        parts.append(
            "| 行业板块 | 趋势强度 | 涉及标的数 | 核心逻辑 | 信号解读 |"
        )
        parts.append(
            "|----------|----------|------------|-------------|----------|"
        )
        for sector_name, ts in trending:
            stocks_in = sector_groups.get(sector_name, [])
            n = len(stocks_in)
            changes = [
                s.get("change_5d") for s in stocks_in
                if s.get("change_5d") is not None
            ]
            logic = sector_logic_map.get(sector_name, "")
            desc = _trend_signal_desc(ts, changes, n)
            stars_trend = "🔥" * min(3, max(1, int(ts / 3.3)))
            parts.append(
                f"| {stars_trend} {sector_name} | **{ts:.1f}** | {n} | "
                f"{_emphasize_cell(logic[:70] if logic else '')} | {desc} |"
            )
        parts.append("")

    # ── 1. 量化目标（增强，仅展示通过筛选的）──
    q_stocks = [s for s in passed if s["category"] == "quantitative"]
    if q_stocks:
        parts.append("## 一、有明确量化目标的股票（增强）\n")
        parts.append(
            "| 序号 | 股票名称 | 当前市值 | 买入参考 | 核心逻辑 | 护城河 | 目标参考(保守/稳健/激进) | 风险点/潜在利空 | 推荐指数 | 趋势 | 来源 |"
        )
        parts.append(
            "|------|----------|----------|----------|----------|----------|------------------------|----------------|----------|------|------|"
        )
        for i, s in enumerate(q_stocks, 1):
            trend_badge = _trend_badge(s)
            parts.append(
                f"| {i} | {_display_stock_name(s)} | {_fmt_market_cap(s.get('market_cap_yi'))} | "
                f"{s.get('entry_ref', '-')} | "
                f"{_emphasize_cell(s['logic'][:60] if s['logic'] else '')} | "
                f"{s.get('moat_type', '-')} | "
                f"{_format_three_scenario_targets(s)} | {s.get('risk_display', '-')[:80]} | {_format_score_display(s)} | "
                f"{trend_badge} | {s['source']} |"
            )
        parts.append("")

    # ── 2. 弹性标的（增强，仅展示通过筛选的）──
    e_stocks = [s for s in passed if s["category"] == "elastic"]
    if e_stocks:
        parts.append("## 二、产业趋势中弹性最大的标的（增强）\n")
        parts.append(
            "| 序号 | 股票名称 | 当前市值 | 买入参考 | 所属赛道 | 核心逻辑 | 护城河 | 目标情景 | 风险点 | 推荐指数 | 趋势 | 来源 |"
        )
        parts.append(
            "|------|----------|----------|----------|----------|----------|----------|----------------|----------------|----------|------|------|"
        )
        for i, s in enumerate(e_stocks, 1):
            trend_badge = _trend_badge(s)
            parts.append(
                f"| {i} | {_display_stock_name(s)} | {_fmt_market_cap(s.get('market_cap_yi'))} | "
                f"{s.get('entry_ref', '-')} | {s['sector'] or '-'} | {_emphasize_cell(s['logic'][:60] if s['logic'] else '')} | "
                f"{s.get('moat_type', '-')} | {_format_three_scenario_targets(s)} | {s.get('risk_display', '-')[:60]} | {_format_score_display(s)} | "
                f"{trend_badge} | {s['source']} |"
            )
        parts.append("")

    # ── 3 & 4. 板块和风险（保留原格式） ──
    # 从原始 markdown 中提取第三、四部分
    section3 = _extract_section(original_markdown, "三、", "四、")
    section4 = _extract_section(original_markdown, "四、", None)

    if section3:
        # 二次清理，防止 JSON 残留
        section3 = _strip_json_block(section3)
        if section3.strip():
            parts.append(section3.strip())
            parts.append("")
    if section4:
        section4 = _strip_json_block(section4)
        if section4.strip():
            parts.append(section4.strip())
            parts.append("")

    # ── 5. 高分股票原文内容 ──
    # 从全部评分候选中选取高分股票（不限于通过筛选的）
    all_scored = sorted(enriched or [], key=lambda s: s.get("score", 0), reverse=True)
    high_score_stocks = [s for s in all_scored if s.get("score", 0) >= 5.0][:5]
    if high_score_stocks:
        raw_posts = _load_raw_posts_for_stocks(high_score_stocks)
        if raw_posts:
            parts.append("## 高分股票原文摘录\n")
            for stock in high_score_stocks:
                name = _display_stock_name(stock)
                score = stock.get("score", 0)
                source = stock.get("source", "")
                logic = stock.get("logic", "")
                target_str = stock.get("target_str", "")
                post_content = raw_posts.get(source, "")
                parts.append(f"### {name}（评分 {score:.1f}）\n")
                parts.append(f"**投资逻辑：** {logic}\n")
                if target_str:
                    parts.append(f"**目标参考：** {target_str}\n")
                if post_content:
                    display_content = post_content[:500]
                    if len(post_content) > 500:
                        display_content += "..."
                    parts.append(f"**原文摘录：**\n> {display_content}\n")
                parts.append("")

    # 快速否决清单
    _append_quick_reject(parts, passed)

    return "\n".join(parts)


def _display_stock_name(stock: dict) -> str:
    """最终报告中的股票名称展示，保留特别来源标注。"""
    name = stock.get("name", "")
    if stock.get("foreign_research"):
        return f"{name}（国外投行研报）"
    return name


def _load_raw_posts_for_stocks(stocks: list[dict]) -> dict[str, str]:
    """加载原始帖子内容，用于高分股票的原文展示。

    Returns:
        {source_label: post_content} 映射。
    """
    import re
    from pathlib import Path

    result = {}
    raw_dir = Path(__file__).parent / "data" / "raw"
    if not raw_dir.exists():
        return result

    # 找到最新的 raw 文件
    raw_files = sorted(raw_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not raw_files:
        return result

    try:
        import json
        with open(raw_files[0], "r", encoding="utf-8") as f:
            posts = json.load(f)
    except Exception:
        return result

    # 构建帖子编号到内容的映射
    post_map = {}
    for i, post in enumerate(posts):
        post_num = i + 1
        content = post.get("content", "") or post.get("text", "")
        if content:
            post_map[f"帖子 {post_num}"] = content[:800]
            post_map[f"帖子{post_num}"] = content[:800]

    # 匹配高分股票的 source
    for stock in stocks:
        source = stock.get("source", "")
        if source in post_map:
            result[source] = post_map[source]

    return result


def _extract_section(markdown: str, start_marker: str, end_marker: Optional[str] = None) -> str:
    """从 Markdown 中提取指定段落。"""
    start_idx = markdown.find(f"## {start_marker}")
    if start_idx == -1:
        return ""
    if end_marker:
        end_idx = markdown.find(f"## {end_marker}", start_idx + 1)
        if end_idx == -1:
            return markdown[start_idx:]
        return markdown[start_idx:end_idx]
    return markdown[start_idx:]


def _build_stock_report(merged: str, post_count: int) -> str:
    """包装最终的股票机会报告。"""
    # 最终防线：确保 JSON 已被移除
    merged = _strip_json_block(merged)
    generated_at = _now_shanghai().strftime("%Y-%m-%d %H:%M:%S 北京时间")
    lines = [
        "# 知识星球股票投资机会提取（增强版）",
        "",
        f"> 分析帖子数: {post_count} 篇",
        f"> 生成时间: {generated_at}",
        f"> 数据来源: 腾讯行情 API（市值等行情数据）",
        "",
        merged,
        "",
        "---",
        "",
        "*免责声明：本报告由AI自动生成，仅供参考，不构成任何投资建议。"
        "投资有风险，入市需谨慎。*",
    ]
    return "\n".join(lines)


def _empty_report() -> str:
    """无帖子时的空报告。"""
    generated_at = _now_shanghai().strftime("%Y-%m-%d %H:%M:%S 北京时间")
    return (
        "# 知识星球股票投资机会提取\n\n"
        f"> 生成时间: {generated_at}\n\n"
        "暂无帖子数据，无法提取股票机会。\n"
    )
