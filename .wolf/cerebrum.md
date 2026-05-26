# Cerebrum

> OpenWolf's learning memory. Updated automatically as the AI learns from interactions.
> Do not edit manually unless correcting an error.
> Last updated: 2026-05-09

## User Preferences

- **股票机会展示**：抓取知识星球内容后，始终按四类表格展示股票机会：有量化目标的 / 弹性最大的 / 细分板块 / 风险提示。每行含来源帖子引用。
- **股票机会最终输出字段**：最终报告不要展示股票代码、当前股价、PE、上涨空间、5日涨跌；展示当前市值，突出核心逻辑和目标参考，推荐指数要有细分层级和区分度。
- **股票机会决策辅助**：最终报告应方便快速选股，包含操作标签、买入参考区间/策略、个股风险点和潜在利空。
- **国外投行研报处理**：股票机会提取中，国外投行/外资券商研报涉及的 A 股要特别标注；港股、美股、海外上市公司、ETF、ADR、指数、基金等非 A 股投资推荐应忽略。
- **定时报告增量范围**：定时任务不再限制拉取帖子数，也不再无新增时兜底抓最近 100 篇；每次只处理从上一次拉取记录之后到当前的全部新增帖子。
- **手动报告拉取数量**：手动触发股票报告时可以输入最大拉取帖子数；填 0 或留空表示不限制增量，填 N 表示忽略上次增量位置、抓取最近 N 篇帖子。
- **定时邮件标题**：定时任务成功报告邮件主题使用“新闻资讯M月D日”，例如“新闻资讯5月25日”。
- **回答语言**：尽可能使用中文回答。
- **GitHub 同步**：每次完成本地修改后，默认提交并推送到 GitHub。
- **本地 API Key 安全**：DeepSeek API key 不应以明文留在 config.yaml；配置使用 `api_key_encrypted`，解密密钥放在本机 `.secrets/deepseek.key` 或环境变量 `DEEPSEEK_API_KEY_ENCRYPTION_KEY`。

## Key Learnings

- **AI 优于正则**：中文财经文本中提取股票名和投资逻辑，AI（DeepSeek/Claude）远优于正则。正则误匹配率高（匹配到随机数字、非股票短语），AI 能理解上下文并区分"投资建议"和"背景提及"。
- **ZSXQ API 限流**：知识星球 API 对请求频率敏感，返回 1059 错误。建议 15s+ 间隔，30s 冷却后重试。
- **Playwright 被屏蔽**：`wx.zsxq.com` 可能返回 `ERR_CONNECTION_CLOSED` 屏蔽 Playwright 自动化浏览器。可通过直接调用 API + Cookie 作为回退方案。
- **富文本清洗**：ZSXQ 的 `talk.text` 含有 `<e type="hashtag" ... />` 等富文本标签，需在 `_parse_topic()` 中清洗后内容才可用。
- **股票报告链路**：AI 提取阶段仍需保留股票代码作为行情查询键；最终报告由 `_rebuild_report()` 重建并移除 JSON/代码等不展示字段。
- **GitHub Actions 定时**：A 股开盘日 08:30/12:00 北京时间应写为 UTC `30 0 * * 1-5` / `0 4 * * 1-5`，交易日检查必须使用 `Asia/Shanghai` 日期再用 `chinese_calendar` 排除节假日。
- **GitHub Actions 邮件发送**：邮件凭证已存在但 `smtplib.SMTPServerDisconnected: Connection unexpectedly closed`
  出现在 `server.login()` 时，优先怀疑 SMTP 端口/安全模式或服务商对 CI 出口的限制；
  email_sender.py 支持 `SMTP_SECURITY=auto|ssl|starttls|plain`，默认 465 SSL 失败后回退 587 STARTTLS。
- **项目:** practise

## Do-Not-Repeat

- [2026-05-11] 不要用正则从中文财经文本中提取股票——匹配结果充满噪声（部分句子被误匹配为股票名，随机数字被当作目标价）。使用 stock_extractor.py 的 AI 方案替代。
- [2026-05-15] 校验 GitHub Actions YAML 时不要用 PyYAML `safe_load` 直接取 `on`，它会按 YAML 1.1 把 `on` 当布尔值；用 `yaml.BaseLoader` 或文本校验。
- [2026-05-15] 不要在需要本地/CI 双端校验的脚本里依赖 `grep -P`；macOS grep 不支持 `-P`，日志解析优先用 Python 正则。
- [2026-05-15] 本地 `python3` 可能是 3.9，不要在需要本地验证的模块里使用 PEP 604 `dict | None` 注解；用 `Optional[...]` 更稳。
- [2026-05-21] CI 中 `config.yaml` 被 `.gitignore` 排除，不会从仓库检出；工作流从 `config.example.yaml` 复制时 `ths.enabled: false`。任何涉及 CI 配置变更，都不要依赖本地修改后的 `config.yaml`，必须在工作流步骤中显式覆写。
- [2026-05-25] CI 邮件失败不要只判断为密码错误；如果日志停在 `server.login()` 且报 `SMTPServerDisconnected`，
  需要同时检查 SMTP 安全模式/端口，保留 465 SSL 与 587 STARTTLS 的可配置和 fallback 路径。

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
