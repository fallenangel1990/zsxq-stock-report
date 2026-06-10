"""报告 PDF 生成模块。

将 Markdown 格式的股票报告通过 Jinja2 模板渲染为 PDF，
支持中文排版、复杂表格和分页控制。

PDF 后端（自动选择）：
- WeasyPrint（优先）：轻量，CSS 分页控制好，需要系统 GTK/Pango 库
- Playwright（回退）：浏览器渲染，中文支持完美，Chromium 已随项目安装
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import jinja2
import markdown


# ── PDF 后端检测 ──

_PDF_BACKEND = None  # "weasyprint" | "playwright" | None


def _now_shanghai() -> datetime:
    """返回北京时间当前时间，用于报告中展示的生成时间。"""
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _get_pdf_backend() -> str:
    """检测可用的 PDF 后端并返回名称。"""
    global _PDF_BACKEND
    if _PDF_BACKEND is not None:
        return _PDF_BACKEND

    # 优先 WeasyPrint
    try:
        from weasyprint import HTML  # noqa: F401
        _PDF_BACKEND = "weasyprint"
        return _PDF_BACKEND
    except ImportError:
        pass
    except OSError:
        # WeasyPrint 已安装但缺少系统库（如 macOS 未装 pango）
        print("[PDF] WeasyPrint 系统库缺失，回退到 Playwright")

    # 回退 Playwright
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        _PDF_BACKEND = "playwright"
        return _PDF_BACKEND
    except ImportError:
        pass

    _PDF_BACKEND = None
    raise RuntimeError(
        "无可用的 PDF 后端。请安装以下任一：\n"
        "  1. WeasyPrint: pip install weasyprint + brew install pango (macOS)\n"
        "  2. Playwright: pip install playwright && playwright install chromium"
    )


# ── 中文字体自动检测 ──

def _detect_chinese_font() -> str:
    """自动检测系统中可用的中文字体路径。

    优先级：环境变量 CHINESE_FONT_PATH > macOS STHeiti > Linux Noto CJK > 其他常见字体。

    Returns:
        字体文件路径或 CSS font-family 名称。
    """
    env_font = os.environ.get("CHINESE_FONT_PATH", "")
    if env_font and Path(env_font).exists():
        return env_font

    # macOS
    mac_fonts = [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for f in mac_fonts:
        if Path(f).exists():
            return f

    # Linux (GitHub Actions / 服务器)
    linux_fonts = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
    ]
    for f in linux_fonts:
        if Path(f).exists():
            return f

    # 回退：中文字体族名
    return "STHeiti, Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif"


_CHINESE_FONT = _detect_chinese_font()
_USE_FONT_PATH = _CHINESE_FONT and "/" in _CHINESE_FONT


# ── Jinja2 环境 ──

_TEMPLATE_DIR = Path(__file__).parent
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(_TEMPLATE_DIR),
    autoescape=False,
)

# CSS 基础样式
_BASE_CSS = r"""
@page {
    size: A4;
    margin: 1.6cm 1.8cm 2.0cm 1.8cm;
    @bottom-center {
        content: "— " counter(page) " —";
        font-size: 9pt;
        color: #888;
        font-family: __FONT_FAMILY__;
    }
}

@page :first {
    @top-center {
        content: string(page_title);
        font-size: 10pt;
        color: #666;
        font-family: __FONT_FAMILY__;
    }
}

body {
    font-family: __FONT_FAMILY__;
    font-size: 10.5pt;
    line-height: 1.7;
    color: #222;
}

h1 {
    font-size: 18pt;
    text-align: center;
    margin: 0.4cm 0 0.2cm 0;
    padding-bottom: 6px;
    border-bottom: 2px solid #2563eb;
    string-set: page_title content();
}
h2 {
    font-size: 14pt;
    margin: 0.8cm 0 0.3cm 0;
    padding: 4px 0;
    border-left: 4px solid #2563eb;
    padding-left: 10px;
}
h3 {
    font-size: 12pt;
    margin: 0.5cm 0 0.2cm 0;
}
h4 {
    font-size: 11pt;
    margin: 0.3cm 0 0.15cm 0;
}

p { margin: 0.15cm 0; }

/* 表格 — 核心样式 */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.3cm 0 0.5cm 0;
    font-size: 8.5pt;
    word-break: break-all;
}
th {
    background-color: #2563eb;
    color: white;
    padding: 6px 4px;
    text-align: center;
    font-weight: bold;
    white-space: nowrap;
}
td {
    padding: 4px 4px;
    border: 1px solid #d1d5db;
    vertical-align: top;
}
tr:nth-child(even) td {
    background-color: #f8fafc;
}
tr {
    page-break-inside: avoid;
}

.stars { color: #f59e0b; font-weight: bold; white-space: nowrap; }
.upside-positive { color: #dc2626; font-weight: bold; }
.upside-na { color: #9ca3af; }

hr {
    border: none;
    border-top: 1px solid #e5e7eb;
    margin: 0.5cm 0;
}

.report-meta {
    text-align: center;
    color: #888;
    font-size: 9pt;
    margin-bottom: 0.3cm;
}

.risk-note {
    background: #fef3c7;
    border: 1px solid #f59e0b;
    border-radius: 4px;
    padding: 8px 12px;
    margin: 0.4cm 0;
    font-size: 9pt;
}

.wide-table table { font-size: 7.5pt; }
.wide-table th, .wide-table td { padding: 3px 2px; }
"""


_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{{ title }}</title>
<style>
{{ css }}
</style>
</head>
<body>

{% if show_title %}
<h1>{{ title }}</h1>
<div class="report-meta">
    生成时间：{{ generated_at }}<br>
    数据来源：知识星球专栏 · 水木调研纪要
</div>
{% endif %}

{{ body }}

{% if show_disclaimer %}
<hr>
<div class="risk-note">
    <strong>⚠️ 免责声明</strong><br>
    本报告由 AI 自动生成，仅供参考，不构成任何投资建议。股市有风险，投资需谨慎。
    所有股票信息来源于知识星球公开帖子，目标价/估值预测不代表未来实际表现。
    请结合自身风险承受能力，独立作出投资决策。
</div>
{% endif %}

</body>
</html>
"""


def _build_css() -> str:
    """构建完整的 CSS 样式，包含字体声明。"""
    font_family = _CHINESE_FONT if not _USE_FONT_PATH else "CustomChineseFont"
    css = _BASE_CSS.replace("__FONT_FAMILY__", font_family)

    if _USE_FONT_PATH:
        font_face = f"""
@font-face {{
    font-family: 'CustomChineseFont';
    src: url('file://{_CHINESE_FONT}') format('truetype');
    font-weight: normal;
    font-style: normal;
}}
"""
        css = font_face + css

    return css


def _build_html(markdown_text: str, title: str = "股票机会提取报告") -> str:
    """将 Markdown 文本转换为完整 HTML 字符串。"""
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "codehilite", "nl2br"],
        extension_configs={"codehilite": {"css_class": "highlight"}},
    )
    body_html = md.convert(markdown_text)

    css = _build_css()
    template = jinja2.Template(_REPORT_TEMPLATE)
    return template.render(
        title=title,
        css=css,
        body=body_html,
        generated_at=_now_shanghai().strftime("%Y-%m-%d %H:%M 北京时间"),
        show_title=True,
        show_disclaimer=True,
    )


# ── PDF 生成：WeasyPrint 后端 ──

def _weasyprint_render(html: str, output_path: str) -> None:
    from weasyprint import HTML
    HTML(string=html).write_pdf(output_path, presentational_hints=True)


# ── PDF 生成：Playwright 后端 ──

def _playwright_render(html: str, output_path: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=output_path,
            format="A4",
            margin={"top": "1.6cm", "bottom": "2.0cm", "left": "1.8cm", "right": "1.8cm"},
            print_background=True,
            display_header_footer=True,
            header_template=(
                '<div style="font-size:10pt;color:#666;text-align:center;'
                'font-family:sans-serif;width:100%;padding-top:0.5cm;"></div>'
            ),
            footer_template=(
                '<div style="font-size:9pt;color:#888;text-align:center;'
                'font-family:sans-serif;width:100%;">'
                '— <span class="pageNumber"></span> —</div>'
            ),
        )
        browser.close()


# ── 公共 API ──

def generate_pdf(
    markdown_text: str,
    output_path: Optional[str] = None,
    title: str = "股票机会提取报告",
) -> str:
    """将 Markdown 报告渲染为 PDF 文件。

    自动选择可用后端（WeasyPrint 优先，Playwright 回退）。

    Args:
        markdown_text: Markdown 格式的报告内容。
        output_path: PDF 输出路径，默认为 data/summary/{title}_{timestamp}.pdf。
        title: 报告标题。

    Returns:
        PDF 文件路径。
    """
    if output_path is None:
        out_dir = Path(__file__).parent / "data" / "summary"
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = title.replace(" ", "_").replace("/", "_")
        output_path = str(out_dir / f"{safe_title}_{date_str}.pdf")

    backend = _get_pdf_backend()
    print(f"[PDF] 后端: {backend} | 字体: {_CHINESE_FONT}")
    html = _build_html(markdown_text, title)

    if backend == "weasyprint":
        _weasyprint_render(html, output_path)
    elif backend == "playwright":
        _playwright_render(html, output_path)
    else:
        raise RuntimeError("无可用的 PDF 后端")

    size_kb = Path(output_path).stat().st_size / 1024
    print(f"[PDF] 已生成: {output_path} ({size_kb:.1f} KB)")
    return output_path


def generate_pdf_from_file(
    markdown_path: str,
    output_path: Optional[str] = None,
    title: str = "股票机会提取报告",
) -> str:
    """从 Markdown 文件生成 PDF。

    Args:
        markdown_path: Markdown 报告文件路径。
        output_path: PDF 输出路径，默认与源文件同目录同名的 .pdf。
        title: 报告标题。

    Returns:
        PDF 文件路径。
    """
    text = Path(markdown_path).read_text(encoding="utf-8")
    if output_path is None:
        output_path = str(Path(markdown_path).with_suffix(".pdf"))
    return generate_pdf(text, output_path, title)


# ── CLI ──

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 pdf_generator.py <markdown文件路径> [输出pdf路径]")
        print(f"当前可用后端: {_get_pdf_backend()}")
        print(f"中文字体: {_CHINESE_FONT}")
        sys.exit(1)

    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else None

    if not Path(src).exists():
        print(f"错误: 文件不存在 — {src}")
        sys.exit(1)

    generate_pdf_from_file(src, dst)
