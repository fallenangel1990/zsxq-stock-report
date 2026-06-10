# Cerebrum

> OpenWolf's learning memory. Updated automatically as the AI learns from interactions.
> Do not edit manually unless correcting an error.
> Last updated: 2026-05-09

## User Preferences

- **股票机会展示**：抓取知识星球内容后，始终按四类表格展示股票机会：有量化目标的 / 弹性最大的 / 细分板块 / 风险提示。每行含来源帖子引用。
- **股票机会最终输出字段**：最终报告不要展示股票代码、当前股价、PE、上涨空间、5日涨跌；展示当前市值，突出核心逻辑和目标参考，推荐指数要有细分层级和区分度。
- **股票机会决策辅助**：最终报告应方便快速选股，包含操作标签、买入参考区间/策略、个股风险点和潜在利空。
- **股票技术买点**：股票报告需要在给出买卖建议时结合技术指标，并单独输出“最适合买入清单”；该清单按当前买点质量排序，避免把逻辑好但短线过热或趋势未修复的票列为立即买入。
- **股票交易规则分层**：买入建议需分为“立即可买 / 等回踩买 / 只观察”，并展示交易周期、来源可信度、市场环境过滤、卖出/减仓触发条件；短线过热票不能进入立即买入。
- **股票专家决策层**：股票报告顶部应优先输出交易员视角摘要、可执行清单、观察清单；大盘偏弱或过热时不得给出可执行/立即买入，最多进入观察。
- **股票报告篇幅控制**：邮件/Markdown 展示层只保留推荐指数 3 分以上个股；不展示“剔除/暂不买入清单”、决策层级、买点分、来源可信度，避免报告过宽过长。
- **股票推荐复盘**：每次增强评分后的推荐快照要落盘到历史记录，保留推荐价、分数、买点、仓位、风险和市场环境，供后续 3/5/10/20 日收益与回撤复盘。
- **国外投行研报处理**：股票机会提取中，国外投行/外资券商研报涉及的 A 股要特别标注；港股、美股、海外上市公司、ETF、ADR、指数、基金等非 A 股投资推荐应忽略。
- **定时报告增量范围**：定时任务不再限制拉取帖子数，也不再无新增时兜底抓最近 100 篇；每次只处理从上一次拉取记录之后到当前的全部新增帖子。
- **手动报告拉取数量**：手动触发股票报告时可以输入最大拉取帖子数；填 0 或留空表示按增量模式最多抓取 300 条并记录上次位置，填 N 表示忽略上次增量位置、抓取最近 N 篇帖子。
- **定时邮件标题**：定时任务成功报告邮件主题使用“新闻资讯M月D日”，例如“新闻资讯5月25日”。
- **定时邮件 UI**：邮件正文需要宽松易扫读，表格不能过于拥挤；买点、推荐、风险、止损、减仓、卖出等重点内容应在邮件中标红突出。
- **盘后复盘任务**：用户需要独立的 A 股盘后复盘报告，覆盖大盘情绪、板块题材、资金流向、个股复盘、主线策略、仓位管理、新闻信息、明日计划和心理纪律；当前龙虎榜、北向资金、真实持仓和新闻公告应明确标为待接入，不编造。
- **回答语言**：尽可能使用中文回答。
- **GitHub 同步**：每次完成本地修改后，默认提交并推送到 GitHub。
- **本地 API Key 安全**：DeepSeek API key 不应以明文留在 config.yaml；配置使用 `api_key_encrypted`，解密密钥放在本机 `.secrets/deepseek.key` 或环境变量 `DEEPSEEK_API_KEY_ENCRYPTION_KEY`。

## Key Learnings

- **AI 优于正则**：中文财经文本中提取股票名和投资逻辑，AI（DeepSeek/Claude）远优于正则。正则误匹配率高（匹配到随机数字、非股票短语），AI 能理解上下文并区分"投资建议"和"背景提及"。
- **ZSXQ API 限流**：知识星球 API 对请求频率敏感，返回 1059 错误。建议 15s+ 间隔，30s 冷却后重试。
- **Playwright 被屏蔽**：`wx.zsxq.com` 可能返回 `ERR_CONNECTION_CLOSED` 屏蔽 Playwright 自动化浏览器。可通过直接调用 API + Cookie 作为回退方案。
- **富文本清洗**：ZSXQ 的 `talk.text` 含有 `<e type="hashtag" ... />` 等富文本标签，需在 `_parse_topic()` 中清洗后内容才可用。
- **股票报告链路**：AI 提取阶段仍需保留股票代码作为行情查询键；最终报告由 `_rebuild_report()` 重建并移除 JSON/代码等不展示字段。
- **股票行情兜底**：腾讯行情 `qt.gtimg.cn` 的总市值字段可能为空；当前市值必须用东方财富 push2 `f20` 兜底并转为亿元，避免最终报告市值显示为 `-`。
- **GitHub Actions 定时**：A 股开盘日 08:30/12:00 北京时间应写为 UTC `30 0 * * 1-5` / `0 4 * * 1-5`，交易日检查必须使用 `Asia/Shanghai` 日期再用 `chinese_calendar` 排除节假日。
- **GitHub Actions 邮件发送**：邮件凭证已存在但 `smtplib.SMTPServerDisconnected: Connection unexpectedly closed`
  出现在 `server.login()` 时，优先怀疑 SMTP 端口/安全模式或服务商对 CI 出口的限制；
  email_sender.py 支持 `SMTP_SECURITY=auto|ssl|starttls|plain`，默认 465 SSL 失败后回退 587 STARTTLS。
- **项目:** practise

## Do-Not-Repeat

- [2026-05-11] 不要用正则从中文财经文本中提取股票——匹配结果充满噪声（部分句子被误匹配为股票名，随机数字被当作目标价）。使用 stock_extractor.py 的 AI 方案替代。
- [2026-05-15] 校验 GitHub Actions YAML 时不要用 PyYAML `safe_load` 直接取 `on`，它会按 YAML 1.1 把 `on` 当布尔值；用 `yaml.BaseLoader` 或文本校验。
- [2026-06-03] GitHub Actions workflow 顶层事件键写成 `"on":`，不要用裸 `on:`；裸键可能在 YAML 1.1 解析链路中变成布尔值并触发 schedule schema 报错。
- [2026-06-03] 禁用 GitHub Actions 定时任务时不要写 `schedule: []`；GitHub 要求 schedule 至少包含一个 `cron` map，应直接移除 `schedule` 键，只保留 `workflow_dispatch`。
- [2026-05-15] 不要在需要本地/CI 双端校验的脚本里依赖 `grep -P`；macOS grep 不支持 `-P`，日志解析优先用 Python 正则。
- [2026-05-15] 本地 `python3` 可能是 3.9，不要在需要本地验证的模块里使用 PEP 604 `dict | None` 注解；用 `Optional[...]` 更稳。
- [2026-05-21] CI 中 `config.yaml` 被 `.gitignore` 排除，不会从仓库检出；工作流从 `config.example.yaml` 复制时 `ths.enabled: false`。任何涉及 CI 配置变更，都不要依赖本地修改后的 `config.yaml`，必须在工作流步骤中显式覆写。
- [2026-05-25] CI 邮件失败不要只判断为密码错误；如果日志停在 `server.login()` 且报 `SMTPServerDisconnected`，
  需要同时检查 SMTP 安全模式/端口，保留 465 SSL 与 587 STARTTLS 的可配置和 fallback 路径。

- **GitHub Actions cron 直接使用 UTC 时间**（已作废，见 2026-05-28 决策）：此前认为不要加"延迟补偿"；用户 2026-05-28 明确反馈 Actions 推送比目标北京时间晚 4 小时，当前工作流需提前 4 小时配置。

## Do-Not-Repeat

- [2026-05-18] 已作废：不要给 GitHub Actions cron 加任何"延迟补偿"偏移。2026-05-28 用户确认 Actions 推送晚 4 小时，需按实测提前 4 小时配置。
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
- **同花顺 cookies 域名**：从浏览器导出的列表式 cookies 可能绑定到 `i.10jqka.com.cn` 等子域；
  自定义分组接口在 `ugc.10jqka.com.cn`，加载 cookies 时需补写 `.10jqka.com.cn` 父域，否则定时任务可能能查默认自选但无法创建分组。
- **同花顺降级同步**：`ths.also_add_to_watchlist: true` 时，分组查询/创建失败不应阻断默认自选股添加；
  应降级继续写默认自选股，并在同步结果中输出分组失败 warning。
- **CI 同花顺兜底执行**：日报 workflow 不能只依赖 `main.py all` 末尾的自动同步。
  Actions 应在爬取/提取后检查日志；若未出现“同花顺同步结果”且 `cookies_ths.json` 存在，需要显式运行 `python main.py thssync --strict`，让同步失败在 CI 中红掉。
- **小米 Mimo API Key**：当 `ai.deepseek.base_url` 指向 `api.xiaomimimo.com` 且模型为 `mimo-v2.5` 时，CI/本地应设置 `MIMO_API_KEY` 或 `XIAOMI_MIMO_API_KEY`，不要复用 `DEEPSEEK_API_KEY`；本地加密 key 使用 `MIMO_API_KEY_ENCRYPTION_KEY` 或 `.secrets/mimo.key`。
- **增量状态提交时机**：`main.py all` 不能在爬取 raw 后立刻更新 `data/state`；必须等股票报告和总结报告都成功后再保存上次位置。否则 AI 失败会导致下一次触发误判“无新内容”。
- **股票候选来源**：同花顺同步依赖 `stock_extractor.py` 生成的 enriched 股票数据；如果 AI 只把股票放在 `sectors.stocks`，也必须拆成弹性候选参与评分，否则最终快速选股表和同花顺同步都会为空。
- **ZSXQ 1059 分页处理**：分页中途遇到 1059 不能当作“没有更多数据”静默结束；应按限流/会话异常冷却重试，重试失败要阻止半截数据推进增量状态。
- **AI JSON 字段类型漂移**：Mimo 等模型可能把 schema 中声明为字符串的字段返回为 list/dict；处理 AI JSON 前要做类型归一，尤其是 `sectors[].stocks` 这类“列表语义”的字段。
- **CI Cookie 认证失败处理**：GitHub Actions 中 ZSXQ HTTP/API 401/403 必须硬失败并提示更新 `ZSXQ_COOKIES`；不要回退 Playwright，也不要把 0 篇结果当作“无新增”或用旧报告发成功邮件。
- **Cookie expires 元数据不可靠**：浏览器导出的 ZSXQ cookie `expires` 可能不准或与服务端会话状态不同；CI 只应要求存在 `zsxq_access_token`，真正有效性以 API 401/403 为准。
- **ZSXQ Cookie 自动刷新边界**：GitHub Actions 不能无交互扫码登录，也不能凭默认权限自动改仓库 Secret；Cookie 即将过期时只能本地扫码刷新，或通过 `ZSXQ_COOKIES_REFRESH_URL` 私有端点返回新 Cookie 供本次 CI 使用。
- **报告展示时区**：所有会出现在邮件正文、邮件头、PDF 页脚或定时报告中的生成时间，都必须使用 `ZoneInfo("Asia/Shanghai")`；不要在 CI 可见输出中使用 naive `datetime.now()` 后再手动标注北京时间。
- **盘后复盘行情源降级**：东方财富 `push2.eastmoney.com` 在 GitHub Actions 中可能连续返回 502；盘后复盘不能把主要指数、全A快照或板块快照作为硬依赖，应返回空/部分样本、尝试腾讯兜底，并在报告中标记数据完整性。

## Decision Log

- [2026-05-19] **同花顺同步实现选择**：双 API 并存 — t.10jqka.com.cn 写默认自选；
  ugc.10jqka.com.cn（group/v1/query + content/v1/add）写自定义分组，与手机端同步。
  config.yaml 的 `ths.group_name` 指定目标分组，`score_threshold: 3.0` 过滤评分。
- [2026-05-28] **GitHub Actions 定时补偿**：用户确认 Actions 推送比目标北京时间晚 4 小时。
  工作流按目标北京时间提前 4 小时配置：08:30 目标使用 UTC `30 20 * * 0-4`，12:00 目标使用 UTC `0 0 * * 1-5`。
- [2026-05-28] **取消股票信息聚合看板定时**：用户要求取消 `stock-dashboard.yml` 的定时推送；
  已移除盘中每 15 分钟和 16:10 盘后 schedule，仅保留手动 `workflow_dispatch`。
- [2026-05-29] **日报定时延后 2.5 小时**：用户要求将定时任务延后 2.5 小时。
  在保留 GitHub Actions 晚 4 小时补偿前提下，日报目标北京时间从 08:30/12:00 调整为 11:00/14:30，对应 cron 为 `0 23 * * 0-4` / `30 2 * * 1-5`。
- [2026-06-03] **增量抓取上限**：用户要求增量抓取上限为 300 条，并记录上次抓取位置。
  `max_posts=0` 现在表示增量模式最多 300 条；显式传入 N 仍表示手动抓最近 N 条并忽略上次位置。
- [2026-06-03] **小米 Mimo 密钥读取**：修复 Mimo 配置误读 `DEEPSEEK_API_KEY` 导致 401。
  `summarizer.py` 现在根据 `base_url` 识别 Mimo，并优先读取 `MIMO_API_KEY` / `XIAOMI_MIMO_API_KEY`。
- [2026-06-04] **增量状态延后提交**：修复触发后无股票结果输出。
  `cmd_all()` 现在在股票报告和总结都成功后才更新 crawl state；若存在未生成股票报告的最新 raw，会在无新增时恢复处理。

<!-- Significant technical decisions with rationale. Why X was chosen over Y. -->
