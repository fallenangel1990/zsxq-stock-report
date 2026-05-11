"""邮件发送模块。

通过 QQ 邮箱 SMTP 发送带 PDF 附件的邮件。
凭证通过环境变量配置，支持 GitHub Actions Secrets。
"""

import os
import smtplib
import sys
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional


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
    attachment_path: str,
) -> MIMEMultipart:
    """构建带 PDF 附件的 MIME 邮件。

    Args:
        to_email: 收件人邮箱。
        subject: 邮件主题。
        body_html: HTML 格式的邮件正文。
        attachment_path: PDF 附件路径。

    Returns:
        MIMEMultipart 邮件对象。
    """
    msg = MIMEMultipart("mixed")
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")

    # 正文
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    # PDF 附件
    with open(attachment_path, "rb") as f:
        pdf = MIMEApplication(f.read(), _subtype="pdf")
        filename = Path(attachment_path).name
        pdf.add_header(
            "Content-Disposition",
            "attachment",
            filename=("utf-8", "", filename),
        )
        msg.attach(pdf)

    return msg


def send_email(
    to_email: str = "",
    subject: str = "",
    body_html: str = "",
    attachment_path: str = "",
    smtp_host: str = "",
    smtp_port: int = 0,
) -> bool:
    """发送带 PDF 附件的邮件。

    凭证从环境变量 SMTP_USER / SMTP_PASS 读取。

    Args:
        to_email: 收件人邮箱，默认使用环境变量 TO_EMAIL。
        subject: 邮件主题，默认为"每日股票机会报告 YYYY-MM-DD"。
        body_html: HTML 邮件正文。
        attachment_path: PDF 附件路径。
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
        subject = f"每日股票机会报告 {today}"

    if not body_html:
        body_html = f"""\
<html>
<body style="font-family: sans-serif;">
<h2>📊 每日股票机会报告</h2>
<p>日期：{today}</p>
<p>请查收附件中的完整股票机会分析报告（PDF）。</p>
<hr>
<p style="color:#888;font-size:12px;">
本邮件由自动化系统发送，请勿回复。<br>
如需停止接收，请联系管理员。
</p>
</body>
</html>"""

    if not attachment_path:
        raise ValueError("attachment_path 不能为空")

    print(f"[邮件] 发送至 {to} ...")
    print(f"[邮件] 附件: {attachment_path} ({Path(attachment_path).stat().st_size / 1024:.1f} KB)")

    msg = _build_message(to, subject, body_html, attachment_path)

    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [to], msg.as_string())

    print(f"[邮件] 发送成功 → {to}")
    return True


def send_report_notification(
    pdf_path: str,
    to_email: str = "",
    extra_info: Optional[dict] = None,
) -> bool:
    """发送股票报告通知邮件（便捷封装）。

    自动生成带报告摘要的邮件正文。

    Args:
        pdf_path: PDF 报告文件路径。
        to_email: 收件人邮箱。
        extra_info: 额外信息字典，支持以下字段：
            - total_posts: 新增帖子数
            - new_stocks: 新发现股票数
            - cookie_expired: cookie 是否过期
    """
    today = datetime.now().strftime("%Y-%m-%d")
    extra_info = extra_info or {}

    # 构建更丰富的正文
    lines = [
        f"<h2>📊 每日股票机会报告</h2>",
        f"<p><strong>日期：</strong>{today}（{datetime.now().strftime('%A')}）</p>",
        f"<p><strong>报告文件：</strong>{Path(pdf_path).name}</p>",
    ]

    if extra_info.get("total_posts"):
        lines.append(f"<p><strong>新增帖子：</strong>{extra_info['total_posts']} 篇</p>")
    if extra_info.get("new_stocks"):
        lines.append(f"<p><strong>发现标的：</strong>{extra_info['new_stocks']} 只</p>")

    if extra_info.get("cookie_expired"):
        lines.append(
            f'<p style="color:#dc2626;"><strong>⚠️ Cookie 已过期！</strong>'
            f"请本地运行 <code>python main.py login</code> 重新登录后更新 GitHub Secret。</p>"
        )

    lines.append("<p>请查收附件中的完整股票机会分析报告（PDF）。</p>")
    lines.append("<hr>")
    lines.append(
        '<p style="color:#888;font-size:12px;">'
        "本邮件由自动化系统发送。<br>"
        "报告基于知识星球专栏内容，由 AI 自动生成，仅供参考。"
        "</p>"
    )

    subject = f"📊 每日股票机会报告 {today}"
    if extra_info.get("cookie_expired"):
        subject = f"[需重新登录] {subject}"

    return send_email(
        to_email=to_email,
        subject=subject,
        body_html="\n".join(lines),
        attachment_path=pdf_path,
    )


# ── CLI ──

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 email_sender.py <pdf文件路径> [收件人邮箱]")
        print()
        print("环境变量:")
        print("  SMTP_USER  — QQ 邮箱地址")
        print("  SMTP_PASS  — QQ 邮箱授权码")
        print("  TO_EMAIL   — 默认收件人（可选）")
        sys.exit(1)

    pdf_path = sys.argv[1]
    to_email = sys.argv[2] if len(sys.argv) > 2 else ""

    if not Path(pdf_path).exists():
        print(f"错误: 文件不存在 — {pdf_path}")
        sys.exit(1)

    try:
        send_report_notification(pdf_path, to_email)
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
