# Cerebrum

> OpenWolf's learning memory. Updated automatically as the AI learns from interactions.
> Do not edit manually unless correcting an error.
> Last updated: 2026-05-09

## User Preferences

- **股票机会展示**：抓取知识星球内容后，始终按四类表格展示股票机会：有量化目标的 / 弹性最大的 / 细分板块 / 风险提示。每行含来源帖子引用。
- **股票机会最终输出字段**：最终报告不要展示股票代码、当前股价、PE、上涨空间、5日涨跌；展示当前市值，突出核心逻辑和目标参考，推荐指数要有细分层级和区分度。
- **股票机会决策辅助**：最终报告应方便快速选股，包含操作标签、买入参考区间/策略、个股风险点和潜在利空。
- **定时报告内容范围**：定时任务有新增内容时只总结新增帖子数；没有新增内容时抓取最近 100 条帖子生成总结和股票机会报告。
- **回答语言**：尽可能使用中文回答。

## Key Learnings

- **AI 优于正则**：中文财经文本中提取股票名和投资逻辑，AI（DeepSeek/Claude）远优于正则。正则误匹配率高（匹配到随机数字、非股票短语），AI 能理解上下文并区分"投资建议"和"背景提及"。
- **ZSXQ API 限流**：知识星球 API 对请求频率敏感，返回 1059 错误。建议 15s+ 间隔，30s 冷却后重试。
- **Playwright 被屏蔽**：`wx.zsxq.com` 可能返回 `ERR_CONNECTION_CLOSED` 屏蔽 Playwright 自动化浏览器。可通过直接调用 API + Cookie 作为回退方案。
- **富文本清洗**：ZSXQ 的 `talk.text` 含有 `<e type="hashtag" ... />` 等富文本标签，需在 `_parse_topic()` 中清洗后内容才可用。
- **股票报告链路**：AI 提取阶段仍需保留股票代码作为行情查询键；最终报告由 `_rebuild_report()` 重建并移除 JSON/代码等不展示字段。
- **GitHub Actions 定时**：A 股开盘日 08:30/12:00 北京时间应写为 UTC `30 0 * * 1-5` / `0 4 * * 1-5`，交易日检查必须使用 `Asia/Shanghai` 日期再用 `chinese_calendar` 排除节假日。
- **项目:** practise

## Do-Not-Repeat

- [2026-05-11] 不要用正则从中文财经文本中提取股票——匹配结果充满噪声（部分句子被误匹配为股票名，随机数字被当作目标价）。使用 stock_extractor.py 的 AI 方案替代。
- [2026-05-15] 校验 GitHub Actions YAML 时不要用 PyYAML `safe_load` 直接取 `on`，它会按 YAML 1.1 把 `on` 当布尔值；用 `yaml.BaseLoader` 或文本校验。
- [2026-05-15] 不要在需要本地/CI 双端校验的脚本里依赖 `grep -P`；macOS grep 不支持 `-P`，日志解析优先用 Python 正则。
- [2026-05-15] 本地 `python3` 可能是 3.9，不要在需要本地验证的模块里使用 PEP 604 `dict | None` 注解；用 `Optional[...]` 更稳。

## Decision Log

<!-- Significant technical decisions with rationale. Why X was chosen over Y. -->
