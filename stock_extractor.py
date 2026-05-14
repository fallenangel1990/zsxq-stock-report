"""股票机会提取模块。

从知识星球帖子中提取股票投资机会，使用 AI 进行分类整理，
增强实时价格、计算上涨空间和推荐指数，按优先级排序输出。
"""

import json
import re
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml


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


def extract_stock_opportunities(
    posts: list[dict],
    batch_size: int = 30,
    verbose: bool = True,
) -> str:
    """从帖子列表中提取股票投资机会，增强实时价格和推荐指数。

    Args:
        posts: 清洗后的结构化帖子列表。
        batch_size: 每批处理的帖子数。

    Returns:
        Markdown 格式的增强股票机会报告（含价格、上涨空间、推荐指数、排序）。
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
            batch_num,
            total_batches,
        ))

    # 并发调用 AI（最多 3 个并发，避免触发 API 限流）
    max_workers = min(3, len(batches))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(_extract_stocks_batch, *b): b[2]
            for b in batches
        }
        # 按批次号收集结果以保持顺序
        results = {}
        for future in as_completed(future_to_idx):
            batch_num = future_to_idx[future]
            try:
                report = future.result()
                results[batch_num] = report
                batch_json = _parse_stock_json(report)
                _merge_json(all_stocks_json, batch_json)
                q = len(batch_json.get("quantitative", []))
                e = len(batch_json.get("elastic", []))
                if verbose:
                    print(f"  [股票 {batch_num}/{total_batches}] 完成 (量化:{q} 弹性:{e})", flush=True)
            except Exception as exc:
                print(f"  [股票 {batch_num}/{total_batches}] 失败: {exc}", flush=True)

    # 按批次号排序结果
    batch_reports = [results[k] for k in sorted(results.keys())]

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
        print("获取实时股价并计算推荐指数...", flush=True)
    enriched, trend_data = _enrich_and_score(all_stocks_json, verbose=verbose)
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
    batch_num: int,
    total_batches: int,
) -> str:
    """将一批帖子发送给 AI，提取股票机会并输出表格 + JSON。"""
    posts_text = "\n\n---\n\n".join(
        _format_post_for_stocks(p, i + 1) for i, p in enumerate(batch)
    )

    system = (
        "你是一位专业的A股/港股/美股投资分析师，擅长从大量财经资讯中"
        "精确提取和分类股票投资机会。你输出干净、结构化的Markdown表格和JSON数据，"
        "绝不输出分析过程或解释性文字。对于没有明确投资机会的内容，"
        "直接说明\"无符合条件的标的\"而不编造。"
    )

    prompt = f"""请分析以下知识星球专栏的帖子内容（第 {batch_num}/{total_batches} 批），
提取其中提到的股票投资机会，并按以下四个类别整理成表格。

对于每只股票：
- 只提取被明确推荐、看好、或给出具体分析逻辑的股票
- 区分"投资建议"和"背景提及"——只在表格中包含有明确投资逻辑的股票
- 如果有股票代码（6位A股代码或境外代码），请务必包含
- 如果同一只股票出现在多个帖子中，合并为一条最完整的记录

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

注意事项：
- "来源帖子"列填写"帖子 X"格式的引用（X为帖子编号）
- 如果某个类别没有符合条件的标的，写"**本批次暂无符合条件的标的**"
- 不要输出表格以外的解释性文字
- 表格使用标准Markdown格式

## JSON 数据输出（重要！）
请在所有表格之后，输出一个 JSON 代码块（```json），包含所有表格中提取的结构化数据：
```json
{{
  "quantitative": [
    {{"name": "股票名称", "code": "股票代码或空字符串", "logic": "投资逻辑简述", "target": "量化参考原文", "source": "帖子X"}}
  ],
  "elastic": [
    {{"name": "股票名称", "code": "股票代码或空字符串", "sector": "所属赛道", "logic": "核心逻辑简述", "source": "帖子X"}}
  ],
  "sectors": [
    {{"sector": "板块名称", "stocks": "核心标的名称列表", "logic": "板块逻辑", "source": "帖子X"}}
  ],
  "risks": [
    {{"type": "风险类型", "target": "涉及标的/板块", "desc": "风险描述", "source": "帖子X"}}
  ]
}}
```
仅包含表格中实际列出的条目，空数组写 []。JSON 块必须放在 Markdown 表格输出之后。
不要改变 Markdown 表格的输出格式。

以下是帖子内容：

{posts_text}"""

    return client.create(system=system, prompt=prompt, max_tokens=4096)


def _merge_stock_reports(client, batch_reports: list[str]) -> str:
    """合并多批次股票报告，去重并统一编号。"""
    combined = "\n\n---\n\n".join(
        f"## 第 {i + 1} 批次\n{r}" for i, r in enumerate(batch_reports)
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

以下是各批次结果：

{combined}

请输出合并后的完整报告（四部分 Markdown 表格 + JSON 代码块）。"""

    return client.create(system=system, prompt=prompt, max_tokens=4096)


# ═══════════════════════════════════════════════════════════════
# 增强层：价格获取 + 评分 + 排序
# ═══════════════════════════════════════════════════════════════

def _parse_stock_json(markdown: str) -> dict:
    """从 AI 输出文本中提取 JSON 结构化数据。

    优先查找 ```json 代码块，若不存在则回退到正则解析 Markdown 表格。
    """
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


def _merge_json(target: dict, source: dict) -> None:
    """将 source 中的股票数据合并到 target，按股票名称去重。

    直接修改 target dict，不返回值。
    """
    # 合并 quantitative 和 elastic — 按 name 去重
    for category in ("quantitative", "elastic"):
        existing_names = {s.get("name", "") for s in target[category]}
        for stock in source.get(category, []):
            name = stock.get("name", "")
            if name and name not in existing_names:
                target[category].append(stock)
                existing_names.add(name)

    # 合并 sectors — 按 sector 名去重
    existing_sectors = {s.get("sector", "") for s in target["sectors"]}
    for sector in source.get("sectors", []):
        s_name = sector.get("sector", "")
        if s_name and s_name not in existing_sectors:
            target["sectors"].append(sector)
            existing_sectors.add(s_name)

    # 合并 risks — 按 type 名去重
    existing_risks = {r.get("type", "") for r in target["risks"]}
    for risk in source.get("risks", []):
        r_type = risk.get("type", "")
        if r_type and r_type not in existing_risks:
            target["risks"].append(risk)
            existing_risks.add(r_type)


def _fallback_parse_tables(markdown: str) -> dict:
    """从 Markdown 表格中回退解析股票数据（JSON 解析失败时使用）。"""
    result = {"quantitative": [], "elastic": [], "sectors": [], "risks": []}

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
                    "source": cols[4] if len(cols) > 4 else "",
                })
            elif key == "elastic" and len(cols) >= 5:
                result[key].append({
                    "name": _extract_stock_name(cols[0]),
                    "code": _extract_code(cols[1]) if len(cols) > 1 else "",
                    "sector": cols[2] if len(cols) > 2 else "",
                    "logic": cols[3] if len(cols) > 3 else "",
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


def _enrich_and_score(stocks_json: dict, verbose: bool = True) -> tuple[list[dict], dict]:
    """增强股票数据：获取实时价格，计算上涨空间和推荐指数。

    Returns:
        按推荐指数降序排列的增强股票列表。
    """
    # 收集所有有代码的股票
    all_stocks = {}
    for entry in stocks_json.get("quantitative", []):
        code = entry.get("code", "").strip()
        name = entry.get("name", "")
        if name:
            key = code if code else name
            if key not in all_stocks:
                all_stocks[key] = {
                    "name": name, "code": code,
                    "logic": entry.get("logic", ""),
                    "target_str": entry.get("target", ""),
                    "target_value": _parse_target_value(entry.get("target", "")),
                    "source": entry.get("source", ""),
                    "category": "quantitative",
                    "sector": "",
                    "post_count": 1,
                    "quality": _assess_quality(entry.get("target", "")),
                }
            else:
                # 合并重复股票
                existing = all_stocks[key]
                existing["post_count"] += 1
                if not existing["target_str"] and entry.get("target"):
                    existing["target_str"] = entry.get("target", "")
                    existing["target_value"] = _parse_target_value(entry.get("target", ""))
                    existing["quality"] = _assess_quality(entry.get("target", ""))

    for entry in stocks_json.get("elastic", []):
        code = entry.get("code", "").strip()
        name = entry.get("name", "")
        sector = entry.get("sector", "")
        if name:
            key = code if code else name
            if key not in all_stocks:
                all_stocks[key] = {
                    "name": name, "code": code,
                    "logic": entry.get("logic", ""),
                    "target_str": "", "target_value": None,
                    "source": entry.get("source", ""),
                    "category": "elastic",
                    "sector": sector,
                    "post_count": 1,
                    "quality": 0.3,  # 定性推荐，信息质量较低
                }
            else:
                all_stocks[key]["post_count"] += 1
                if sector and not all_stocks[key]["sector"]:
                    all_stocks[key]["sector"] = sector

    if not all_stocks:
        return [], {}

    # 批量获取价格
    valid_codes = [s["code"] for s in all_stocks.values() if s["code"] and s["code"].isdigit() and len(s["code"]) == 6]
    if valid_codes and verbose:
        print(f"  获取 {len(valid_codes)} 只 A 股实时行情...", flush=True)

    prices = {}
    changes_5d = {}
    if valid_codes:
        from price_fetcher import fetch_prices, fetch_5day_changes
        prices = fetch_prices(valid_codes)
        changes_5d = fetch_5day_changes(valid_codes)

    if verbose and prices:
        print(f"  成功获取 {len(prices)} 只股票行情", flush=True)
    if verbose and changes_5d:
        print(f"  成功获取 {len(changes_5d)} 只股票 5 日涨跌幅", flush=True)

    # 计算板块热度（sectors 中的 stocks 字符串被提及的总字符数作为代理）
    sector_heat = {}
    for entry in stocks_json.get("sectors", []):
        sector_name = entry.get("sector", "")
        stocks_str = entry.get("stocks", "")
        if sector_name:
            sector_heat[sector_name] = len(stocks_str)

    # 加载评分配置权重
    scoring = _load_scoring_config()
    w_upside = scoring.get("upside_weight", 0.30)
    w_quality = scoring.get("quality_weight", 0.20)
    w_consensus = scoring.get("consensus_weight", 0.16)
    w_sector = scoring.get("sector_weight", 0.14)
    w_trend = scoring.get("trend_weight", 0.10)
    w_fundamentals = scoring.get("fundamentals_weight", 0.10)

    # 行业趋势检测
    sector_aliases = scoring.get("sector_aliases", {})
    trend_config = scoring.get("trend", {})
    trend_scores, sector_groups, sector_logic_map = _detect_sector_trends(
        all_stocks, stocks_json.get("sectors", []),
        sector_heat, sector_aliases, trend_config,
    )
    if verbose and trend_scores:
        trending = [(s, ts) for s, ts in trend_scores.items() if ts >= 5.0]
        if trending:
            trending.sort(key=lambda x: x[1], reverse=True)
            names = ", ".join(f"{s}({ts})" for s, ts in trending)
            print(f"  行业趋势检测: {names}", flush=True)

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
        # 1. 上涨空间得分（0-10）
        upside_score = 0
        if upside_pct is not None:
            # 上涨空间映射到 0-10 分（30%+ = 10 分，0% = 0 分）
            upside_score = min(10, max(0, upside_pct / 3))

        # 2. 信息质量得分（0-10）
        quality_score = stock["quality"] * 10

        # 3. 分析师共识得分（0-10）
        consensus_score = min(10, stock["post_count"] * 2.5)  # 4 篇帖子 = 满分

        # 4. 板块热度得分（0-10）
        sector_score = 0
        if stock["sector"]:
            heat = sector_heat.get(stock["sector"], 0)
            sector_score = min(10, heat / 20)  # 200 字符 = 满分

        # 5. 行业趋势得分（0-10）
        trend_score = 0.0
        norm_sec = _normalize_sector_name(stock.get("sector", ""), sector_aliases)
        if norm_sec and norm_sec in trend_scores:
            trend_score = trend_scores[norm_sec]

        # 6. 公司基本面得分（0-10）
        fundamentals_score = _fundamentals_score(pe, pb, market_cap)

        total_score = (
            w_upside * upside_score
            + w_quality * quality_score
            + w_consensus * consensus_score
            + w_sector * sector_score
            + w_trend * trend_score
            + w_fundamentals * fundamentals_score
        )
        # 映射到 1-10
        total_score = round(max(1.0, min(10.0, total_score)), 1)

        # 生成星级
        stars = _score_to_stars(total_score)

        enriched.append({
            **stock,
            "current_price": current_price,
            "pe": pe,
            "pb": pb,
            "market_cap_yi": market_cap,
            "change_5d": change_5d,
            "upside_pct": upside_pct,
            "score": total_score,
            "stars": stars,
            "price_available": price_info is not None,
            "score_detail": {
                "upside": round(upside_score, 1),
                "quality": round(quality_score, 1),
                "consensus": round(consensus_score, 1),
                "sector": round(sector_score, 1),
                "trend": round(trend_score, 1),
                "fundamentals": round(fundamentals_score, 1),
            },
            "trend_score": round(trend_score, 1),
            "trending_sector": norm_sec if trend_score >= 5.0 else "",
            "fundamentals_score": round(fundamentals_score, 1),
        })

    # 按推荐指数降序排列
    enriched.sort(key=lambda x: x["score"], reverse=True)

    # 构建趋势数据供报告层使用
    trend_data = {
        "scores": trend_scores,
        "groups": sector_groups,
        "logic_map": sector_logic_map,
    }
    return enriched, trend_data


def _fundamentals_score(pe, pb, market_cap_yi) -> float:
    """基于 PE / PB / 市值 计算公司基本面得分（0-10）。

    PE 估值（0-5 分）：
      - PE < 0（亏损）→ 1 分
      - 0 < PE ≤ 15 → 5 分（便宜）
      - 15 < PE ≤ 25 → 4 分
      - 25 < PE ≤ 40 → 3 分
      - 40 < PE ≤ 60 → 2 分
      - PE > 60 → 1 分

    PB 估值（0-3 分）：
      - PB ≤ 1.5 → 3 分（破净或低PB）
      - 1.5 < PB ≤ 3 → 2 分
      - PB > 3 → 1 分

    市值稳定性（0-2 分）：
      - ≥ 500 亿 → 2 分（大盘蓝筹）
      - 100-500 亿 → 1 分
      - < 100 亿 → 0 分

    总分 = PE + PB + 市值，缺数据时对应项给中间分。
    """
    score = 0.0

    # PE 估值分（0-5）
    if pe is not None:
        if pe <= 0:
            score += 1.0
        elif pe <= 15:
            score += 5.0
        elif pe <= 25:
            score += 4.0
        elif pe <= 40:
            score += 3.0
        elif pe <= 60:
            score += 2.0
        else:
            score += 1.0
    else:
        score += 2.5  # 缺数据给中间分

    # PB 估值分（0-3）
    if pb is not None:
        if pb <= 1.5:
            score += 3.0
        elif pb <= 3:
            score += 2.0
        else:
            score += 1.0
    else:
        score += 1.5

    # 市值稳定性分（0-2）
    if market_cap_yi is not None:
        if market_cap_yi >= 500:
            score += 2.0
        elif market_cap_yi >= 100:
            score += 1.0
        # < 100 亿不加分
    else:
        score += 1.0

    return score


def _assess_quality(target_str: str) -> float:
    """评估信息质量（0-1 分）。

    - 有明确目标价（元）：0.9
    - 有目标市值（亿）：0.8
    - 有业绩预测（增速/利润）：0.6
    - 有估值参考（PE/PB）：0.7
    - 纯定性描述：0.3
    """
    if not target_str:
        return 0.3
    score = 0.3
    if re.search(r"\d+[\.\d]*\s*(?:元|块)", target_str):
        score = max(score, 0.9)
    if re.search(r"\d+[\.\d]*\s*(?:亿|[eE]\b)", target_str):
        score = max(score, 0.8)
    if re.search(r"(?:PE|PB|PS|估值)\s*\d+", target_str):
        score = max(score, 0.7)
    if re.search(r"(?:增速|增长|利润|营收|收入)\s*\d+", target_str):
        score = max(score, 0.6)
    return score


# ── 行业趋势检测：关键词 ──

_POSITIVE_LOGIC_KW = [
    "景气", "向好", "拐点", "反转", "超预期", "加速", "爆发",
    "政策支持", "国产替代", "需求旺盛", "供不应求", "涨价",
    "上行", "增长", "利好", "催化", "高景气", "确定性",
    "底部", "估值修复", "戴维斯双击", "双击",
]
_NEGATIVE_LOGIC_KW = [
    "下行", "衰退", "过剩", "内卷", "降价", "利空",
    "政策风险", "不确定性", "需求疲软", "库存高企",
    "景气度下降", "见顶", "泡沫", "炒作",
]


def _normalize_sector_name(raw_sector: str, aliases: dict) -> str:
    """将 AI 自由文本板块名标准化为规范名称。

    匹配策略（按优先级）：
      1. 精确匹配
      2. 大小写不敏感精确匹配
      3. 去除括号内容后匹配（如 "锂电材料（铁锂正极）" → "锂电材料"）
      4. 关键字包含匹配（别名 key 出现在原文中）
      5. 返回原文
    """
    if not raw_sector or not raw_sector.strip():
        return ""
    raw_sector = raw_sector.strip()
    # 1. 精确匹配
    if raw_sector in aliases:
        return aliases[raw_sector]
    # 2. 大小写不敏感
    raw_lower = raw_sector.lower()
    for key, canonical in aliases.items():
        if key.lower() == raw_lower:
            return canonical
    # 3. 去除中文/英文括号内容后再匹配
    import re as _re
    stripped = _re.sub(r"[（(][^)）]*[)）]", "", raw_sector).strip()
    if stripped and stripped != raw_sector:
        result = _normalize_sector_name(stripped, aliases)
        if result != stripped:
            return result
    # 4. 关键字包含匹配（别名 key 长度 >= 2 且出现在原文中）
    for key, canonical in sorted(aliases.items(), key=lambda x: -len(x[0])):
        if len(key) >= 2 and key in raw_sector:
            return canonical
    return raw_sector


def _sentiment_score(logic_text: str) -> float:
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
    """从 Markdown 中移除 ```json ... ``` 代码块。

    分两步：先匹配标准的多行 JSON 块，再处理无换行的边界情况。
    """
    # 移除 ```json ... ``` 代码块（不要求闭合前必须有换行）
    cleaned = re.sub(r"```json\s*\n.*?```", "", markdown, flags=re.DOTALL)
    # 移除可能残留的独立 ``` 标记
    cleaned = re.sub(r"\n?```\s*\n?", "\n", cleaned)
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


def _rebuild_report(enriched: list[dict], original_markdown: str, trend_data: dict = None) -> str:
    """用增强后的股票数据重建 Markdown 报告。

    新增：优先级排序总览表、行业趋势概览，并在前两部分添加价格/上涨空间/推荐指数列。
    """
    if trend_data is None:
        trend_data = {}
    trend_scores = trend_data.get("scores", {})
    sector_groups = trend_data.get("groups", {})
    sector_logic_map = trend_data.get("logic_map", {})
    # 先移除 JSON 代码块，避免泄露到最终输出
    original_markdown = _strip_json_block(original_markdown)
    parts = []

    # ── 0. 优先级排序总览 ──
    parts.append("## 优先级排序总览（按推荐指数降序）\n")
    parts.append(
        "| 推荐 | 股票名称 | 代码 | 当前股价 | PE | "
        "5日涨跌 | 目标参考 | 上涨空间 | 推荐指数 | 趋势 | 核心逻辑 |"
    )
    parts.append(
        "|------|----------|------|----------|-----|"
        "--------|----------|----------|----------|------|----------|"
    )

    for stock in enriched:
        name = stock["name"]
        code = stock["code"] or "-"
        price_str = f"{stock['current_price']:.2f}" if stock["current_price"] else "N/A"
        pe_str = f"{stock['pe']:.1f}" if stock["pe"] else "-"
        change_5d_str = _fmt_change(stock.get("change_5d"))
        target_str = stock["target_str"] or "-"
        upside_str = f"{stock['upside_pct']:+.1f}%" if stock["upside_pct"] is not None else "N/A"
        logic = stock["logic"][:50] if stock["logic"] else "-"
        # 趋势标记
        trend_badge = _trend_badge(stock)

        parts.append(
            f"| {stock['stars']} | {name} | {code} | {price_str} | {pe_str} | "
            f"{change_5d_str} | {target_str} | {upside_str} | **{stock['score']}** | "
            f"{trend_badge} | {logic} |"
        )

    parts.append("")

    # ── 0.5. 行业趋势概览 ──
    trending = [(s, ts) for s, ts in trend_scores.items() if ts >= 5.0]
    if trending:
        trending.sort(key=lambda x: x[1], reverse=True)
        parts.append("## 🔥 行业趋势概览\n")
        parts.append(
            "| 行业板块 | 趋势强度 | 涉及标的数 | 平均5日涨跌 | 信号解读 |"
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
            avg_chg_str = f"{sum(changes)/len(changes):+.1f}%" if changes else "-"
            logic = sector_logic_map.get(sector_name, "")
            desc = _trend_signal_desc(ts, changes, n)
            stars_trend = "🔥" * min(3, max(1, int(ts / 3.3)))
            parts.append(
                f"| {stars_trend} {sector_name} | **{ts:.1f}** | {n} | "
                f"{avg_chg_str} | {desc} |"
            )
        parts.append("")

    # ── 1. 量化目标（增强） ──
    q_stocks = [s for s in enriched if s["category"] == "quantitative"]
    if q_stocks:
        parts.append("## 一、有明确量化目标的股票（增强）\n")
        parts.append(
            "| 序号 | 股票名称 | 代码 | 当前股价 | PE | "
            "5日涨跌 | 上涨空间 | 投资逻辑 | 量化参考 | 推荐指数 | 趋势 | 来源 |"
        )
        parts.append(
            "|------|----------|------|----------|-----|"
            "--------|----------|----------|----------|----------|------|------|"
        )
        for i, s in enumerate(q_stocks, 1):
            price_str = f"{s['current_price']:.2f}" if s["current_price"] else "N/A"
            pe_str = f"{s['pe']:.1f}" if s["pe"] else "-"
            change_5d_str = _fmt_change(s.get("change_5d"))
            upside_str = f"{s['upside_pct']:+.1f}%" if s["upside_pct"] is not None else "N/A"
            trend_badge = _trend_badge(s)
            parts.append(
                f"| {i} | {s['name']} | {s['code'] or '-'} | {price_str} | {pe_str} | "
                f"{change_5d_str} | {upside_str} | {s['logic'][:60]} | {s['target_str']} | "
                f"**{s['score']}** {s['stars']} | {trend_badge} | {s['source']} |"
            )
        parts.append("")

    # ── 2. 弹性标的（增强） ──
    e_stocks = [s for s in enriched if s["category"] == "elastic"]
    if e_stocks:
        parts.append("## 二、产业趋势中弹性最大的标的（增强）\n")
        parts.append(
            "| 序号 | 股票名称 | 代码 | 当前股价 | PE | "
            "5日涨跌 | 所属赛道 | 核心逻辑 | 推荐指数 | 趋势 | 来源 |"
        )
        parts.append(
            "|------|----------|------|----------|-----|"
            "--------|----------|----------|----------|------|------|"
        )
        for i, s in enumerate(e_stocks, 1):
            price_str = f"{s['current_price']:.2f}" if s["current_price"] else "N/A"
            pe_str = f"{s['pe']:.1f}" if s["pe"] else "-"
            change_5d_str = _fmt_change(s.get("change_5d"))
            trend_badge = _trend_badge(s)
            parts.append(
                f"| {i} | {s['name']} | {s['code'] or '-'} | {price_str} | {pe_str} | "
                f"{change_5d_str} | {s['sector']} | {s['logic'][:60]} | "
                f"**{s['score']}** {s['stars']} | {trend_badge} | {s['source']} |"
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

    return "\n".join(parts)


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
    lines = [
        "# 知识星球股票投资机会提取（增强版）",
        "",
        f"> 分析帖子数: {post_count} 篇",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 数据来源: 腾讯行情 API（实时股价）",
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
    return (
        "# 知识星球股票投资机会提取\n\n"
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "暂无帖子数据，无法提取股票机会。\n"
    )
