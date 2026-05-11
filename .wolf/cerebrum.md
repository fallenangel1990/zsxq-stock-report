# Cerebrum

> OpenWolf's learning memory. Updated automatically as the AI learns from interactions.
> Do not edit manually unless correcting an error.
> Last updated: 2026-05-09

## User Preferences

- **股票机会展示**：抓取知识星球内容后，始终按四类表格展示股票机会：有量化目标的 / 弹性最大的 / 细分板块 / 风险提示。每行含来源帖子引用。
- **回答语言**：尽可能使用中文回答。

## Key Learnings

- **AI 优于正则**：中文财经文本中提取股票名和投资逻辑，AI（DeepSeek/Claude）远优于正则。正则误匹配率高（匹配到随机数字、非股票短语），AI 能理解上下文并区分"投资建议"和"背景提及"。
- **ZSXQ API 限流**：知识星球 API 对请求频率敏感，返回 1059 错误。建议 15s+ 间隔，30s 冷却后重试。
- **Playwright 被屏蔽**：`wx.zsxq.com` 可能返回 `ERR_CONNECTION_CLOSED` 屏蔽 Playwright 自动化浏览器。可通过直接调用 API + Cookie 作为回退方案。
- **富文本清洗**：ZSXQ 的 `talk.text` 含有 `<e type="hashtag" ... />` 等富文本标签，需在 `_parse_topic()` 中清洗后内容才可用。
- **项目:** practise

## Do-Not-Repeat

- [2026-05-11] 不要用正则从中文财经文本中提取股票——匹配结果充满噪声（部分句子被误匹配为股票名，随机数字被当作目标价）。使用 stock_extractor.py 的 AI 方案替代。

## Decision Log

<!-- Significant technical decisions with rationale. Why X was chosen over Y. -->
