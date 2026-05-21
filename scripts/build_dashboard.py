#!/usr/bin/env python3
"""Build a GitHub Pages stock dashboard.

The dashboard is intentionally static: GitHub Actions refreshes the generated
HTML/JSON on a schedule, so it works on GitHub Pages without a backend server.
"""

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SITE_DIR = ROOT / "site"
DATA_DIR = SITE_DIR / "data"
REPORTS_DIR = SITE_DIR / "reports"
SUMMARY_DIR = ROOT / "data" / "summary"


def _load_config() -> dict:
    config_path = ROOT / "config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _latest_file(pattern: str) -> Optional[Path]:
    if not SUMMARY_DIR.exists():
        return None
    files = sorted(SUMMARY_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _copy_latest_reports() -> dict:
    reports = {}
    patterns = {
        "stocks": "*stocks*.md",
        "market": "market/*.md",
        "sectors": "sectors/*.md",
        "research": "research/*.md",
        "summary": "*summary*.md",
    }
    for key, pattern in patterns.items():
        src = _latest_file(pattern)
        if not src:
            continue
        dest = REPORTS_DIR / f"latest_{key}.md"
        shutil.copyfile(src, dest)
        reports[key] = {
            "title": src.name,
            "path": f"reports/{dest.name}",
            "updated_at": datetime.fromtimestamp(src.stat().st_mtime).isoformat(),
        }
    return reports


def _build_payload() -> dict:
    from sector_monitor import capture_market_signals, capture_sector_signals

    config = _load_config()
    dashboard_config = config.get("dashboard", {})
    top_n = int(dashboard_config.get("top_n", 10))

    market_report, market, market_boards = capture_market_signals(
        mode="intraday",
        top_n=top_n,
        board_type=dashboard_config.get("board_type", "all"),
        with_ai=False,
    )
    sector_report, sector_boards = capture_sector_signals(
        mode="review",
        top_n=top_n,
        board_type=dashboard_config.get("board_type", "all"),
        with_ai=False,
    )

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    reports = _copy_latest_reports()

    (REPORTS_DIR / "latest_market_signal.md").write_text(market_report, encoding="utf-8")
    reports["market_signal"] = {
        "title": "最新大盘与板块建仓/加仓信号",
        "path": "reports/latest_market_signal.md",
        "updated_at": now.isoformat(),
    }
    (REPORTS_DIR / "latest_sector_signal.md").write_text(sector_report, encoding="utf-8")
    reports["sector_signal"] = {
        "title": "最新板块异动/盘后复盘",
        "path": "reports/latest_sector_signal.md",
        "updated_at": now.isoformat(),
    }

    return {
        "generated_at": now.isoformat(),
        "generated_at_display": now.strftime("%Y-%m-%d %H:%M:%S"),
        "market": market,
        "market_boards": _compact_boards(market_boards),
        "sector_boards": _compact_boards(sector_boards),
        "reports": reports,
        "disclaimer": "数据来自公开行情接口与规则模型识别，仅作观察，不构成投资建议。",
    }


def _compact_boards(boards: list[dict]) -> list[dict]:
    compact = []
    for board in boards:
        compact.append({
            "name": board.get("name", ""),
            "code": board.get("code", ""),
            "action": board.get("action", board.get("signal_level", "")),
            "level": board.get("signal_level", ""),
            "change_pct": board.get("change_pct", 0),
            "main_net_yi": board.get("main_net_yi", 0),
            "main_net_ratio": board.get("main_net_ratio", 0),
            "up_ratio": board.get("up_ratio", 0),
            "signal_score": board.get("signal_score", 0),
            "leader_name": board.get("leader_name", ""),
            "leader_code": board.get("leader_code", ""),
            "leader_change_pct": board.get("leader_change_pct", 0),
            "logic_hint": board.get("logic_hint", ""),
            "leading_stocks": [
                {
                    "name": s.get("name", ""),
                    "code": s.get("code", ""),
                    "change_pct": s.get("change_pct", 0),
                    "main_net_yi": s.get("main_net_yi", 0),
                    "turnover_rate": s.get("turnover_rate", 0),
                    "volume_ratio": s.get("volume_ratio", 0),
                }
                for s in board.get("leading_stocks", [])[:5]
            ],
        })
    return compact


def _write_json(payload: dict) -> None:
    (DATA_DIR / "dashboard.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _fmt_num(value, suffix: str = "", digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "-"


def _action_class(action: str) -> str:
    if "加仓" in action:
        return "add"
    if "建仓" in action:
        return "build"
    if "观察" in action:
        return "watch"
    return "avoid"


def _render_board_rows(boards: list[dict]) -> str:
    rows = []
    for b in boards:
        action = b.get("action") or b.get("level") or "-"
        rows.append(
            "<tr>"
            f"<td><span class='badge {_action_class(action)}'>{action}</span></td>"
            f"<td><strong>{b.get('name', '-')}</strong><small>{b.get('code', '')}</small></td>"
            f"<td>{_fmt_num(b.get('change_pct'), '%')}</td>"
            f"<td>{_fmt_num(b.get('main_net_yi'), '亿')}</td>"
            f"<td>{_fmt_num(b.get('main_net_ratio'), '%')}</td>"
            f"<td>{_fmt_num(b.get('up_ratio'), '%', 1)}</td>"
            f"<td>{b.get('leader_name', '-')} <small>{_fmt_num(b.get('leader_change_pct'), '%')}</small></td>"
            f"<td>{_fmt_num(b.get('signal_score'), '', 1)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _render_cards(boards: list[dict]) -> str:
    cards = []
    for b in boards[:6]:
        stocks = "".join(
            f"<li>{s.get('name')} <span>{_fmt_num(s.get('change_pct'), '%')} / {_fmt_num(s.get('main_net_yi'), '亿')}</span></li>"
            for s in b.get("leading_stocks", [])[:4]
        )
        action = b.get("action") or b.get("level") or "-"
        cards.append(
            f"<article class='card'>"
            f"<div class='card-head'><h3>{b.get('name')}</h3><span class='badge {_action_class(action)}'>{action}</span></div>"
            f"<p>{b.get('logic_hint', '')}</p>"
            f"<ul>{stocks}</ul>"
            f"</article>"
        )
    return "\n".join(cards)


def _render_reports(reports: dict) -> str:
    if not reports:
        return "<p class='muted'>暂无历史报告。</p>"
    items = []
    order = ["market_signal", "sector_signal", "stocks", "research", "market", "sectors", "summary"]
    for key in order:
        report = reports.get(key)
        if not report:
            continue
        items.append(
            f"<a class='report-link' href='{report['path']}' target='_blank'>"
            f"<span>{report['title']}</span><small>{key}</small></a>"
        )
    return "\n".join(items)


def _write_html(payload: dict) -> None:
    market = payload["market"]
    rows = _render_board_rows(payload["market_boards"])
    cards = _render_cards(payload["market_boards"])
    reports = _render_reports(payload["reports"])
    data_json = json.dumps(payload, ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>A股信息聚合看板</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d9e0ea;
      --red: #c9362f;
      --green: #168a53;
      --blue: #2864c7;
      --amber: #a96500;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--ink); }}
    header {{ padding: 28px 24px 18px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    main {{ max-width: 1240px; margin: 0 auto; padding: 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 0 0 14px; font-size: 20px; }}
    h3 {{ margin: 0; font-size: 16px; }}
    p {{ margin: 0; line-height: 1.65; }}
    .topline {{ max-width: 1240px; margin: 0 auto; }}
    .muted, small {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 22px; }}
    .metric, section, .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    .metric {{ padding: 16px; }}
    .metric b {{ display: block; margin-top: 8px; font-size: 24px; }}
    section {{ padding: 18px; margin-bottom: 18px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 11px 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    td small {{ display: block; margin-top: 3px; }}
    .badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; white-space: nowrap; }}
    .add {{ background: #ffe8e5; color: var(--red); }}
    .build {{ background: #fff2d8; color: var(--amber); }}
    .watch {{ background: #e7f0ff; color: var(--blue); }}
    .avoid {{ background: #e9f7ef; color: var(--green); }}
    .cards {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
    .card {{ padding: 16px; }}
    .card-head {{ display: flex; justify-content: space-between; gap: 10px; align-items: center; margin-bottom: 10px; }}
    .card p {{ color: var(--muted); font-size: 14px; min-height: 44px; }}
    ul {{ margin: 12px 0 0; padding-left: 18px; }}
    li {{ margin: 6px 0; }}
    li span {{ color: var(--muted); }}
    .reports {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }}
    .report-link {{ display: flex; justify-content: space-between; gap: 8px; padding: 12px; border: 1px solid var(--line); border-radius: 8px; color: var(--ink); text-decoration: none; background: #fafbfd; min-width: 0; }}
    .report-link span {{ min-width: 0; overflow-wrap: anywhere; }}
    .report-link small {{ flex: 0 0 auto; }}
    .report-link:hover {{ border-color: #98a9c2; }}
    footer {{ padding: 18px 24px 32px; color: var(--muted); text-align: center; }}
    @media (max-width: 900px) {{
      .grid, .cards, .reports {{ grid-template-columns: 1fr; }}
      main {{ padding: 14px; }}
      table {{ font-size: 13px; }}
      th:nth-child(5), td:nth-child(5), th:nth-child(8), td:nth-child(8) {{ display: none; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="topline">
      <h1>A股信息聚合看板</h1>
      <p class="muted">更新时间：{payload['generated_at_display']} ｜ 数据自动刷新，页面可手动刷新查看最新版本。</p>
    </div>
  </header>
  <main>
    <div class="grid">
      <div class="metric"><span>大盘环境</span><b>{market.get('level', '-')}</b></div>
      <div class="metric"><span>大盘评分</span><b>{market.get('score', '-')} / 100</b></div>
      <div class="metric"><span>建议仓位上限</span><b>{market.get('position_ceiling', '-')}</b></div>
      <div class="metric"><span>上涨家数占比</span><b>{_fmt_num(market.get('up_ratio'), '%', 1)}</b></div>
    </div>

    <section>
      <h2>建仓 / 加仓信号</h2>
      <table>
        <thead><tr><th>动作</th><th>板块</th><th>涨幅</th><th>主力净流入</th><th>净占比</th><th>上涨占比</th><th>领涨股</th><th>信号分</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </section>

    <section>
      <h2>主线卡片</h2>
      <div class="cards">{cards}</div>
    </section>

    <section>
      <h2>最新报告</h2>
      <div class="reports">{reports}</div>
    </section>

    <section>
      <h2>说明</h2>
      <p class="muted">{payload['disclaimer']} GitHub Pages 是静态站点，实时性取决于 GitHub Actions 的刷新频率和调度延迟。</p>
    </section>
  </main>
  <footer>Generated by zsxq-stock-report</footer>
  <script id="dashboard-data" type="application/json">{data_json}</script>
</body>
</html>
"""
    (SITE_DIR / "index.html").write_text(html, encoding="utf-8")


def main() -> None:
    _ensure_dirs()
    payload = _build_payload()
    _write_json(payload)
    _write_html(payload)
    print(f"Dashboard built: {SITE_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
