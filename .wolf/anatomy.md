# anatomy.md

> Auto-maintained by OpenWolf. Last scanned: 2026-08-05T01:06:17.616Z
> Files: 76 tracked | Anatomy hits: 0 | Misses: 0

## ../../../.claude/plans/

- `indexed-percolating-thimble.md` — 行业趋势检测 & 优先推荐 (~761 tok)
- `piped-chasing-sky.md` — 定时任务 + PDF 生成 + 邮件发送 (~1111 tok)

## ../../../.cursor/projects/Users-chenlin-Desktop-claud-code-zsxq-stock/agent-tools/

- `707a7088-9ff7-4e5e-9304-1e434635177f.txt` — Declares PortfolioManager (~4889 tok)
- `a1170b5e-6fd8-4a71-96fb-a02e7c97b4ea.txt` — /*.tsx" (~6640 tok)

## ../../../.cursor/rules/

- `reply-in-chinese.mdc` (~50 tok)

## ../../../Library/Application Support/Cursor/User/

- `locale.json` (~7 tok)

## ../../.claude/plans/

- `warm-dancing-wave.md` — 股票评分体系优化 — P0+P1 实现计划 (~1069 tok)

## ./

- `.DS_Store` (~2732 tok)
- `.gitignore` — Git ignore rules (~15 tok)
- `adaptive_weights.py` — 自适应权重闭环：基于因子IC分析自动调整评分权重，支持滚动IC、衰减检测、权重平滑。 (~3200 tok)
- `AGENTS.md` — OpenWolf 项目协作说明，要求每次会话读取 .wolf/OPENWOLF.md、编码前查看 cerebrum、读文件前查看 anatomy。 (~57 tok)
- `attachment_processor.py` — 知识星球 PDF/音频附件解析模块，下载附件并将 PDF 文本或音频转写文本注入帖子。 (~3300 tok)
- `auth.py` — 知识星球登录与 Cookie 管理模块。 (~1473 tok)
- `backtester.py` — 推荐回测模块。 (~7147 tok)
- `benchmark.py` — 基准对比与收益归因：CSI300/500日收益率、Alpha/Beta/Sharpe、因子归因+行业归因。 (~4200 tok)
- `CLAUDE.md` — OpenWolf (~57 tok)
- `concentration_monitor.py` — 市场资金集中度监控模块。 (~1858 tok)
- `concentration_monitor.py` — 市场资金集中度监控模块：三信号（板块净流入/成交额/宽度背离）采集 + 等级判定 + 综合汇总。 (~2500 tok)
- `config.example.yaml` (~1032 tok)
- `config.yaml` (~705 tok)
- `consecutive_limit_up.py` — A股连板股票扫描模块，抓取涨停池、计算连板天数、AI分类分组、生成报告。 (~4800 tok)
- `cookies.json` (~132 tok)
- `crawler.py` — 知识星球专栏内容爬取模块。 (~3380 tok)
- `dashboard.py` — Web仪表盘Flask服务端：20个API路由，包装所有CLI功能为可视化界面。 (~3200 tok)
- `email_sender.py` — 邮件发送模块。 (~4730 tok)
- `extractor.py` — 内容解析与清洗模块。 (~1290 tok)
- `factor_research.py` — 因子研究框架：分组回测（Quintile Analysis）、因子相关矩阵、因子换手率。 (~2600 tok)
- `intraday_monitor.py` — 盘中动态预警模块。 (~6032 tok)
- `main.py` — 知识星球内容爬取与总结工具 — CLI 入口。 (~9069 tok)
- `market_regime.py` — 市场状态机与自适应配置，含波动率regime+信用利差信号+政策事件检测+流动性状态检测。 (~3500 tok)
- `market_review.py` — A 股盘后复盘任务，汇总指数、全A宽度、板块题材、自选股表现、明日计划和待接入数据项。 (~4460 tok)
- `paper_trader.py` — 模拟交易框架：虚拟买卖、NAV追踪、佣金滑点模拟、自动交易。 (~3600 tok)
- `pdf_generator.py` — 报告 PDF 生成模块。 (~2809 tok)
- `performance_tracker.py` — 推荐绩效跟踪模块。 (~2478 tok)
- `portfolio_builder.py` — 组合构建模块，含Kelly公式+风险平价+自动选择+行业敞口上限+相关性过滤。 (~3500 tok)
- `premarket_briefing.py` — 盘前财经快讯模块。 (~2885 tok)
- `price_fetcher.py` — 实时股价获取模块。 (~6325 tok)
- `requirements.txt` — Python dependencies (~43 tok)
- `stock_extractor.py` — 股票机会提取模块。 (~38494 tok)
- `storage.py` — 数据持久化模块。 (~3506 tok)
- `summarizer.py` — 内容总结模块。 (~4538 tok)
- `test_concentration_intraday.py` — 盘中集中度预警测试（边缘触发 + 日去重 + 回归）。 (~1200 tok)
- `test_concentration_intraday.py` — 集中度盘中预警测试。 (~1467 tok)
- `test_concentration_monitor.py` — 集中度核心模块单元测试（三信号 + 综合判定 + 降级）。 (~1500 tok)
- `test_concentration_monitor.py` — 市场资金集中度模块单元测试。 (~2354 tok)
- `test_concentration_report.py` — 报告集中度仪表盘测试（normal 隐藏 + elevated/danger 格式）。 (~1000 tok)
- `test_concentration_report.py` — 集中度报告集成测试。 (~867 tok)
- `ths_sync.py` — 同花顺账户自选股同步模块。 (~5803 tok)

## .claude/

- `settings.json` (~441 tok)
- `settings.local.json` (~21 tok)

## .claude/rules/

- `openwolf.md` (~313 tok)

## .cursor/rules/

- `reply-in-chinese.mdc` (~49 tok)

## .github/workflows/

- `consecutive-limit-up.yml` — CI: A股连板股票扫描，支持手动触发和收盘后定时扫描+邮件+同花顺分组。 (~1200 tok)
- `daily-report.yml` — CI: 每日股票报告 (~4880 tok)
- `intraday-monitor.yml` — CI: 盘中动态预警 (~1036 tok)
- `market-review.yml` — CI: A 股盘后复盘报告，支持手动触发和盘后定时邮件。 (~1510 tok)
- `stock-dashboard.yml` — CI: 股票仪表盘定时更新/同步任务，含盘中 15 分钟频率和收盘后触发。 (~1600 tok)

## .secrets/

- `deepseek.key` — 本机 DeepSeek 配置密文的 Fernet 解密密钥；已被 .gitignore 忽略，不能提交。 (~1 tok)

## .superpowers/sdd/

- `final-fix-report.md` — 个股按板块分类展示 + 去掉评分阈值 — Final Fix Report (~1435 tok)
- `sector-classified-progress.md` — SDD Progress — 按板块分类展示 + 去评分阈值 (~87 tok)
- `task-1-report.md` — Task 1 Report: 新增 `_group_stocks_by_sector` 板块聚合函数 (~1010 tok)
- `task-2-report.md` — Task 2 Report: `_select_report_display_stocks` 去掉评分截断 (~1303 tok)
- `task-3-report.md` — Task 3 Report: `_rebuild_report` 筛选链路去掉评分阈值 + 每板块上限 (~1638 tok)
- `task-4-fix-report.md` — Fix Report: `_check_concentration` state-clobber bug (~1113 tok)
- `task-4-report.md` — Task 4 Report: 按板块分类主清单 + 过滤概览文案更新 (~1046 tok)
- `task-5-report.md` — Task 5 Report — 端到端回归验证 (E2E Regression Verification) (~1230 tok)

## data/

- `.DS_Store` (~2732 tok)

## data/raw/

- `88888142214212_20260509_021821.json` (~28488 tok)

## data/state/

- `88888142214212.json` (~66 tok)

## data/summary/

- `88888142214212_summary_20260509_022015.md` — 知识星球专栏内容总结 (~3881 tok)

## docs/superpowers/plans/

- `2026-07-29-concentration.md` — 市场资金集中度指标 — 实现计划 (~8682 tok)
- `2026-08-04-sector-classified-report.md` — 个股按板块分类展示 + 去掉评分阈值 — 实现计划 (~5323 tok)

## docs/superpowers/specs/

- `2026-07-29-concentration-design.md` — 市场资金集中度指标 — 设计文档 (~1598 tok)
- `2026-08-04-sector-classified-report-design.md` — 个股按板块分类展示 + 去掉评分阈值 — 设计文档 (~669 tok)

## templates/

- `index.html` — Web仪表盘前端：暗色主题SPA，6个Tab页（总览/股票报告/模拟组合/回测分析/市场状态/数据文件），20个API交互。 (~5500 tok)

## tests/

- `test_stock_extractor.py` — Unit tests for pure functions in stock_extractor.py. (~6054 tok)
