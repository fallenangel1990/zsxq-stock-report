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

- **GitHub Actions cron 直接使用 UTC 时间**：不要加"延迟补偿"。GitHub Actions 按 cron 表达式在 UTC 时间触发，不存在系统性 +7h 延迟。北京时间 = UTC + 8h，直接换算即可。之前的 +7h 补偿反而造成 4h 偏差。

## Do-Not-Repeat

- [2026-05-18] 不要给 GitHub Actions cron 加任何"延迟补偿"偏移。北京时间 8:30 = UTC 0:30 (cron `30 0 * * 1-5`)，北京时间 12:00 = UTC 4:00 (cron `0 4 * * 1-5`)。之前的 +7h 补偿导致实际执行偏离目标 4 小时。
- [2026-05-16] 不要假设 GitHub Actions cron 延迟固定为 +4h。实测约 +7h，且不同时段可能不同。新增定时任务时应验证实际触发时间。
- [2026-05-11] 不要用正则从中文财经文本中提取股票——匹配结果充满噪声（部分句子被误匹配为股票名，随机数字被当作目标价）。使用 stock_extractor.py 的 AI 方案替代。

## Key Learnings

- **同花顺分组同步**：同花顺（i.10jqka.com.cn）自选股管理没有公开 API，使用 cookie 式 HTTP 调用
  userSelfStockOper 接口（type=3 创建分组, type=1 添加股票）。端点可通过 config.yaml 配置。
- **增强数据持久化**：stock_extractor.py 计算推荐指数后会自动保存 enriched 数据到
  data/summary/*_enriched_*.json，供 ths_sync.py 等下游模块使用。这是通过 storage.py 的
  save_enriched_stocks() 实现的。
- **配置式自动化**：同花顺同步通过 config.yaml 的 ths.enabled 控制开关，默认关闭。
  启用后会在 stocks 和 all 流程末尾自动执行，不阻塞主流程。

## Decision Log

- [2026-05-19] **同花顺同步实现选择**：双 API 并存 — t.10jqka.com.cn 写默认自选；
  ugc.10jqka.com.cn（group/v1/query + content/v1/add）写自定义分组，与手机端同步。
  config.yaml 的 `ths.group_name` 指定目标分组，`score_threshold: 3.0` 过滤评分。

<!-- Significant technical decisions with rationale. Why X was chosen over Y. -->
