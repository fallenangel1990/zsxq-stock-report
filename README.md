# AI Berkshire — 知识星球量化选股系统

基于知识星球专栏内容，通过 AI 提取投资机会、量化评分、自动推送报告的全流程量化选股系统。

---

## 系统架构

```
知识星球专栏 → 爬取 → AI 提取 → 量化评分 → 报告/同花顺同步
                ↓           ↓          ↓
           原始数据    候选股票    1-10分评分
                       ↓          ↓
                  板块轮动检测  止损/仓位建议
                       ↓
                  邮件/微信推送
```

---

## 快速开始

### 1. 安装

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

### 2. 配置

编辑 `config.yaml`：

```yaml
ai:
  provider: deepseek-v4-flash    # LLM 提供商
  deepseek_v4_flash:
    api_key: "你的API Key"       # 或设置环境变量 DEEPSEEK_V4_FLASH_API_KEY
    model: deepseek-v4-flash
    base_url: https://token.sensenova.cn/v1

zsxq:
  group_url: https://wx.zsxq.com/group/你的专栏ID

stocks:
  scoring:
    score_threshold: 3.0          # ≥3分加入自选股
```

### 3. 登录知识星球

```bash
python3 main.py login            # 扫码登录，保存 cookie
```

### 4. 运行完整流程

```bash
# 一键：爬取 + AI 提取 + 评分 + 同花顺同步 + 邮件
python3 main.py all https://wx.zsxq.com/group/你的专栏ID
```

---

## 核心命令

| 命令 | 功能 | 示例 |
|------|------|------|
| `all` | 全流程（爬取+提取+评分+同步） | `python3 main.py all <url>` |
| `crawl` | 仅爬取帖子 | `python3 main.py crawl <url> -n 50` |
| `stocks` | 提取股票机会并评分 | `python3 main.py stocks` |
| `thssync` | 同步到同花顺自选股 | `python3 main.py thssync` |
| `sectors` | 板块异动/复盘 | `python3 main.py sectors --mode review` |
| `market` | 大盘+板块信号 | `python3 main.py market --mode intraday` |
| `review` | 盘后复盘报告 | `python3 main.py review --email` |
| `monitor` | 盘中预警监控 | `python3 main.py monitor` |
| `backtest` | 回测评分体系 | `python3 main.py backtest` |
| `adaptive-weights` | 自适应权重 | `python3 main.py adaptive-weights` |
| `research` | 个股深度研究 | `python3 main.py research 华亚智能` |
| `web` | 启动 Web 仪表盘 | `python3 main.py web` |

---

## 自动化（GitHub Actions）

系统通过 GitHub Actions 全自动运行，无需人工干预：

| Workflow | 触发时间 | 功能 |
|----------|----------|------|
| `daily-report.yml` | 每交易日 08:50 | 爬取+提取+评分+同花顺同步+邮件 |
| `premarket-briefing.yml` | 每交易日 08:50 | 盘前财经快讯（全球科技新闻） |
| `intraday-monitor.yml` | 09:25 + 13:00 | 盘中预警（个股+板块轮动） |
| `market-review.yml` | 手动/盘后 | 盘后复盘报告 |
| `stock-dashboard.yml` | 手动 | 静态看板部署到 GitHub Pages |

### 需要的 GitHub Secrets

```
DEEPSEEK_V4_FLASH_API_KEY   # LLM API Key
SMTP_USER                    # 发件邮箱
SMTP_PASS                    # 邮箱授权码
TO_EMAIL                     # 收件邮箱
ZSXQ_COOKIES                 # 知识星球 Cookie
THS_COOKIES                  # 同花顺 Cookie（可选）
```

---

## 评分体系

每只股票最终得分 1-10 分，由 9 个因子加权计算：

| 因子 | 权重 | 含义 |
|------|------|------|
| upside | 0.20 | 上涨空间（目标价/当前价） |
| quality | 0.14 | 信息质量（是否有量化参考） |
| consensus | 0.10 | 分析师共识（独立作者数） |
| sector | 0.14 | 板块热度（板块内推荐密度） |
| trend | 0.12 | 行业趋势（板块涨跌排名） |
| fundamentals | 0.06 | 基本面（PE/PB/市值） |
| capital_flow | 0.08 | 资金流（龙虎榜/主力净流入） |
| volume_confirm | 0.08 | 量价确认（量比/均线） |
| logic | 0.08 | 逻辑质量（AI 情感分析） |

### 评分调整项

- **护城河加分**：宽护城河(≥8分) +0.5，中等(≥6分) +0.2
- **拥挤度惩罚**：同板块推荐>2只时，从第3只起 -0.3分/只
- **ATR 止损**：`max(价格×0.94, 价格-2×ATR)`

### 评分等级

| 分数 | 含义 | 操作建议 |
|------|------|----------|
| 8-10 | 强烈关注 | 可执行买入 |
| 5-8 | 重点观察 | 等回踩买点 |
| 3-5 | 一般关注 | 观察清单 |
| <3 | 不推荐 | 过滤掉 |

---

## 市场状态自适应

系统自动检测市场状态并调整权重：

| 状态 | 特征 | 权重偏向 |
|------|------|----------|
| 强势进攻 | 指数站上20日线，赚钱效应好 | 重趋势、重动量 |
| 修复可试仓 | 超跌反弹，资金回流 | 重资金流、重超跌 |
| 震荡观察 | 方向不明 | 均衡配置 |
| 防守降仓 | 趋势向下 | 重质量、重估值 |

---

## 回测验证

```bash
# 验证评分体系有效性
python3 main.py backtest

# 查看因子 IC（信息系数）
python3 main.py factor-research

# 查看自适应权重
python3 main.py adaptive-weights
```

回测会输出：
- 各因子 IC（|IC|>0.03 有效，>0.05 较强）
- 评分分组收益（验证高分是否=高收益）
- IC 统计显著性（t 检验）

---

## 目录结构

```
├── main.py                  # CLI 入口
├── stock_extractor.py       # 股票提取 + 评分（核心）
├── summarizer.py            # LLM 客户端
├── price_fetcher.py         # 行情数据
├── sector_monitor.py        # 板块异动检测
├── market_regime.py         # 市场状态检测
├── portfolio_builder.py     # 组合优化（Kelly/风险平价）
├── adaptive_weights.py      # 自适应权重（IC 驱动）
├── backtester.py            # 回测验证
├── premarket_briefing.py    # 盘前快讯
├── intraday_monitor.py      # 盘中预警
├── dashboard.py             # Web 仪表盘
├── ths_sync.py              # 同花顺同步
├── email_sender.py          # 邮件发送
├── storage.py               # 数据存储
├── config.yaml              # 配置（本地，不提交）
├── config.example.yaml      # 配置模板
├── tests/                   # 单元测试
│   ├── test_portfolio_builder.py
│   └── test_stock_extractor.py
├── .github/workflows/       # GitHub Actions
│   ├── daily-report.yml
│   ├── premarket-briefing.yml
│   ├── intraday-monitor.yml
│   ├── market-review.yml
│   └── stock-dashboard.yml
└── data/
    ├── raw/                 # 爬取的原始帖子
    ├── summary/             # 报告/评分结果
    │   ├── history/         # 推荐历史（JSONL）
    │   ├── briefings/       # 盘前快讯
    │   └── sectors/         # 板块复盘
    └── state/               # 预警状态
```

---

## 本地开发

```bash
# 运行测试
python3 -m pytest tests/ -v

# 代码检查
ruff check .

# 本地启动仪表盘
python3 main.py web
# 访问 http://localhost:8501
```

---

## 注意事项

1. **API Key 安全**：永远不要把 `config.yaml` 中的 API Key 提交到 Git
2. **Cookie 有效期**：知识星球 Cookie 约 7-14 天过期，需定期 `refresh-cookie`
3. **同花顺同步**：需在同花顺 APP 扫码登录后导出 Cookie
4. **邮件发送**：QQ 邮箱需使用"授权码"而非登录密码
5. **风险提示**：系统生成的所有信息仅供参考，不构成投资建议
