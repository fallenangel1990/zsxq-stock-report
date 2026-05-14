"""邮件发送模块。

通过 QQ 邮箱 SMTP 发送 HTML 格式报告邮件。
凭证通过环境变量配置，支持 GitHub Actions Secrets。
将 Markdown 报告全文转换为 HTML 嵌入邮件正文，无需附件。
"""

import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import markdown as md_lib


# ── 默认配置 ──

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
TO_EMAIL = os.environ.get("TO_EMAIL", "470337944@qq.com")


def _build_message(
    to_email: str,
    subject: str,
    body_html: str,
) -> MIMEMultipart:
    """构建纯 HTML MIME 邮件（无附件）。

    Args:
        to_email: 收件人邮箱。
        subject: 邮件主题。
        body_html: HTML 格式的邮件正文。

    Returns:
        MIMEMultipart 邮件对象。
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")

    # 正文（纯 HTML，无附件）
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    return msg


def send_email(
    to_email: str = "",
    subject: str = "",
    body_html: str = "",
    smtp_host: str = "",
    smtp_port: int = 0,
) -> bool:
    """发送 HTML 格式邮件（无附件）。

    凭证从环境变量 SMTP_USER / SMTP_PASS 读取。

    Args:
        to_email: 收件人邮箱，默认使用环境变量 TO_EMAIL。
        subject: 邮件主题。
        body_html: HTML 邮件正文。
        smtp_host: SMTP 服务器，默认 smtp.qq.com。
        smtp_port: SMTP 端口，默认 465。

    Returns:
        True 表示发送成功。

    Raises:
        ValueError: 缺少 SMTP 凭证。
        smtplib.SMTPException: SMTP 通信失败。
    """
    if not SMTP_USER or not SMTP_PASS:
        raise ValueError(
            "缺少 SMTP 凭证。请设置环境变量 SMTP_USER 和 SMTP_PASS。\n"
            "  SMTP_USER: QQ 邮箱地址\n"
            "  SMTP_PASS: QQ 邮箱授权码（非密码，在 smtp.qq.com 设置中生成）"
        )

    to = to_email or TO_EMAIL
    host = smtp_host or SMTP_HOST
    port = smtp_port or SMTP_PORT
    today = datetime.now().strftime("%Y-%m-%d")

    if not subject:
        subject = f"每日报告 {today}"

    if not body_html:
        body_html = f"""\
<html>
<body style="font-family: sans-serif;">
<h2>📊 每日报告</h2>
<p>日期：{today}</p>
<p>报告内容为空。</p>
<hr>
<p style="color:#888;font-size:12px;">
本邮件由自动化系统发送，请勿回复。<br>
如需停止接收，请联系管理员。
</p>
</body>
</html>"""

    print(f"[邮件] 发送至 {to} ...")

    msg = _build_message(to, subject, body_html)

    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [to], msg.as_string())

    print(f"[邮件] 发送成功 → {to}")
    return True


def _md_to_html(md_text: str) -> str:
    """将 Markdown 报告全文转换为内联样式的 HTML（兼容邮件客户端）。

    使用 markdown 库解析，然后给表格、标题等标签加上内联样式，
    确保在 QQ 邮箱 / Gmail 等客户端中正常显示。
    """
    # 使用 markdown 库转换（tables 扩展处理管道表格）
    html = md_lib.markdown(md_text, extensions=["tables", "fenced_code"])

    # 表格样式（内联，兼容邮件客户端）
    html = html.replace(
        "<table>",
        '<table style="border-collapse:collapse;width:100%;margin:12px 0;font-size:13px;">',
    )
    html = html.replace(
        "<thead>",
        '<thead style="background:#2563eb;color:white;">',
    )
    html = html.replace(
        "<th>",
        '<th style="padding:6px 8px;text-align:center;border:1px solid #1d4ed8;">',
    )
    html = html.replace(
        "<td>",
        '<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;text-align:center;">',
    )

    # 标题层级调整（报告已有 h1，邮件中降一级）
    html = html.replace("<h1>", '<h2 style="color:#1e3a5f;margin-top:8px;">')
    html = html.replace("</h1>", "</h2>")
    html = html.replace("<h2>", '<h3 style="color:#2563eb;margin-top:20px;">')
    html = html.replace("</h2>", "</h3>")

    # 引用块样式
    html = html.replace(
        "<blockquote>",
        '<blockquote style="border-left:3px solid #2563eb;padding-left:12px;color:#555;margin:8px 0;">',
    )

    # 水平线
    html = html.replace("<hr>", '<hr style="border:0;border-top:1px solid #e5e7eb;margin:16px 0;">')
    html = html.replace("<hr />", '<hr style="border:0;border-top:1px solid #e5e7eb;margin:16px 0;">')

    return html


def _weekday_cn() -> str:
    """返回当前星期几的中文名称。"""
    return ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][
        datetime.now().weekday()
    ]


def send_report_notification(
    markdown_path: str,
    to_email: str = "",
    extra_info: Optional[dict] = None,
) -> bool:
    """发送报告通知邮件（HTML 正文，无附件）。

    将 Markdown 报告全文转换为 HTML 嵌入邮件正文。

    Args:
        markdown_path: Markdown 报告文件路径。
        to_email: 收件人邮箱。
        extra_info: 额外信息字典：
            - total_posts: 本次处理帖子数（有新增时为新增数，无新增时为兜底总结数）
            - new_stocks: 新发现股票数
            - cookie_expired: cookie 是否过期
            - cookie_warning: cookie 是否即将过期
            - cookie_days: cookie 剩余天数
    """
    today = datetime.now().strftime("%Y-%m-%d")
    extra_info = extra_info or {}

    # 读取 Markdown 并转换为 HTML
    md_path = markdown_path
    if Path(md_path).exists():
        md_text = Path(md_path).read_text(encoding="utf-8")
        report_html = _md_to_html(md_text)
    else:
        report_html = '<p style="color:#dc2626;">报告文件不存在，请检查 GitHub Actions 运行日志。</p>'

    # 构建带样式的完整邮件正文
    lines = [
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;max-width:800px;">',
        # 头部横幅
        f'<div style="background:#2563eb;color:white;padding:16px 20px;border-radius:8px 8px 0 0;">',
        f'<h2 style="margin:0;font-size:18px;">📊 每日报告</h2>',
        f'<p style="margin:4px 0 0;opacity:0.85;font-size:13px;">{today} {_weekday_cn()}</p>',
        f"</div>",
    ]

    # 统计条
    stats_parts = []
    if extra_info.get("total_posts"):
        stats_parts.append(f'📬 处理帖子：<strong>{extra_info["total_posts"]}</strong> 篇')
    if extra_info.get("new_stocks"):
        stats_parts.append(f'🎯 发现标的：<strong>{extra_info["new_stocks"]}</strong> 只')
    if stats_parts:
        lines.append(
            '<div style="background:#f0f7ff;padding:10px 20px;border-left:4px solid #2563eb;">'
            + " &nbsp;│&nbsp; ".join(stats_parts)
            + "</div>"
        )

    # Cookie 预警
    if extra_info.get("cookie_expired"):
        lines.append(
            '<div style="background:#fef2f2;padding:10px 20px;border-left:4px solid #dc2626;margin:4px 0;">'
            "⚠️ <strong>Cookie 已过期！</strong>请本地运行 <code>python main.py login</code> 重新登录。"
            "</div>"
        )
    elif extra_info.get("cookie_warning"):
        days = extra_info.get("cookie_days", "")
        expires = extra_info.get("cookie_expires", "")
        lines.append(
            '<div style="background:#fffbeb;padding:10px 20px;border-left:4px solid #f59e0b;margin:4px 0;">'
            f"⚠️ <strong>Cookie 将在 {days} 天后过期</strong>（{expires}），请提前更新。"
            "</div>"
        )

    # 报告主体
    lines.append('<div style="padding:8px 20px 0;">')
    lines.append(report_html)
    lines.append("</div>")

    # 页脚
    lines.append(
        '<div style="color:#888;font-size:12px;padding:16px 20px;'
        'border-top:1px solid #e5e7eb;margin-top:16px;">'
        "本邮件由自动化系统发送。<br>"
        "报告基于知识星球专栏内容，由 AI 自动生成，仅供参考。"
        "</div>"
    )
    lines.append("</div>")

    body_html = "\n".join(lines)

    # 邮件主题
    subject = f"📊 每日报告 {today}"
    if extra_info.get("cookie_expired"):
        subject = f"[需重新登录] {subject}"
    elif extra_info.get("cookie_warning"):
        subject = f"[Cookie 即将过期] {subject}"

    return send_email(
        to_email=to_email,
        subject=subject,
        body_html=body_html,
    )


def send_error_email(
    error_msg: str,
    to_email: str = "",
    step: str = "",
) -> bool:
    """发送错误通知邮件。

    Args:
        error_msg: 错误信息。
        to_email: 收件人邮箱。
        step: 失败的步骤名称。
    """
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    step_info = f"（步骤：{step}）" if step else ""

    body = f"""\
<html>
<body style="font-family: sans-serif;">
<h2>⚠️ 报告生成失败</h2>
<p><strong>时间：</strong>{today}</p>
<p><strong>失败步骤：</strong>{step or '未知'}</p>
<p><strong>错误信息：</strong></p>
<pre style="background:#fef2f2;padding:12px;border-radius:4px;color:#dc2626;">{error_msg}</pre>
<p>请检查 <a href="https://github.com/fallenangel1990/zsxq-stock-report/actions">GitHub Actions</a> 的详细日志。</p>
<hr>
<p style="color:#888;font-size:12px;">本邮件由自动化系统发送。</p>
</body>
</html>"""

    subject = f"❌ 报告异常 {step_info}"
    return send_email(to_email=to_email, subject=subject, body_html=body)


# ── CLI ──

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 email_sender.py <markdown文件路径> [收件人邮箱]")
        print()
        print("环境变量:")
        print("  SMTP_USER  — QQ 邮箱地址")
        print("  SMTP_PASS  — QQ 邮箱授权码")
        print("  TO_EMAIL   — 默认收件人（可选）")
        sys.exit(1)

    md_path = sys.argv[1]
    to_email = sys.argv[2] if len(sys.argv) > 2 else ""

    if not Path(md_path).exists():
        print(f"错误: 文件不存在 — {md_path}")
        sys.exit(1)

    try:
        send_report_notification(md_path, to_email)
    except ValueError as e:
        print(f"配置错误: {e}")
        sys.exit(1)
    except smtplib.SMTPAuthenticationError:
        print("SMTP 认证失败。请检查 SMTP_USER 和 SMTP_PASS 是否正确。")
        print("提示: QQ 邮箱需要使用授权码而非密码，在 smtp.qq.com 设置中生成。")
        sys.exit(1)
    except Exception as e:
        print(f"发送失败: {e}")
        sys.exit(1)
