#!/usr/bin/env python3
"""Web 仪表盘 — Flask 服务端。

提供 REST API 和 Web UI，包装所有 CLI 功能为可视化界面。
启动: python3 dashboard.py
访问: http://localhost:8501
"""

import json
import os
import sys
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, render_template, jsonify, request, send_from_directory

app = Flask(__name__, template_folder="templates", static_folder="static")

# 全局错误处理：确保所有响应都是 JSON
@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": str(e), "type": type(e).__name__}), 500

@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "接口不存在"}), 404

@app.errorhandler(500)
def handle_500(e):
    return jsonify({"error": "服务器内部错误"}), 500

PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"

# ═══════════════════════════════════════════════════════════════
# 鉴权 — 所有写操作必须携带 DASHBOARD_TOKEN
# ═══════════════════════════════════════════════════════════════
import hashlib
import hmac

def _get_dashboard_token() -> str:
    """获取仪表盘访问令牌。优先级: 环境变量 > 配置文件 > 默认值(仅开发)。"""
    token = os.environ.get("DASHBOARD_TOKEN", "").strip()
    if token:
        return token
    # 开发环境默认 token — 生产环境必须通过环境变量覆盖
    return "dev-only-change-me"

DASHBOARD_TOKEN = _get_dashboard_token()
WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

def _check_auth():
    """校验写操作的 Bearer <REDACTED>。"""
    if request.method not in WRITE_METHODS:
        return None  # GET 请求无需鉴权（只读）
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        provided = auth_header[7:].strip()
        if hmac.compare_digest(provided, DASHBOARD_TOKEN):
            return None
    # 也支持查询参数 ?token=xxx（方便 curl 调试）
    if request.args.get("token") and hmac.compare_digest(request.args.get("token"), DASHBOARD_TOKEN):
        return None
    return jsonify({"error": "未授权。请在请求头添加 Authorization: Bearer <token>"}), 401

# 注册鉴权中间件
@app.before_request
def auth_middleware():
    result = _check_auth()
    if result is not None:
        return result

# 确保项目目录在 Python 路径中
sys.path.insert(0, str(PROJECT_DIR))

# 检查可选依赖
try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import yaml as _yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def _run_cmd(cmd: list[str], timeout: int = 120) -> dict:
    """执行命令并返回结果。"""
    try:
        # 确保环境变量传递
        env = {**os.environ}
        if "PYTHONPATH" not in env:
            env["PYTHONPATH"] = str(PROJECT_DIR)

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(PROJECT_DIR), env=env,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-5000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "命令超时"}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e)}


# ═══════════════════════════════════════════════════════════════
# 页面路由
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


# ═══════════════════════════════════════════════════════════════
# API: 股票报告
# ═══════════════════════════════════════════════════════════════

@app.route("/api/stocks", methods=["POST"])
def api_stocks():
    """生成股票报告。先检查是否有爬取数据，没有则自动爬取。"""
    try:
        data = request.json or {}
        max_posts = data.get("max_posts", 0)
        group_url = data.get("url", "")

        # 检查是否有已爬取的数据
        raw_dir = DATA_DIR / "raw"
        has_data = raw_dir.exists() and any(raw_dir.glob("*.json"))

        if not has_data and not group_url:
            # 尝试从 config.yaml 读取默认 URL
            config_path = PROJECT_DIR / "config.yaml"
            if config_path.exists():
                try:
                    import yaml
                    config = yaml.safe_load(config_path.read_text()) or {}
                    group_url = config.get("zsxq_group_url", "")
                except Exception:
                    pass

        if not has_data:
            if not group_url:
                return jsonify({
                    "success": False,
                    "stdout": "",
                    "stderr": "暂无爬取数据。请在输入框中填写专栏 URL 后重试，或使用命令行：\npython3 main.py crawl <专栏URL>",
                })
            # 先爬取
            crawl_cmd = [sys.executable, "main.py", "crawl", group_url]
            if max_posts > 0:
                crawl_cmd += ["-n", str(max_posts)]
            _run_cmd(crawl_cmd, timeout=300)

        # 生成报告
        result = _run_cmd([sys.executable, "main.py", "stocks"], timeout=300)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "stdout": "", "stderr": str(e)})


@app.route("/api/report/latest")
def api_latest_report():
    """获取最新报告。"""
    summary_dir = DATA_DIR / "summary"
    if not summary_dir.exists():
        return jsonify({"content": "暂无报告，请先生成股票报告。"})
    files = sorted(summary_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return jsonify({"content": "暂无 Markdown 报告。"})
    content = files[0].read_text(encoding="utf-8")
    return jsonify({"content": content, "file": files[0].name})


@app.route("/api/enriched/latest")
def api_latest_enriched():
    """获取最新增强股票数据（JSON）。"""
    summary_dir = DATA_DIR / "summary"
    if not summary_dir.exists():
        return jsonify([])
    files = sorted(
        summary_dir.glob("*_enriched_*.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not files:
        return jsonify([])
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        return jsonify(data)
    except Exception:
        return jsonify([])


# ═══════════════════════════════════════════════════════════════
# API: 回测与绩效
# ═══════════════════════════════════════════════════════════════

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    """运行回测。"""
    result = _run_cmd([sys.executable, "main.py", "backtest"], timeout=120)
    return jsonify(result)


@app.route("/api/performance", methods=["POST"])
def api_performance():
    """绩效追踪。"""
    result = _run_cmd([sys.executable, "main.py", "performance"], timeout=120)
    return jsonify(result)


@app.route("/api/benchmark", methods=["POST"])
def api_benchmark():
    """基准对比。"""
    result = _run_cmd([sys.executable, "main.py", "benchmark"], timeout=120)
    return jsonify(result)


@app.route("/api/factor-research", methods=["POST"])
def api_factor_research():
    """因子研究。"""
    result = _run_cmd([sys.executable, "main.py", "factor-research"], timeout=120)
    return jsonify(result)


@app.route("/api/adaptive-weights", methods=["POST"])
def api_adaptive_weights():
    """自适应权重。"""
    result = _run_cmd([sys.executable, "main.py", "adaptive-weights"], timeout=120)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════
# API: 组合与交易
# ═══════════════════════════════════════════════════════════════

@app.route("/api/paper/status")
def api_paper_status():
    """模拟组合状态。"""
    if not HAS_REQUESTS:
        return jsonify({"error": "未安装 requests 模块", "total_value": 1000000, "nav": 1.0, "cash": 1000000, "cash_pct": 100, "total_pnl": 0, "total_pnl_pct": 0, "holdings": []})
    try:
        from paper_trader import get_portfolio_summary
        summary = get_portfolio_summary()
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e), "total_value": 1000000, "nav": 1.0, "cash": 1000000, "cash_pct": 100, "total_pnl": 0, "total_pnl_pct": 0, "holdings": []})


@app.route("/api/paper/trade", methods=["POST"])
def api_paper_trade():
    """执行模拟交易。"""
    result = _run_cmd([sys.executable, "main.py", "paper-trade"], timeout=120)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════
# API: 市场数据
# ═══════════════════════════════════════════════════════════════

@app.route("/api/market/sentiment")
def api_market_sentiment():
    """市场情绪。"""
    if not HAS_REQUESTS:
        return jsonify({"error": "未安装 requests 模块", "score": 50, "signal": "数据不可用"})
    try:
        from price_fetcher import fetch_margin_data
        margin = fetch_margin_data()
        # 综合情绪：只用融资数据
        score = 50.0
        if margin.get("margin_change", 0) > 5:
            score += 15
        elif margin.get("margin_change", 0) < -5:
            score -= 15
        score = max(0, min(100, score))
        signal = "资金面偏多" if score >= 65 else ("资金面偏空" if score <= 35 else "资金面中性")
        return jsonify({
            "score": round(score, 1),
            "signal": signal,
            "margin": margin,
        })
    except Exception as e:
        return jsonify({"error": str(e), "score": 50, "signal": "数据加载失败"})


@app.route("/api/market/regime")
def api_market_regime():
    """市场状态。"""
    if not HAS_REQUESTS:
        return jsonify({"error": "未安装 requests 模块", "regime": "neutral", "label": "数据不可用", "score": 50, "signals": {}})
    try:
        from market_regime import detect_market_regime
        from price_fetcher import fetch_market_environment
        ext = fetch_market_environment()
        regime = detect_market_regime(external_market=ext)
        return jsonify(regime)
    except Exception as e:
        return jsonify({"error": str(e), "regime": "neutral", "label": "加载失败", "score": 50, "signals": {}})


@app.route("/api/prices", methods=["POST"])
def api_prices():
    """获取实时行情。"""
    data = request.json or {}
    codes = data.get("codes", [])
    if not codes:
        return jsonify({})
    if not HAS_REQUESTS:
        return jsonify({"error": "未安装 requests 模块"})
    try:
        from price_fetcher import fetch_prices
        prices = fetch_prices(codes)
        return jsonify(prices)
    except Exception as e:
        return jsonify({"error": str(e)})


# ═══════════════════════════════════════════════════════════════
# API: 复盘
# ═══════════════════════════════════════════════════════════════

@app.route("/api/review", methods=["POST"])
def api_review():
    """盘后复盘。"""
    result = _run_cmd([sys.executable, "main.py", "review"], timeout=180)
    return jsonify(result)


@app.route("/api/consec", methods=["POST"])
def api_consec():
    """连板扫描。"""
    result = _run_cmd([sys.executable, "main.py", "consec"], timeout=120)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════
# API: 爬虫
# ═══════════════════════════════════════════════════════════════

@app.route("/api/crawl", methods=["POST"])
def api_crawl():
    """增量爬取。"""
    data = request.json or {}
    url = data.get("url", "")
    max_posts = data.get("max_posts", 0)
    if not url:
        return jsonify({"success": False, "stderr": "请提供专栏 URL"})
    cmd = [sys.executable, "main.py", "crawl", url]
    if max_posts > 0:
        cmd += ["-n", str(max_posts)]
    result = _run_cmd(cmd, timeout=300)
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════
# API: 数据文件
# ═══════════════════════════════════════════════════════════════

@app.route("/api/files")
def api_files():
    """列出数据文件。"""
    files = []
    for d in ["summary", "raw", "state", "attribution", "factor_research", "paper_trading"]:
        dir_path = DATA_DIR / d
        if dir_path.exists():
            for f in sorted(dir_path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
                files.append({
                    "dir": d,
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })
    return jsonify(files)


@app.route("/api/data/<path:filepath>")
def api_data_file(filepath):
    """读取数据文件内容。"""
    full_path = DATA_DIR / filepath
    if not full_path.exists():
        return jsonify({"error": "文件不存在"})
    try:
        content = full_path.read_text(encoding="utf-8")
        if full_path.suffix == ".json":
            return jsonify(json.loads(content))
        return jsonify({"content": content})
    except Exception as e:
        return jsonify({"error": str(e)})


# ═══════════════════════════════════════════════════════════════
# API: 系统状态
# ═══════════════════════════════════════════════════════════════

@app.route("/api/status")
def api_status():
    """系统状态概览。"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    summary_dir = DATA_DIR / "summary"

    # 最新报告时间
    latest_report = None
    if summary_dir.exists():
        md_files = sorted(summary_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if md_files:
            latest_report = datetime.fromtimestamp(md_files[0].stat().st_mtime).isoformat()

    # 推荐历史数量
    history_file = DATA_DIR / "summary" / "history" / "recommendations.jsonl"
    history_count = 0
    if history_file.exists():
        with open(history_file) as f:
            history_count = sum(1 for _ in f)

    # 模拟组合状态
    paper_value = None
    try:
        sys.path.insert(0, str(PROJECT_DIR))
        from paper_trader import get_portfolio_summary
        ps = get_portfolio_summary()
        paper_value = ps.get("total_value")
    except Exception:
        pass

    return jsonify({
        "server_time": now.isoformat(),
        "latest_report": latest_report,
        "history_count": history_count,
        "paper_portfolio_value": paper_value,
    })


if __name__ == "__main__":
    print("=" * 60)
    print("  📊 知识星球股票分析仪表盘")
    print("  访问: http://localhost:8501")
    print("=" * 60)
    app.run(host="0.0.0.0", port=8501, debug=True)
