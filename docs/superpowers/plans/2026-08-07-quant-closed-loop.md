# 量化交易闭环 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `quant` 一键闭环命令，串联"评分 → 精选 → 信号 → 执行 → 回测"成单一流程，交易信号消费精选增强数据。

**Architecture:** 改动集中在 `auto_trader.py`（SignalGenerator/RiskController 增强）、`paper_trader.py`（精选过滤）、`main.py`（新增 quant 命令编排）。`quant` 是编排器，调用现有 `extract_stock_opportunities`、`AutoTrader.run`、`run_backtest`，不重写核心逻辑。

**Tech Stack:** Python 3.9+（CI 用 3.12，避免 PEP 604 `dict | None`，用 `Optional[...]`），pytest，PyYAML。

## Global Constraints

- `quant` 命令默认 `--mode semi`（人工确认），`--no-execute` 只出信号不下单
- 信号输入 = 精选增强数据（含 `selectivity_score`/`liquidity_score`/`turnover_rate`/`long_term_value`）
- `SignalGenerator` 买入分支：`liquidity_gate=True` 时过 `_liquidity_eligible`；买入排序改 `(selectivity_score, buy_score)` 降序，字段缺失降级回退 buy_score
- `RiskController` 新增配置：`selectivity_min`（默认 3.0）、`liquidity_gate`（默认 True）
- `_liquidity_eligible`/`_selectivity_score` 从 stock_extractor import 复用（无循环依赖，已验证）
- 决策报告保存到 `data/quant/quant_report_YYYYMMDD_HHMMSS.md`
- 类型注解用 `Optional[...]`，不用 `|`
- 变更后必须 `python3 -m pytest tests/ -q` 全绿

---

### Task 1: SignalGenerator 增强——流动性门槛 + 精选分排序 + reason 标注

**Files:**
- Modify: `auto_trader.py:91-100`（RiskController init）、`360-375`（SignalGenerator 买入分支）
- Modify: `auto_trader.py` 顶部（新增常量 + 从 stock_extractor import）

**Interfaces:**
- Consumes: `stock_extractor._liquidity_eligible`、`_selectivity_score`（已有）
- Produces: `RiskController` 含 `selectivity_min`/`liquidity_gate` 属性；`SignalGenerator.generate_signals` 买入信号过流动性门槛、按精选分排序、reason 附长期价值/流动性

- [ ] **Step 1: 写失败测试**

```python
class TestSignalGeneratorSelectivity:
    """Tests for SignalGenerator using selectivity/liquidity data."""

    def _make_risk(self):
        from auto_trader import RiskController
        return RiskController({
            "buy_score_threshold": 7.4, "buy_total_score": 7.0,
            "sell_score_threshold": 4.0, "liquidity_gate": True,
        })

    def test_low_liquidity_skipped_from_buy(self):
        from auto_trader import SignalGenerator
        risk = self._make_risk()
        gen = SignalGenerator(risk)
        stock = {
            "code": "600001", "name": "低流动性股", "score": 7.5, "buy_score": 8.0,
            "decision_tier": "可执行清单", "market_cap_yi": 30.0,  # <50亿
        }
        signals = gen.generate_signals([stock], [])
        assert len(signals["buy"]) == 0, "低流动性票不应进买入"
        assert len(signals["skip"]) == 1
        assert "流动性" in signals["skip"][0].get("skip_reason", "")

    def test_buy_sorted_by_selectivity_score(self):
        from auto_trader import SignalGenerator
        risk = self._make_risk()
        gen = SignalGenerator(risk)
        stocks = [
            {"code": "600001", "name": "A", "score": 7.5, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0, "selectivity_score": 3.0},
            {"code": "600002", "name": "B", "score": 7.6, "buy_score": 7.5,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0, "selectivity_score": 4.5},
        ]
        signals = gen.generate_signals(stocks, [])
        buy_names = [s["name"] for s in signals["buy"]]
        assert buy_names == ["B", "A"], "精选分高的应排前面（B 4.5 > A 3.0）"

    def test_selectivity_missing_falls_back_to_buy_score(self):
        from auto_trader import SignalGenerator
        risk = self._make_risk()
        gen = SignalGenerator(risk)
        stocks = [
            {"code": "600001", "name": "A", "score": 7.5, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0},  # 无 selectivity
            {"code": "600002", "name": "B", "score": 7.6, "buy_score": 7.5,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0},
        ]
        signals = gen.generate_signals(stocks, [])
        buy_names = [s["name"] for s in signals["buy"]]
        assert buy_names == ["A", "B"], "无精选分时回退 buy_score 排序（A 8.0 > B 7.5）"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_auto_trader.py -k TestSignalGeneratorSelectivity -v`
Expected: FAIL（低流动性票进买入、排序未用精选分）

- [ ] **Step 3: RiskController 新增配置项（91-100）**

在 `__init__` 末尾加：

```python
        self.selectivity_min = config.get("selectivity_min", DEFAULT_SELECTIVITY_MIN)
        self.liquidity_gate = config.get("liquidity_gate", True)
```

在默认常量区（45 行后）加：

```python
DEFAULT_SELECTIVITY_MIN = 3.0
```

- [ ] **Step 4: SignalGenerator 买入分支增强（360-375）**

在 `SignalGenerator.__init__`（319 行）前从 stock_extractor import：

```python
    from stock_extractor import _liquidity_eligible, _selectivity_score
```

将买入分支（360-371）改为：

```python
            # 买入信号
            if tier == "可执行清单" and buy_score >= self.risk.buy_score_threshold and score >= self.risk.buy_total_score:
                # 流动性门槛：低流动性票不进可执行清单
                if self.risk.liquidity_gate and not _liquidity_eligible(stock):
                    signals["skip"].append({**stock, "skip_reason": f"流动性不足(市值{stock.get('market_cap_yi')}亿<50亿)"})
                    continue
                sel = stock.get("selectivity_score")
                ltv = stock.get("long_term_value")
                liq = stock.get("liquidity_score")
                reason = f"buy_score={buy_score:.1f}, score={score:.1f}"
                if sel is not None:
                    reason += f", 精选分={sel:.1f}"
                if ltv is not None:
                    reason += f", 长期价值={ltv:.1f}"
                if liq is not None:
                    reason += f", 流动性={liq:.1f}"
                signals["buy"].append({**stock, "signal_reason": reason})
            elif tier == "观察清单":
                signals["skip"].append({**stock, "skip_reason": "观察清单，等待触发"})
            else:
                signals["skip"].append({**stock, "skip_reason": f"tier={tier}, buy_score={buy_score:.1f}"})
```

- [ ] **Step 5: 买入排序改精选分优先（374-375）**

```python
        # 排序：买入按 (精选分, buy_score) 降序；无精选分回退 buy_score
        def _buy_sort_key(x):
            sel = x.get("selectivity_score")
            return (sel if sel is not None else -1.0, x.get("buy_score", 0))
        signals["buy"].sort(key=_buy_sort_key, reverse=True)
        signals["sell"].sort(key=lambda x: x.get("score", 0))
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest tests/test_auto_trader.py -k TestSignalGeneratorSelectivity -v`
Expected: PASS（3 passed）

- [ ] **Step 7: 全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 8: 提交**

```bash
git add auto_trader.py tests/test_auto_trader.py
git commit -m "feat(quant): SignalGenerator 接入精选数据——流动性门槛+精选分排序+长期价值标注"
```

---

### Task 2: paper_trader 精选过滤

**Files:**
- Modify: `paper_trader.py:599-620`（auto_trade_from_recommendations）

**Interfaces:**
- Consumes: `stock_extractor._liquidity_eligible`、`_selectivity_score`（已有）
- Produces: `auto_trade_from_recommendations` 只模拟精选合格 + 流动性门槛的票

- [ ] **Step 1: 写失败测试**

```python
class TestPaperTraderSelectivity:
    """Tests for paper_trader using selectivity/liquidity data."""

    def test_low_liquidity_not_simulated(self):
        from unittest import mock
        from paper_trader import auto_trade_from_recommendations
        enriched = [
            {"code": "600001", "name": "低流动性", "score": 8.0, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 30.0},  # <50亿
            {"code": "600002", "name": "高流动性", "score": 8.0, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 200.0},
        ]
        with mock.patch("price_fetcher.fetch_single_price", return_value={"price": 10.0}), \
             mock.patch("paper_trader._save_trade", return_value=None):
            trades = auto_trade_from_recommendations(enriched, verbose=False)
        trade_codes = [t.get("code") for t in trades]
        assert "600001" not in trade_codes, "低流动性票不应模拟买入"
        assert "600002" in trade_codes
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_paper_trader.py -k TestPaperTraderSelectivity -v`
Expected: FAIL（低流动性票被模拟买入）

- [ ] **Step 3: 实现精选过滤（599-620）**

在 `auto_trade_from_recommendations` 循环买入判断前加：

```python
    from stock_extractor import _liquidity_eligible
```

在买入条件处加流动性门槛（找到 `if decision_tier == "可执行清单"` 附近）：

```python
            # 流动性门槛：低流动性票不模拟买入
            if not _liquidity_eligible(stock):
                continue
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_paper_trader.py -k TestPaperTraderSelectivity -v`
Expected: PASS

- [ ] **Step 5: 全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add paper_trader.py tests/test_paper_trader.py
git commit -m "feat(quant): paper_trader 模拟交易加流动性门槛"
```

---

### Task 3: main.py 新增 `quant` 命令（编排器）

**Files:**
- Modify: `main.py`（新增 cmd_quant + subparser + dispatch）

**Interfaces:**
- Consumes: `extract_stock_opportunities`、`AutoTrader`、`run_backtest`、`format_backtest_report`、`_liquidity_eligible`（已有）
- Produces: `python3 main.py quant [--mode semi|full] [--no-execute]` 一键闭环 + 决策报告

- [ ] **Step 1: 写失败测试（验证 quant 编排函数存在且产出报告）**

```python
class TestQuantCommand:
    """Tests for quant closed-loop command."""

    def test_quant_orchestrator_exists(self):
        import main
        assert hasattr(main, "cmd_quant"), "main 应有 cmd_quant 编排函数"

    def test_quant_report_generation(self):
        # 用 mock 数据验证 quant 能产出决策报告
        from unittest import mock
        import main
        enriched = [
            {"code": "600001", "name": "A", "score": 8.0, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0,
             "selectivity_score": 5.0, "liquidity_score": 6.0},
        ]
        with mock.patch("main.extract_stock_opportunities", return_value="mock report"), \
             mock.patch("storage.save_enriched_stocks", return_value=""), \
             mock.patch("storage.append_recommendation_history", return_value=""), \
             mock.patch("auto_trader.AutoTrader.run", return_value={
                 "signals": {"buy_list": [], "sell_list": [], "buy_count": 0, "sell_count": 0,
                             "hold_count": 0, "skip_count": 1},
                 "executed": [], "risk_status": {},
                 "mode": "semi", "time": "2026-08-07T09:30:00"}):
            report = main._build_quant_report(enriched, None, "2026-08-07")
        assert "量化交易决策报告" in report
        assert "精选候选" in report
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_main.py -k TestQuantCommand -v`（若无 test_main.py 则创建）
Expected: FAIL（无 cmd_quant/_build_quant_report）

- [ ] **Step 3: 实现 cmd_quant 编排（main.py）**

在 `cmd_auto`（742 行）后新增：

```python
def cmd_quant(args) -> None:
    """量化交易闭环：评分→精选→信号→执行→回测，输出决策报告。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    # 1. 生成精选增强数据
    from storage import load_latest_raw
    posts, _ = load_latest_raw()
    if not posts:
        _log("错误：没有已爬取的数据。请先运行 crawl 命令。")
        return
    from stock_extractor import extract_stock_opportunities
    _log(f"共 {len(posts)} 篇帖子，提取股票机会...")
    extract_stock_opportunities(posts)

    # 2. 加载最新 enriched（含精选分/流动性/换手率）
    from storage import load_latest_stock_data
    enriched, _ = load_latest_stock_data()
    if not enriched:
        _log("错误：无推荐数据，请先运行 stocks 命令。")
        return
    _log(f"共 {len(enriched)} 只候选，生成交易信号...")

    # 3. 构建决策报告（信号预览，不下单）
    from main import _build_quant_report
    date_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    report = _build_quant_report(enriched, args, date_str)

    # 4. 保存报告
    from pathlib import Path
    quant_dir = Path("data/quant")
    quant_dir.mkdir(parents=True, exist_ok=True)
    report_path = quant_dir / f"quant_report_{date_str}.md"
    report_path.write_text(report, encoding="utf-8")
    _log(f"决策报告已保存到: {report_path}")

    # 5. 执行（--no-execute 则只出报告不下单）
    if getattr(args, "no_execute", False):
        _log("⏸ --no-execute 模式：只生成信号与报告，不下单。")
        print(report)
        return

    from auto_trader import AutoTrader, load_trading_config
    config = load_trading_config()
    mode = getattr(args, "mode", None) or config.get("mode", "semi")
    config["mode"] = mode
    trader = AutoTrader(config)
    _log(f"🤖 执行量化交易（模式: {mode}）...")
    result = trader.run(enriched_stocks=enriched)
    if "error" in result:
        _log(f"❌ 执行错误: {result['error']}")
    elif result.get("circuit_breaker"):
        _log(f"⚠️ 熔断已触发: {result.get('circuit_breaker_reason', '')}")
    else:
        signals = result.get("signals", {})
        _log(f"✅ 执行完成: 买{signals.get('buy_count',0)} 卖{signals.get('sell_count',0)}")
        for t in result.get("executed", []):
            _log(f"   {t.get('action')} {t.get('name')}({t.get('code')}) × {t.get('shares')} @ {t.get('price')}")

    # 6. 回测复盘
    try:
        from backtester import run_backtest, format_backtest_report
        metrics = run_backtest()
        print("\n" + "=" * 40)
        print("📊 回测复盘:")
        print(format_backtest_report(metrics))
    except Exception as exc:
        _log(f"回测失败（不影响交易）: {exc}")

    print(report)


def _build_quant_report(
    enriched: list[dict],
    args,
    date_str: str,
) -> str:
    """构建量化交易决策报告（不含下单执行）。"""
    from stock_extractor import _liquidity_eligible
    parts = []

    # 头部
    parts.append(f"# 🤖 量化交易决策报告 {date_str[:8]} {date_str[9:15]}\n")

    # 精选候选（复用精选分排序）
    from stock_extractor import _load_scoring_config, _group_stocks_by_sector
    scoring_cfg = _load_scoring_config()
    aliases = scoring_cfg.get("sector_aliases", {})
    eligible = [s for s in enriched if _liquidity_eligible(s)]
    eligible.sort(key=lambda s: (s.get("selectivity_score") or 0), reverse=True)
    top = eligible[:10]
    parts.append("## ⭐ 精选候选（前 10 只，流动性合格）\n")
    parts.append("| 排名 | 股票 | 板块 | 精选分 | 推荐指数 | 长期价值 | 流动性 | 换手率 | 买入参考 |")
    parts.append("|------|------|------|--------|----------|----------|--------|--------|----------|")
    for i, s in enumerate(top, 1):
        parts.append(
            f"| {i} | {s.get('name','-')} | {s.get('sector','-')} | "
            f"{s.get('selectivity_score','-')} | {s.get('score','-')} | "
            f"{s.get('long_term_value','-')} | {s.get('liquidity_score','-')} | "
            f"{s.get('turnover_rate','-')} | {s.get('entry_ref','-')[:30]} |"
        )
    parts.append("")

    # 交易信号（调用 SignalGenerator 预览，不下单）
    try:
        from auto_trader import load_trading_config, RiskController, SignalGenerator
        config = load_trading_config()
        mode = getattr(args, "mode", None) or config.get("mode", "semi")
        risk = RiskController(config.get("risk", config))
        gen = SignalGenerator(risk)
        signals = gen.generate_signals(eligible, [])
        parts.append(f"## 📈 交易信号（模式: {mode}）\n")
        parts.append(f"- 🟢 买入 {len(signals['buy'])} 只")
        parts.append(f"- 🔴 卖出 {len(signals['sell'])} 只")
        parts.append(f"- ⏸ 持有 {len(signals['hold'])} 只")
        parts.append(f"- ⏭ 跳过 {len(signals['skip'])} 只")
        parts.append("")
        if signals["buy"]:
            parts.append("**买入候选：**")
            for s in signals["buy"]:
                parts.append(f"- {s.get('name')}({s.get('code')}) {s.get('signal_reason','')}")
            parts.append("")
    except Exception as exc:
        parts.append(f"## 📈 交易信号\n\n> 生成失败: {exc}\n")
    parts.append("")

    # 风控状态
    parts.append("## 🛡️ 风控状态\n")
    parts.append(f"- 精选分门槛: {getattr(risk, 'selectivity_min', 3.0)} | 流动性门槛: {'开' if getattr(risk, 'liquidity_gate', True) else '关'}")
    parts.append("")

    # 回测复盘
    try:
        from backtester import run_backtest, format_backtest_report
        metrics = run_backtest()
        parts.append("## 📊 回测复盘\n")
        parts.append(format_backtest_report(metrics))
    except Exception as exc:
        parts.append(f"## 📊 回测复盘\n\n> 回测失败: {exc}\n")
    parts.append("")

    return "\n".join(parts)
```

- [ ] **Step 4: 注册 subparser（921 行 auto_parser 后）**

```python
    quant_parser = subparsers.add_parser("quant", help="量化交易闭环（评分→精选→信号→执行→回测）")
    quant_parser.add_argument("--mode", choices=["semi", "full"], default=None, help="交易模式（默认读配置 semi）")
    quant_parser.add_argument("--no-execute", action="store_true", help="只出信号不下单（安全预览）")
```

- [ ] **Step 5: 注册 dispatch（978 行 auto 后）**

```python
    elif args.command == "quant":
        cmd_quant(args)
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest tests/test_main.py -k TestQuantCommand -v`
Expected: PASS

- [ ] **Step 7: 全量测试 + 回归**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 8: 提交**

```bash
git add main.py tests/test_main.py
git commit -m "feat(quant): 新增 quant 一键闭环命令——评分/精选/信号/回测决策报告"
```

---

### Task 4: 端到端验证 + 文档

**Files:**
- Modify: `tests/test_main.py`（quant 集成测试）
- Modify: `.wolf/cerebrum.md`、`.wolf/memory.md`（OpenWolf 追踪）

**Interfaces:**
- Consumes: `cmd_quant` 产出报告
- Produces: 端到端验证 + OpenWolf 追踪

- [ ] **Step 1: 端到端集成测试**

```python
    def test_quant_no_execute_end_to_end(self):
        from unittest import mock
        import main
        from pathlib import Path
        import tempfile
        with mock.patch("main.load_latest_raw", return_value=([{"content": "测试"}], "f")), \
             mock.patch("main.extract_stock_opportunities", return_value="ok"), \
             mock.patch("storage.load_latest_stock_data", return_value=(
                 [{"code": "600001", "name": "A", "score": 8.0, "buy_score": 8.0,
                   "decision_tier": "可执行清单", "market_cap_yi": 100.0,
                   "selectivity_score": 5.0, "liquidity_score": 6.0}], "")), \
             mock.patch("backtester.run_backtest", return_value={"metrics": {}}), \
             mock.patch("backtester.format_backtest_report", return_value="回测摘要"), \
             mock.patch("main.Path.write_text", return_value=None):
            class FakeArgs:
                mode = None
                no_execute = True
            main.cmd_quant(FakeArgs())
```

- [ ] **Step 2: 运行测试确认通过**

Run: `python3 -m pytest tests/test_main.py -k test_quant_no_execute_end_to_end -v`
Expected: PASS

- [ ] **Step 3: 全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 4: 更新 OpenWolf 追踪 + 提交**

向 `.wolf/cerebrum.md` Decision Log 追加 2026-08-07 量化闭环条目；`.wolf/memory.md` 追加操作行。

```bash
git add tests/test_main.py .wolf/cerebrum.md .wolf/memory.md
git commit -m "test(quant): 量化闭环端到端验证"
git push origin main
```

- [ ] **Step 5: 验证推送**

Run: `git log --oneline origin/main -1`
Expected: 显示最新提交
