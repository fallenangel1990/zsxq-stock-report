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
- **股票报告篇幅控制**：邮件/Markdown 展示层只保留推荐指数 3 分以上个股；不展示”剔除/暂不买入清单”、决策层级、买点分、来源可信度，避免报告过宽过长。
- **股票趋势精选**：报告顶部应输出”趋势精选清单”，仅展示处于上升趋势、所属板块整体上涨、得分5分以上、且当前股价在5日均线附近（±3%）的股票。
- **股票推荐复盘**：每次增强评分后的推荐快照要落盘到历史记录，保留推荐价、分数、买点、仓位、风险和市场环境，供后续 3/5/10/20 日收益与回撤复盘。
- **国外投行研报处理**：股票机会提取中，国外投行/外资券商研报涉及的 A 股要特别标注；港股、美股、海外上市公司、ETF、ADR、指数、基金等非 A 股投资推荐应忽略。
- **定时报告增量范围**：定时任务不再限制拉取帖子数，也不再无新增时兜底抓最近 100 篇；每次只处理从上一次拉取记录之后到当前的全部新增帖子。
- **手动报告拉取数量**：手动触发股票报告时可以输入最大拉取帖子数；填 0 或留空表示按增量模式最多抓取 300 条并记录上次位置，填 N 表示忽略上次增量位置、抓取最近 N 篇帖子。
- **定时邮件标题**：定时任务成功报告邮件主题使用“新闻资讯M月D日”，例如“新闻资讯5月25日”。
- **定时邮件 UI**：邮件正文需要宽松易扫读，表格不能过于拥挤；买点、推荐、风险、止损、减仓、卖出等重点内容应在邮件中标红突出。
- **盘后复盘任务**：用户需要独立的 A 股盘后复盘报告，覆盖大盘情绪、板块题材、龙虎榜资金、个股复盘、主线策略、仓位管理、新闻信息、明日计划和心理纪律；不要展示外资资金占位；市场上涨/下跌家数优先使用同花顺 indexflash；涨跌停数量使用真实涨跌停池；板块统计要完整展示涨跌、成交额、主力净流入、强弱分布，并单独统计强势行业和强势题材；真实持仓和新闻公告应明确标为待接入，不编造。
- **盘前快讯格式**：盘前财经快讯不要使用 Markdown，应输出 HTML 格式（可直接作为邮件正文的完整 HTML 片段，含内联样式）。LLM 不一定严格遵循 prompt 输出 HTML，因此 `_normalize_to_html()` 会检测格式：Markdown → 用 markdown 库转 HTML；裸 HTML → 直接通过。最终统一走 `_style_inline_html()` 加内联样式。
- **邮件发送职责唯一**：`main.py all` 不再自行发送邮件；邮件统一由 GitHub Actions workflow (`daily-report.yml`) 的 "发送成功报告邮件" 步骤发送，避免重复邮件。
- **盘中预警收盘退出**：`intraday_monitor.py` 在 15:00 后自动退出（break），不再无限休眠直到 workflow 超时；workflow `timeout-minutes` 设为 480 作为兜底。
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
- **自适应权重**：stock_extractor.py 的评分权重已支持三级优先级：自适应权重（IC驱动）> 市场状态权重 > 静态配置。自适应权重通过 adaptive_weights.py 的 IC 历史自动更新。
- **趋势精选打分制**：_filter_trending_near_ma5() 已从刚性四条件门槛改为打分制（满分100），总分≥55即可通过。各条件权重：得分40+趋势25+板块20+均线15+附加分10。避免了"四条件同时满足"导致的无票问题。
- **AI 置信度**：股票提取 prompt 现在要求 AI 对每只股票输出 1-5 的置信度分数，映射到 0.2-1.0 的权重，低置信度股票得分打折。
- **止盈策略**：_exit_trigger() 现在包含5层止盈逻辑：风险触发→RSI+MACD联动止盈→目标价止盈→过热止盈→均线止损。
- **波动率regime**：market_regime.py 新增 detect_volatility_regime()（基于ATR%）和 detect_credit_spread_signal()（基于国债/信用债ETF相对强弱），信号已整合到市场状态评分。
- **Kelly公式仓位**：portfolio_builder.py 新增 allocate_kelly() 和 allocate_risk_parity()，select_allocation_method() 自动选择最优分配方法。

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
- **同花顺写后确认**：同花顺接口可能返回添加成功但刷新后目标分组/默认自选里没有股票；同步必须写后刷新确认并重试，仍未确认时返回 `partial`，CI 严格兜底必须失败而不是静默通过。
- **同花顺定时同步阈值和分组**：日报定时任务只将推荐指数 >= 5.0 的股票同步到同花顺；自动分组名按北京时间只使用当天日期（如 `06-17`），不要带“知识星球”前缀。
- **CI 同花顺兜底执行**：日报 workflow 不能只依赖 `main.py all` 末尾的自动同步。
  Actions 应在爬取/提取后检查日志；若未出现“同花顺同步结果”或状态不是 `success`，且 `cookies_ths.json` 存在，需要显式运行 `python main.py thssync --strict`，让同步失败在 CI 中红掉。
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
- **盘后复盘私有数据接入**：真实持仓和交易心理日志不提交到仓库；本地分别读取 `data/holdings.json`、`data/trading_journal.json`，CI 通过 `PORTFOLIO_JSON`、`TRADING_JOURNAL_JSON` Secrets 写入运行时文件。报告应展示明确数据状态，不再输出“待接入”占位。
- **盘后复盘市场风格**：市场风格不能显示“未知”；板块数据不可用时应根据涨停行业集中、创业板/科创/沪深300/上证相对强弱和上涨下跌家数兜底判断，并同时给出判断依据。
- **盘后复盘龙虎榜 UI**：龙虎榜章节要先给资金结论和热点方向，再给买入焦点/卖出风险 Top3；避免展示买入、卖出、净额、原因等多列宽表导致邮件拥挤。
- **盘后复盘可读性**：复盘报告应优先给交易结论、驱动因素和影响方向；不要展示平均换手率这类低价值字段。板块数据缺失时必须用涨停池/连板池补全题材方向；新闻区要展示收盘后到当前的重要快讯。
- **盘后复盘展示格式**：盘后复盘不要以 Markdown 形式展示给用户；保存和邮件都应优先使用 HTML 卡片/表格。主要行情源缺失时必须用同花顺、东方财富、腾讯行情、涨跌停池/连板池等多渠道补全，不要输出“未知”。
- **每日股票报告候选池**：AI 阶段应负责“候选提取”而不是最终推荐，满足 A 股公司名/代码 + 投资逻辑即可进入候选；最终报告需同时展示可评分候选、3 分以上正式推荐和最终展示数量。正式推荐过少时可补充 2 分以上观察候选，但必须明确“不等同于立即买入”。
- **知识星球附件解析**：ZSXQ 帖子里的 `talk.files` 不能混入 `images`；PDF/音频应保留为 `files` 并通过 attachment_processor.py 转成 `attachment_text`，再由 extractor.py 追加到正文供总结和股票提取使用。PDF 用 pypdf 本地抽取；MP3/M4A/WAV 默认使用小米 MiMo `mimo-v2.5-asr`（`MIMO_API_KEY` / `XIAOMI_MIMO_API_KEY`）转写，OpenAI Whisper 仅作 fallback，缺密钥时跳过但不阻断日报。

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
- [2026-06-12] **日报股票候选展示**：修复 300 篇帖子只展示 2 只股票的问题。
  AI 提取改为候选池覆盖，报告层保留 3 分正式推荐阈值，同时在推荐过少时补充 2 分以上观察候选并输出过滤诊断。
- [2026-06-13] **附件解析策略**：为知识星球 PDF/MP3 附件接入解析。
  附件文本作为帖子正文补充进入股票候选提取；PDF 本地解析，音频在配置转写密钥时转写，所有附件失败均降级为日志提示。
- [2026-06-13] **音频转写 Provider**：MP3 解析默认切换为小米 MiMo ASR。
  `attachments.audio_provider` 默认 `mimo`，模型 `mimo-v2.5-asr`，base URL `https://api.xiaomimimo.com/v1`；OpenAI Whisper 只作为 fallback。
- [2026-06-17] **同花顺同步规则**：日报定时任务同花顺同步改为 >=5 分，并使用纯日期分组名。
  CI 覆写配置中 `ths.score_threshold=5.0`、`group_name_prefix=""`、`group_name="auto"`；`make_daily_group_name("")` 返回 `MM-DD`。
- [2026-06-17] **同花顺分组日期时区**：纯日期分组名必须以北京时间为准。
  `make_daily_group_name()` 使用 `ZoneInfo("Asia/Shanghai")`，避免 GitHub Actions UTC runner 在凌晨生成前一天日期。
- [2026-06-24] **趋势精选过滤**：用户要求股票报告优先推荐处于上升趋势、所属板块整体上涨的股票，仅保留得分 5 分以上且当前股价在 5 日均线附近的标的。
  报告新增"趋势精选清单"章节，过滤条件：得分≥5 + 均线多头或站上5日线 + 板块趋势分≥5 + 距5日线偏离≤3%。
  `price_fetcher.py` 新增 `distance_ma5_pct` 字段；`stock_extractor.py` 新增 `_filter_trending_near_ma5()` 过滤函数。

<!-- Significant technical decisions with rationale. Why X was chosen over Y. -->

## Key Learnings (2026-07-01)

- **共识度独立作者计数**：`stock_extractor.py` 的共识评分改为按独立作者数计分（`authors` set），而非帖子数。AI prompt 新增 `author` 字段输出，`_enrich_and_score` 中维护 `authors` 集合，`_calibrate_recommendation_score` 中 `unique_authors` 权重高于 `post_count`。
- **拥挤度惩罚**：新增 `_apply_crowding_penalty()` 函数，同板块内排名第 3 及以后的标的按位置递减扣分（-0.3, -0.6, -0.9...），避免报告被同一板块堆叠。
- **量价背离检测**：`_technical_buy_score` 新增量价背离逻辑：缩量上涨（change_5d>5 且 volume_ratio<0.8）扣 1.0 分，放量下跌扣 0.5 分，放量确认加 0.4 分。
- **行业敞口双限制**：`_apply_portfolio_constraints` 增强为两步——数量限制（max_per_sector）+ 仓位占比限制（portfolio_builder.apply_sector_cap, max_sector_pct=0.25）。
- **政策事件+流动性状态**：`market_regime.py` 新增 `detect_policy_event()` 和 `detect_liquidity_regime()`，基于成交额判断增量/存量/缩量环境，动态调整趋势和基本面因子权重。
- **因子 IC 稳定性检验**：`adaptive_weights.py` 新增 `check_ic_stability()` 和 `check_all_factors_stability()`，计算 IC 自相关性和标准差，对不稳定因子按可靠性打折。`format_weights_report` 输出稳定性评估表。
- **权重配置调整**：`config.yaml` 中 `upside_weight` 从 0.30 降至 0.20，`quality_weight` 从 0.20 升至 0.22，`consensus_weight` 从 0.16 升至 0.18，`trend_weight` 从 0.10 升至 0.12，`fundamentals_weight` 从 0.10 升至 0.14。

## Decision Log (2026-07-01)

- [2026-07-01] **选股策略六项增强**：按优先级依次实现——共识度独立作者计数、upside_weight 下调+拥挤度惩罚、量价背离检测、行业敞口上限、政策事件+流动性状态、因子 IC 稳定性检验。所有改动涉及 stock_extractor.py、config.yaml、market_regime.py、adaptive_weights.py、portfolio_builder.py。

## Key Learnings (2026-07-01 续：量化基金视角增强)

- **缺失函数 Bug**：发现 `_sentiment_score`、`_fundamentals_score`、`_volume_confirm_score` 三个函数被调用但从未定义，导致运行时崩溃。已补全。
- **IC Look-ahead Bias**：`calculate_factor_ic` 原来用当前价格计算推荐以来收益，改为优先使用固定持有期（T+5/T+20）前向收益。新增 `_compute_forward_return()` 函数。
- **聪明钱信号接入**：新增 `_smart_money_adjustment()` 综合北向资金、融资融券、个股主力净流入三个数据源。`fetch_northbound_flow` 和 `fetch_money_flow` 数据已在 `price_fetcher` 中实现但未接入评分。
- **风控熔断机制**：`paper_trader.py` 新增 `check_circuit_breakers()`：单日亏损>2%暂停开仓、个股亏损>8%止损、组合回撤>10%减半、>15%清仓。
- **交易成本模型**：`calculate_transaction_cost` 新增市场冲击成本（平方根模型），考虑参与率和市值对冲击的影响。
- **因子正交化**：`_apply_factor_orthogonalization()` 检测高相关因子对（sector-trend, volume-capital, upside-logic），对后排因子降权。
- **风格暴露监控**：`_calculate_style_exposure()` 计算候选池在动量/价值/成长/波动/规模维度的暴露，输出到报告。

## Decision Log (2026-07-01 续)

- [2026-07-01] **量化基金视角九项增强**：P0补全缺失函数+修复IC bias → P1接入聪明钱信号+排序改buy_score → P2截面归一化+交易成本+风控熔断 → P3因子正交化+风格暴露。
  涉及文件：stock_extractor.py, backtester.py, paper_trader.py, market_regime.py, portfolio_builder.py, adaptive_weights.py, config.yaml。
- [2026-07-01] **排序逻辑变更**：报告主排序从 `score` 改为 `(buy_score, score)` 降序，确保逻辑好但买点差的票排后。
- [2026-07-01] **交易成本模型升级**：买入成本 = max(5, 金额 × 0.025%) + 滑点(0.1%) + 市场冲击(σ × √参与率 × 0.5)。

## Key Learnings (2026-07-01 续2：AI Berkshire 框架融合)

- **AI Berkshire 框架核心**：四大师视角对抗（巴菲特财务估值/段永平生意本质/芒格逆向思考/李录文明趋势）+ 镜子测试（5句话说不完整=不买）+ 快速否决清单（8条红线）+ 三情景估值
- **三情景估值格式**：激进/稳健/保守三个价格带（🔴保守/🟡稳健/🟢激进），便于不同风险偏好投资者快速决策
- **护城河分类体系**：品牌定价权/转换成本/网络效应/规模效应/技术壁垒/渠道壁垒/无明显护城河
- **镜子测试实现**：逻辑文本长度>50字视为"逻辑清晰"，否则标注"阐述不足"
- **快速否决信号**：诚信瑕疵/大股东减持/护城河薄弱但评分高/高估值需深度验证
- **AI研究置信度声明**：信息丰富度受限于帖子覆盖度，投资确定性取决于生意本质

## Decision Log (2026-07-01 续2)

- [2026-07-01] **融合 ai-berkshire.git 投资逻辑**：从 xbtlin/ai-berkshire 借鉴四大师视角、镜子测试、三情景估值、快速否决清单。
  新增功能：_parse_moat_score / _format_three_scenario_targets / _smart_money_adjustment / _normalize_factors_cross_section / _apply_factor_orthogonalization / _calculate_style_exposure / _append_mirror_test / _append_quick_reject / _fundamentals_score / _volume_confirm_score / _sentiment_score。
  报告新增：护城河类型+评分、三情景目标价表格、镜子测试&反向思考、快速否决清单、风格暴露+聪明钱信号。
  AI prompt 新增：生意本质提取、护城河判断、管理层评估、三情景目标。

## Decision Log (2026-07-01 续3)

- [2026-07-01] **日报定时任务时间调整**：用户要求将定时任务改为 A 股交易日北京时间 08:50。
  cron 从原来的 `0 23 * * 0-4` / `30 2 * * 1-5`（目标 11:00/14:30）统一改为 `50 0 * * 1-5`（目标 08:50）。
  GitHub Actions 时间 = UTC 00:50（周一到周五），对应北京时间 08:50。
  法定节假日由 workflow 内部的 chinese_calendar 检查跳过。

## Decision Log (2026-07-01 续4)

- [2026-07-01] **大模型切换到 LongCat 2.0**：用户要求将所有 workflow 中的大模型换成 LongCat 2.0。
  - API: https://api.longcat.chat/openai (OpenAI 兼容格式)
  - Model: LongCat-2.0
  - API Key: ak_2ze1Hg2LZ3KP0yx6gN5ah4Ew2eI94
  - 认证方式: Bearer <REDACTED> (标准 OpenAI Authorization header)
  - GitHub Actions Secret: LONGCAT_API_KEY（需手动添加到仓库 Secrets）
  - 变更文件: config.yaml, summarizer.py, daily-report.yml, consecutive-limit-up.yml

## Key Learnings (2026-07-17)

- **AI API timeout 配置**：OpenAI Python 客户端默认 timeout 为 600s，LongCat/DeepSeek 等 API 在大批量请求时可能超时。需显式配置 `timeout=180.0` 和 `max_retries=2`，避免单次 API 调用长时间阻塞。
- **AI 返回 content 可能为 None**：LongCat-2.0 / DeepSeek 等 OpenAI 兼容 API 在内容被过滤或模型仅输出推理时，`response.choices[0].message.content` 返回 `None`（而非抛出异常）。重试循环只捕获异常无法拦截 None，需在 wrapper 层显式检查并 raise 才能触发重试；同时消费端（如 `_build_report`）应做 None 兜底实现防御深度。
- **AI 总结串行+重试**：300 篇帖子分 15 批总结时，并发调用容易触发限流或单批超时导致整体取消。改为串行 + 重试（失败等 5/10s 后重试 2 次）更稳定；失败批次跳过不阻断，报告中注明跳过。
- **GitHub Actions timeout**：涉及多轮 AI 调用的任务（300 篇总结 + 股票提取），30 分钟 timeout 可能偏紧；预留 45 分钟更安全。
- **GitHub Actions cancel-in-progress**：`concurrency.cancel-in-progress: true` 会在新触发时取消正在运行的 run，而不是排队等待。对于耗时 30-45 分钟的长任务（如日报 AI 总结），这会导致运行中途被取消、浪费已完成的 AI 调用。长任务应设为 `cancel-in-progress: false`，让新触发排队等待。

## Key Learnings (2026-07-29)

- **市场资金集中度指标**：新增 `concentration_monitor.py` 模块，三信号（板块净流入占比 / 成交额占比 / 宽度背离）综合判定资金拥挤度。复用 `sector_monitor.fetch_boards()` 和 `fetch_market_indices()` 获取数据，不重复实现 eastmoney API。
- **change_pct 是百分比值**：东方财富 push2 的 `f3` 字段（change_pct）是百分比值（0.8 = 0.8%），显示时直接 `{value:.1f}%` 不要再乘 100。这与 sector_monitor.py 的显示惯例一致。
- **状态去重的元数据保存**：盘中预警的 daily dedup 需要把 `last_push_level`/`last_push_date` 存在 state 中。更新 state 时必须用 merge 模式（`{**old, "level": new}`）而非整体覆盖，否则同等级轮次会把元数据擦除，导致跨日波动时重复推送。
- **降级路径不记录推送等级**：集中度等级下降（danger→elevated）的释放路径不应记录 `last_push_level`，否则会导致升级判断基准错误，产生振荡推送。
- **config.yaml 不应提交**：config.yaml 在 .gitignore 中（含 API keys / group_url）。实现配置新功能时只提交 config.example.yaml，本地 config.yaml 手动编辑即可。

## Decision Log (2026-07-29)

- [2026-07-29] **集中度模块架构选择**：独立 `concentration_monitor.py` 模块而非嵌入 market_regime.py。理由：报告和盘中预警两个消费方需要共享计算逻辑；集中度是风险叠加信号而非市场状态，职责不同；符合项目模块化风格。
- [2026-08-03] **大模型切换到 DeepSeek-V4-Flash**：用户要求将 `ai.provider` 从 longcat 切换为 `deepseek-v4-flash`。`summarizer.py` 的 `_init_deepseek_v4_flash()` 早已就绪，只缺 provider 翻转和 key 配置。所有 workflow 已传 `DEEPSEEK_V4_FLASH_API_KEY` secret。涉及文件：config.yaml、config.example.yaml、summarizer.py。

## Key Learnings (2026-08-04)

- **空 JSON 块会遮蔽 Markdown 表格**：`_parse_stock_json` 之前命中任何可解析 JSON 块（即使全空 `{"quantitative": [], ...}`）就直接返回，导致 AI 输出完整表格 + 空 JSON 时候选池为 0。现在增加 `_json_has_content()`（stock_extractor.py:461）：只有 JSON 含有效条目才采用，空 JSON 继续回退到表格解析。
- **Markdown 表格行正则要容忍空格**：`_fallback_parse_tables` 的行正则原来是 `^\|(\d+)\|(.+)\|$`，只能匹配 `|1|`；真实 AI 输出是 `| 1 |` 带空格，导致回退解析也拿不到行。改为 `^\|\s*(\d+)\s*\|(.*)\|$`（stock_extractor.py:651）。
- **deepseek-v4-flash 端点是 api.deepseek.com 而非 sensenova**：用户提供的 key（sk-b08c...）只在官方 `https://api.deepseek.com` 有效，对 `token.sensenova.cn`（商汤）返回 401 code 16。config 与 `_init_deepseek_v4_flash` 默认 base_url 都改为 `https://api.deepseek.com`。最初提交 7ff713f 用的就是官方端点，中途被改坏了。
- **deepseek-v4-flash 是推理模型，thinking 会把输出预算烧光**：默认在长 prompt 下把整个 max_tokens 消耗在 `reasoning_content` 上，`content` 为空且 `finish_reason=length`（实测 3 篇帖子 max_tokens=8192 → reasoning_tokens=8191、content 0 字符）。修复：`DeepSeekV4FlashWrapper.create()` 加 `extra_body={"thinking": {"type": "disabled"}}`。禁用后 10 篇帖子 8192 预算产出 11973 字符（6208 tokens）。
- **DEEPSEEK_V4_FLASH_API_KEY 不兜底 DEEPSEEK_API_KEY**：本地旧 `DEEPSEEK_API_KEY`（sk-6cf...）是无效 key，会遮蔽 config.yaml 里加密的有效 key（`_resolve_api_key` 先查环境变量）。所以 v4-flash 只认 `DEEPSEEK_V4_FLASH_API_KEY`，本地用加密配置（`.secrets/deepseek_v4_flash.key` + Fernet）。
- **v4-flash key 加密惯例**：`config.yaml` 的 `ai.deepseek_v4_flash.api_key_encrypted` 存 `fernet:<密文>`，解密密钥在 `.secrets/deepseek_v4_flash.key`（gitignored）；`_load_encryption_key` 支持 `DEEPSEEK_V4_FLASH_API_KEY_ENCRYPTION_KEY` 环境变量兜底。本地跑 v4-flash 需要 `cryptography`（requirements.txt 已有，PEP 668 需 --break-system-packages 或 venv）。
- **sector_aliases 作用域**：`_enrich_and_score` 里板块推断块必须放在 `sector_aliases = scoring.get("sector_aliases", {})` 之后，否则 UnboundLocalError 崩溃。配置加载要放在所有依赖它的代码之前。
- **`_group_stocks_by_sector` 的分组回退语义**：实现计划 brief 里的 `key = norm if norm else "未分类"` 会把"板块非空但未命中别名"的个股（如"半导体/芯片"）错误并入"未分类"。正确语义应以测试为 spec：norm 为空时回退到原非空板块名，仅 sector 为空的才归"未分类"。config 的 sector_aliases 里规范名通常也有子串别名（如 `半导体: 半导体/芯片`），所以真实数据里规范板块大多能命中；回退只兜住别名表外的非空板块。
- **mock 函数局部 import 的 patch 目标**：当被测代码用函数内 `from X import Y` 导入时，`mock.patch("stock_extractor.Y")` 是静默空转（stock_extractor 没有模块级 Y 属性），必须 patch 源模块 `X.Y`。同理 E2E 测试要 patch `storage.save_enriched_stocks`/`storage.append_recommendation_history`（而非 `stock_extractor.*`），才能让测试免副作用。
- **报告按板块分类展示**：股票报告主清单从扁平"快速选股清单"改为"📋 按板块分类（全部候选，评分仅作板块内排序）"，用 `_group_stocks_by_sector(passed, sector_aliases)` 聚合；`_select_report_display_stocks` 不再按分数截断（display_count=全部候选，recommendation_count 仅统计）。筛选链路：全部 enriched → `_apply_liquidity_filter` → `filter_by_correlation`。删除死代码：`_apply_portfolio_constraints`、`REPORT_MIN_*` 常量、`_append_report_filter_note`。`_rebuild_report` 相关测试需 mock `portfolio_builder.filter_by_correlation`/`select_allocation_method` 和 `concentration_monitor.compute_concentration`，否则真实网络调用拖到 ~90s。

## Decision Log (2026-08-04)

- [2026-08-04] **选股 0 只修复方案**：根因是四层：①解析链路双 bug（空 JSON 遮蔽表格 + 回退正则不认空格行）→ 空 JSON 不信任 + 表格回退 + 4 回归测试；②API key 端点错误（sensenova 401）→ base_url 改回 api.deepseek.com + 加密 key；③deepseek-v4-flash 推理烧光输出预算 → thinking disabled；④sector_aliases 作用域 bug → 配置加载提到板块推断前。验证：24 篇相关帖子端到端产出 62 只增强候选（>=3 分 4 只），报告保存成功。
- [2026-08-05] **个股按板块分类展示 + 去掉评分阈值（方案3）**：报告主清单改为按板块分类展示全部候选（评分仅作板块内排序/标注），移除评分阈值截断与每板块上限，保留流动性/相关性风控。6 任务 SDD 执行：新增 `_group_stocks_by_sector`、`_select_report_display_stocks` 去截断、`_rebuild_report` 筛选链路改造、按板块分类主清单、端到端回归、清理死代码。测试 117 passed。设计/计划见 docs/superpowers/specs + plans 2026-08-04 两份文档。
- [2026-07-29] **数据源复用策略**：集中度指标复用 `sector_monitor.fetch_boards(board_type="industry")`（提供 main_net_yi + amount_yi）和 `fetch_market_indices()`（提供 change_pct + up/down_count），不直接调用 eastmoney API。降级策略：fetch 失败直接标记 unavailable，不实现 spec 中的腾讯兜底（简化 + unavailable 路径已足够安全）。

## Key Learnings (2026-08-04 Task 4)

- **板块替换跨任务测试耦合**：Task 3 的 `TestRebuildReportNoScoreThreshold` 断言 `"快速选股清单" in report`（注释明确写着"Task 4 才替换"），Task 4 把该章节替换为"按板块分类"后此断言必然失败。跨任务删除/重命名报告章节时，必须 grep 所有测试里对旧章节名的断言并同步更新，否则全量回归必红。更新为 `assert "按板块分类" in report` 保留测试本意（低分票不被阈值丢弃）即可。
- **Task 4 实现代码与测试无矛盾**：与 Task 1-3 不同，本次 brief 的 Step 4 实现代码与 Step 1 测试完全自洽，`_group_stocks_by_sector(passed, sector_aliases)` + `_load_scoring_config()` 直接照抄即可，无需要按测试修正实现。

## Key Learnings (2026-08-05 Task 5)

- **brief 的 mock 目标/返回值可能过期**：Task 5 brief 的 E2E 测试两处要按真实实现修正：①`mock.patch("stock_extractor.get_client")` → 该名只存在于函数内 `from summarizer import get_client`，不是模块属性，须改 patch `summarizer.get_client`；②`detect_market_regime` mock 返回 tuple `("中性", {})` → 真实返回 dict，tuple 在 `_enrich_and_score` 被 try/except 容忍但流入 `trend_data["market_regime"]` 后在 `_rebuild_report` 的 `regime.get("label")`（无 try/except）崩溃。写 mock 前先 grep 真实导入来源与返回类型。
- **`_apply_liquidity_filter` 在 stock_extractor 是模块属性**（def 2577），patch `stock_extractor._apply_liquidity_filter` 有效；`_rebuild_report` 内 `filter_by_correlation`/`format_portfolio_summary` 有 try/except，E2E 无需 mock。

## Key Learnings (2026-08-05 Final Review Fix)

- **patch 函数内 `from X import Y` 的局部导入，目标是 `X.Y`，不是调用方模块属性**：`_rebuild_report` 里 `from portfolio_builder import filter_by_correlation, select_allocation_method`（line ~3727）和 `from concentration_monitor import compute_concentration`（line ~3760）、`extract_stock_opportunities` 里 `from storage import append_recommendation_history, save_enriched_stocks`（line ~288）都是在**函数执行时**从源模块取属性绑定局部名。`mock.patch("stock_extractor.filter_by_correlation")` 等对 `stock_extractor.*` 打补丁是 no-op（该名根本不是 stock_extractor 的属性）。正确目标：`mock.patch("portfolio_builder.filter_by_correlation")`、`mock.patch("portfolio_builder.select_allocation_method")`、`mock.patch("concentration_monitor.compute_concentration", return_value=None)`、`mock.patch("storage.save_enriched_stocks")`、`mock.patch("storage.append_recommendation_history")`。final-review brief 建议的 `stock_extractor.*` 目标本身就是错的，写这类 mock 前先确认是模块级 def 还是函数内局部 import。
- **`_append_trader_summary` 对 `concentration_snapshot=None` 是安全的**（`if concentration_snapshot:` 守卫，line ~3445），所以 `compute_concentration` mock 直接 `return_value=None` 即可，无需构造最小 snapshot dict；`_append_concentration_gauge` 在 None 时不调用。

## Key Learnings (2026-08-05 Task 1)

- **brief 的弱断言可能不是有效 RED 测试**：Task 1 `test_market_penalty_capped_at_1` 的断言 `bs >= 1.0` 在旧代码上也通过（`_buy_score` 有 `max(1.0, ...)` 下限，旧值 1.5 ≥ 1.0），无法区分惩罚是否封顶。brief 注释自己算出的期望新值是 ≈2.48。处理：按 TDD 把断言加强为 `bs >= 2.0`（旧 1.5 失败、新 2.5 通过），以 brief 意图为 spec 而非照抄弱断言。写 brief 提供的测试时先心算新旧值确认断言有判别力。
- **`_buy_score` 的 `max(1.0, ...)` 下限会掩盖惩罚改动**：由于最终 `round(max(1.0, min(10.0, raw - penalty)), 1)` 有 1.0 地板，任何小于 1.0 的原始结果都被夹到 1.0。测试惩罚封顶时，需构造原始分 > 1.0 的输入并断言具体分数区间，否则断言在改动前后都通过。
- **评分基线四参数已调整（commit 38b12a0）**：base_consensus 单作者 2.0→3.5、2帖 3.0→4.0、3帖 3.5→4.5（保持单调）；时间加权 `base_consensus * recency_weight` → `* (0.85 + 0.15 * recency_weight)`（recency_weight∈[0.7,1.0]，因子∈[0.955,1.0] 温和衰减）；`_buy_score` 市场惩罚 `min(market_filter.get("buy_penalty",0.0), 1.0)`；`_calibrate_recommendation_score` 下限 `max(1.0,...)`→`max(1.5,...)`。为"精选 Top 清单"功能铺路，让候选池能从 2.1 分档铺开到更高分档。

## Key Learnings (2026-08-05 Task 4)

- **精选 Top 清单 brief 的测试 mock 目标又错了，但实现代码照抄即可**：Task 4 brief 的 Step 1 测试里 `mock.patch("stock_extractor.filter_by_correlation")` 和 `mock.patch("stock_extractor.compute_concentration")` 是静默空转（`_rebuild_report` 内是 `from portfolio_builder import filter_by_correlation` / `from concentration_monitor import compute_concentration` 函数内局部导入）。按既有模式改为 patch `portfolio_builder.filter_by_correlation`/`portfolio_builder.select_allocation_method`/`concentration_monitor.compute_concentration`，并补上 brief 漏掉的 `select_allocation_method` mock。与之前 Task 4（按板块分类）的 mock 目标完全一致。实现代码（排序 scored_candidates 取前 N、渲染表格）与测试自洽，无需按测试修改实现。
- **精选表在报告中的位置在"最适合买入清单"之后、"按板块分类"之前**：精选分 `_selectivity_score` 会为缺 `selectivity_score` 的 stock 回填，因此测试里 hand-built 的 dict 也能排对序；护城河 >=8 加 🏰 标识。

## Key Learnings (2026-08-06 Task 5)

- **E2E 的 mock 目标修正第三次重复出现**：Task 5 brief 的端到端测试和 Task 4 一样又写错了 mock 目标——`stock_extractor.filter_by_correlation`/`stock_extractor.compute_concentration` 是 `_rebuild_report` 内函数级局部导入（patch `stock_extractor.*` 静默空转），且 `detect_market_regime` 用了已废弃的 tuple 返回值 `("中性", {})`。统一按既有模式修正：detect_market_regime 必须 mock 返回 dict（tuple 流入 `trend_data["market_regime"]` 后在 `_rebuild_report` 的 `regime.get("label")` 无 try/except 处崩溃）；相关性/集中度 patch `portfolio_builder.*`/`concentration_monitor.*`；同 try 块补 `portfolio_builder.select_allocation_method`。写 brief 提供的测试前先 grep 真实导入来源与返回类型——本项目每次 brief 都在这里错。
- **现有 `test_full_pipeline_no_zero_candidates` 仍会发真实网络调用**：该旧 E2E 未 mock `filter_by_correlation`（`_fetch_recent_returns`）和 `compute_concentration`，全量 suite 时长在 1.3s~43s 间随机波动（有 try/except 不会崩溃，只是慢）。新 `test_end_to_end_has_selectivity_section` 是 hermetic（~0.08s）。若后续要稳定 CI 时长，可给旧 E2E 补同样的 hermetic mock。

## Decision Log (2026-08-06)

- [2026-08-06] **精选 Top 清单端到端验证（Task 5）**：新增 `TestExtractEndToEnd.test_end_to_end_has_selectivity_section`，驱动 `extract_stock_opportunities` 全链路（mock summarizer.get_client + price_fetcher.* + market_regime.* + storage 写库 no-op + portfolio_builder.*/concentration_monitor.*），断言报告含"⭐ 精选 Top 清单"与思泉新材。全量 125 passed，commit `test(stocks): 精选 Top 清单端到端验证`。
