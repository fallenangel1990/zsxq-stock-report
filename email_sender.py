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
from zoneinfo import ZoneInfo

import markdown as md_lib


def _remove_code_blocks(text: str) -> str:
    """移除文本中所有围栏代码块（最后的防线，避免 JSON 泄露到邮件）。"""
    cleaned = re.sub(r"```[a-zA-Z]*\s*\n.*?```", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"```[a-zA-Z]*\{.*?}```", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\njson\s*\n\{.*", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\n?\s*```\s*\n?", "\n", cleaned)
    cleaned = re.sub(r"^##?\s*JSON\s.*?\n", "", cleaned, flags=re.MULTILINE | re.IGNORECASE)
    return cleaned.strip()


# ── 默认配置 ──

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.qq.com").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_SECURITY = os.environ.get("SMTP_SECURITY", "auto").strip().lower()
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
TO_EMAIL = os.environ.get("TO_EMAIL", "470337944@qq.com").strip()

_EMAIL_HIGHLIGHT_TERMS = (
    "最适合买入",
    "立即可买",
    "优先买入",
    "重点买入",
    "强烈关注",
    "重点关注",
    "高推荐",
    "强异动/疑似建仓",
    "疑似建仓",
    "主力净流入",
    "推荐指数",
    "风险点",
    "潜在利空",
    "风险",
    "止损",
    "止盈",
    "减仓",
    "卖出",
    "过热",
    "只观察",
)

_HIGHLIGHT_STYLE = (
    "color:#b91c1c;background:#fff1f2;border:1px solid #fecdd3;"
    "border-radius:4px;padding:1px 5px;font-weight:700;"
)


def _now_shanghai() -> datetime:
    """返回北京时间当前时间。"""
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _news_subject(now: Optional[datetime] = None) -> str:
    """生成定时报告邮件主题。"""
    current = now or _now_shanghai()
    return f"新闻资讯{current.month}月{current.day}日"


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
    msg["Date"] = _now_shanghai().strftime("%a, %d %b %Y %H:%M:%S +0800")

    # 正文（纯 HTML，无附件）
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    return msg


def _highlight_email_keywords(html: str) -> str:
    """只在 HTML 文本节点中标红重点词，避免破坏标签属性。"""
    if not html:
        return html

    pattern = re.compile(
        "(" + "|".join(re.escape(term) for term in _EMAIL_HIGHLIGHT_TERMS) + ")"
    )
    parts = re.split(r"(<[^>]+>)", html)
    highlighted = []
    for part in parts:
        if not part or part.startswith("<"):
            highlighted.append(part)
            continue
        highlighted.append(
            pattern.sub(
                rf'<span style="{_HIGHLIGHT_STYLE}">\1</span>',
                part,
            )
        )
    return "".join(highlighted)


def _smtp_login_and_send(
    host: str,
    port: int,
    security: str,
    to_email: str,
    message: MIMEMultipart,
) -> None:
    """连接 SMTP 并发送邮件。

    security:
        ssl      - SMTP over SSL，常见端口 465
        starttls - 明文连接后 STARTTLS，常见端口 587
        plain    - 明文 SMTP，仅用于明确配置的内网服务
    """
    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [to_email], message.as_string())
        return

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        if security == "starttls":
            server.starttls()
            server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [to_email], message.as_string())


def _smtp_attempts(host: str, port: int, security: str) -> list[tuple[str, int]]:
    """根据配置生成 SMTP 尝试顺序。"""
    if security in {"ssl", "starttls", "plain"}:
        return [(security, port)]

    attempts = [("ssl" if port == 465 else "starttls", port)]
    fallback = ("starttls", 587) if port == 465 else ("ssl", 465)
    if fallback not in attempts:
        attempts.append(fallback)
    return attempts


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
    today = _now_shanghai().strftime("%Y-%m-%d")

    if not subject:
        subject = _news_subject()

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

    errors = []
    for security, attempt_port in _smtp_attempts(host, port, SMTP_SECURITY):
        try:
            print(f"[邮件] SMTP {host}:{attempt_port} ({security})")
            _smtp_login_and_send(host, attempt_port, security, to, msg)
            break
        except smtplib.SMTPAuthenticationError:
            raise
        except (smtplib.SMTPException, OSError) as exc:
            errors.append(f"{host}:{attempt_port} ({security}) -> {type(exc).__name__}: {exc}")
            if SMTP_SECURITY in {"ssl", "starttls", "plain"}:
                raise
            print(f"[邮件] SMTP 尝试失败，准备重试: {type(exc).__name__}: {exc}")
    else:
        detail = "\n".join(f"  - {item}" for item in errors)
        raise smtplib.SMTPException(
            "SMTP 发送失败，所有连接方式均不可用。\n"
            f"{detail}\n"
            "请检查 GitHub Secrets 中的 SMTP_USER/SMTP_PASS 是否为邮箱授权码，"
            "以及 SMTP_HOST/SMTP_PORT/SMTP_SECURITY 是否与邮箱服务商匹配。"
        )

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
        '<div style="overflow-x:auto;margin:18px 0 24px;border:1px solid #e5e7eb;'
        'border-radius:8px;background:#fff;">'
        '<table style="border-collapse:collapse;width:100%;font-size:14px;'
        'line-height:1.65;min-width:760px;">',
    )
    html = html.replace("</table>", "</table></div>")
    html = html.replace(
        "<thead>",
        '<thead style="background:#1d4ed8;color:white;">',
    )
    html = html.replace(
        "<th>",
        '<th style="padding:11px 12px;text-align:left;border:1px solid #1e40af;'
        'font-weight:700;white-space:nowrap;">',
    )
    html = html.replace(
        "<td>",
        '<td style="padding:10px 12px;border-bottom:1px solid #eef2f7;'
        'border-right:1px solid #eef2f7;text-align:left;vertical-align:top;">',
    )

    # 标题层级调整（报告已有 h1，邮件中降一级）。从低层级往高层级替换，避免闭合标签二次降级。
    html = html.replace(
        "<h3>",
        '<h4 style="color:#374151;margin:22px 0 10px;font-size:16px;line-height:1.45;">',
    )
    html = html.replace("</h3>", "</h4>")
    html = html.replace(
        "<h2>",
        '<h3 style="color:#1d4ed8;margin:28px 0 12px;font-size:18px;'
        'line-height:1.45;border-left:4px solid #2563eb;padding-left:10px;">',
    )
    html = html.replace("</h2>", "</h3>")
    html = html.replace(
        "<h1>",
        '<h2 style="color:#111827;margin:10px 0 18px;font-size:22px;'
        'line-height:1.35;">',
    )
    html = html.replace("</h1>", "</h2>")
    html = html.replace(
        "<p>",
        '<p style="margin:10px 0;line-height:1.8;color:#374151;font-size:15px;">',
    )
    html = html.replace(
        "<ul>",
        '<ul style="margin:10px 0 16px;padding-left:22px;line-height:1.8;color:#374151;">',
    )
    html = html.replace(
        "<ol>",
        '<ol style="margin:10px 0 16px;padding-left:22px;line-height:1.8;color:#374151;">',
    )
    html = html.replace(
        "<li>",
        '<li style="margin:6px 0;">',
    )
    html = html.replace(
        "<strong>",
        '<strong style="color:#111827;font-weight:700;">',
    )
    html = html.replace(
        "<code>",
        '<code style="background:#f3f4f6;color:#b91c1c;padding:2px 5px;'
        'border-radius:4px;font-family:Menlo,Consolas,monospace;font-size:13px;">',
    )

    # 引用块样式
    html = html.replace(
        "<blockquote>",
        '<blockquote style="border-left:4px solid #2563eb;padding:10px 14px;'
        'color:#4b5563;margin:14px 0;background:#f8fafc;border-radius:0 8px 8px 0;">',
    )

    # 水平线
    html = html.replace("<hr>", '<hr style="border:0;border-top:1px solid #e5e7eb;margin:24px 0;">')
    html = html.replace("<hr />", '<hr style="border:0;border-top:1px solid #e5e7eb;margin:24px 0;">')

    return _highlight_email_keywords(html)


def _weekday_cn() -> str:
    """返回当前星期几的中文名称。"""
    return ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][
        _now_shanghai().weekday()
    ]


def send_report_notification(
    markdown_path: str,
    to_email: str = "",
    extra_info: Optional[dict] = None,
    subject_override: str = "",
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
    now = _now_shanghai()
    today = now.strftime("%Y-%m-%d")
    subject = subject_override or _news_subject(now)
    extra_info = extra_info or {}

    # 读取报告。复盘报告可直接保存为 HTML；Markdown 报告再转换。
    md_path = markdown_path
    direct_html_report = False
    if Path(md_path).exists():
        report_text = Path(md_path).read_text(encoding="utf-8")
        if Path(md_path).suffix.lower() in (".html", ".htm") or report_text.lstrip().lower().startswith(("<!doctype html", "<html")):
            report_html = report_text
            direct_html_report = True
        else:
            md_text = _remove_code_blocks(report_text)
            report_html = _md_to_html(md_text)
    else:
        report_html = '<p style="color:#dc2626;">报告文件不存在，请检查 GitHub Actions 运行日志。</p>'

    if direct_html_report:
        return send_email(
            to_email=to_email,
            subject=subject,
            body_html=report_html,
        )

    # 构建带样式的完整邮件正文
    lines = [
        '<div style="background:#f3f6fb;padding:18px 10px;">',
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'max-width:980px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;'
        'border-radius:12px;overflow:hidden;color:#1f2937;">',
        # 头部横幅
        f'<div style="background:#1d4ed8;color:white;padding:22px 26px;">',
        f'<h2 style="margin:0;font-size:22px;line-height:1.35;">{subject}</h2>',
        f'<p style="margin:8px 0 0;opacity:0.88;font-size:14px;">{today} {_weekday_cn()}</p>',
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
            '<div style="background:#eff6ff;padding:14px 26px;border-left:4px solid #2563eb;'
            'font-size:15px;line-height:1.7;">'
            + " &nbsp;│&nbsp; ".join(stats_parts)
            + "</div>"
        )

    # Cookie 预警
    if extra_info.get("cookie_expired"):
        lines.append(
            '<div style="background:#fef2f2;padding:14px 26px;border-left:4px solid #dc2626;'
            'margin:8px 0;font-size:15px;line-height:1.7;">'
            "⚠️ <strong>Cookie 已过期！</strong>请本地运行 <code>python main.py login</code> 重新登录。"
            "</div>"
        )
    elif extra_info.get("cookie_warning"):
        days = extra_info.get("cookie_days", "")
        expires = extra_info.get("cookie_expires", "")
        lines.append(
            '<div style="background:#fffbeb;padding:14px 26px;border-left:4px solid #f59e0b;'
            'margin:8px 0;font-size:15px;line-height:1.7;">'
            f"⚠️ <strong>Cookie 将在 {days} 天后过期</strong>（{expires}），请提前更新。"
            "</div>"
        )

    # 报告主体
    lines.append('<div style="padding:18px 26px 4px;font-size:15px;line-height:1.75;">')
    lines.append(report_html)
    lines.append("</div>")

    # 页脚
    lines.append(
        '<div style="color:#6b7280;font-size:12px;padding:18px 26px;'
        'border-top:1px solid #e5e7eb;margin-top:18px;line-height:1.7;background:#fafafa;">'
        "本邮件由自动化系统发送。<br>"
        "报告基于知识星球专栏内容，由 AI 自动生成，仅供参考。"
        "</div>"
    )
    lines.append("</div>")
    lines.append("</div>")

    body_html = "\n".join(lines)

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
    today = _now_shanghai().strftime("%Y-%m-%d %H:%M 北京时间")
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
