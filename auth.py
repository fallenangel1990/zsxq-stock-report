"""知识星球登录与 Cookie 管理模块。

支持手动扫码登录后将 cookie 持久化到本地文件，后续自动复用。
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

COOKIE_FILE = Path(__file__).parent / "cookies.json"


def login(headless: bool = False) -> dict:
    """启动浏览器，等待用户手动扫码登录，保存并返回 cookies。

    Args:
        headless: 是否无头模式，默认 False 以便用户扫码。

    Returns:
        dict: cookies 列表，每项含 name/value/domain 等字段。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto("https://wx.zsxq.com/")

        print("请使用微信扫码登录知识星球...")
        print("等待登录完成（检测到登录成功后将自动继续）...")

        # 等待登录成功 — 检测页面跳转到知识星球首页或出现用户信息
        try:
            # 方式1: 等待 URL 不再包含 login 相关路径
            page.wait_for_url(
                "**/groups**",
                timeout=300_000,  # 5 分钟超时
            )
        except Exception:
            # 方式2: 检测用户昵称元素出现
            try:
                page.wait_for_selector(
                    '[class*="user"]',
                    timeout=300_000,
                )
            except Exception as e:
                print(f"登录检测超时，请确认是否已完成登录: {e}")
                raise

        # 额外等待确保所有 cookie 都写入
        time.sleep(2)
        cookies = context.cookies()
        browser.close()

    # 持久化
    COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
    print(f"Cookie 已保存到 {COOKIE_FILE}")

    return cookies


def load_cookies() -> dict:
    """加载本地持久化的 cookies。

    优先级：
    1. 环境变量 ZSXQ_COOKIES（JSON 字符串，用于 CI/GitHub Actions）
    2. 本地 cookies.json 文件
    3. 触发登录流程

    Returns:
        dict: cookies 列表。
    """
    # CI 环境：从环境变量加载
    env_cookies = os.environ.get("ZSXQ_COOKIES", "")
    if env_cookies:
        try:
            cookies = json.loads(env_cookies)
            if isinstance(cookies, list) and len(cookies) > 0:
                print(f"已从环境变量 ZSXQ_COOKIES 加载（共 {len(cookies)} 条）")
                return cookies
        except json.JSONDecodeError:
            print("警告: ZSXQ_COOKIES 环境变量不是有效的 JSON，回退到文件加载")

    if not COOKIE_FILE.exists():
        if os.environ.get("GITHUB_ACTIONS"):
            raise RuntimeError(
                "未找到 cookies.json，且当前在 GitHub Actions 中无法扫码登录。"
                "请更新 GitHub Secret: ZSXQ_COOKIES。"
            )
        print("未找到已保存的 Cookie，开始登录...")
        return login()

    cookies = json.loads(COOKIE_FILE.read_text())

    # 检查是否有核心 cookie。浏览器导出的 expires 元数据可能不准或缺失，
    # CI 中最终以 ZSXQ API 的 401/403 作为服务端有效性判断。
    now = time.time()
    token_cookie = None
    for c in cookies:
        if c.get("name") == "zsxq_access_token":
            token_cookie = c
            break

    if not token_cookie:
        if os.environ.get("GITHUB_ACTIONS"):
            raise RuntimeError(
                "知识星球 Cookie 缺少 zsxq_access_token，且当前在 GitHub Actions 中无法扫码登录。"
                "请更新 GitHub Secret: ZSXQ_COOKIES。"
            )
        print("Cookie 缺少 zsxq_access_token，需要重新登录...")
        return login()

    expires = token_cookie.get("expires", 0)
    if isinstance(expires, (int, float)) and expires > 0 and expires <= now:
        print("警告: Cookie expires 元数据已过期，将继续请求 API 由服务端验证。")

    print(f"已加载本地 Cookie（共 {len(cookies)} 条）")
    return cookies


def get_authenticated_context(playwright, headless: bool = True):
    """创建已认证的浏览器上下文。

    自动加载本地 cookie 并注入到新的浏览器上下文中。
    如 cookie 无效则自动触发登录。

    Args:
        playwright: Playwright 实例。
        headless: 是否无头模式。

    Returns:
        tuple: (browser, context, page)
    """
    cookies = load_cookies()

    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
    )

    # 先访问目标站点以设置正确的 domain
    page = context.new_page()
    page.goto("https://wx.zsxq.com/", wait_until="domcontentloaded")

    # 注入 cookie
    context.add_cookies(cookies)

    # 刷新页面让 cookie 生效
    page.reload(wait_until="domcontentloaded")
    time.sleep(2)

    return browser, context, page


def get_cookie_status() -> dict:
    """检查 cookie 状态，返回过期信息和剩余天数。

    用于 CI 环境：将结果写入 GITHUB_OUTPUT，便于后续步骤判断是否需要告警。

    Returns:
        dict: {
            valid: bool,
            expires_at: str (ISO 日期),
            days_remaining: int,
            warning: bool (剩余 ≤ 3 天),
        }
    """
    # CI 环境：从环境变量加载
    env_cookies = os.environ.get("ZSXQ_COOKIES", "")
    if env_cookies:
        try:
            cookies = json.loads(env_cookies)
        except json.JSONDecodeError:
            cookies = []
    elif COOKIE_FILE.exists():
        cookies = json.loads(COOKIE_FILE.read_text())
    else:
        return {"valid": False, "expires_at": "", "days_remaining": 0, "warning": True}

    # 查找 zsxq_access_token 的过期时间
    now = time.time()
    for c in cookies:
        if c.get("name") == "zsxq_access_token":
            expires = c.get("expires", 0)
            if isinstance(expires, (int, float)) and expires > 0:
                expires_dt = datetime.fromtimestamp(expires)
                days = (expires_dt - datetime.now()).days
                return {
                    "valid": True,
                    "expires_at": expires_dt.strftime("%Y-%m-%d"),
                    "days_remaining": max(0, days),
                    "warning": days <= 3,
                    "metadata_expired": expires <= now,
                }
            return {
                "valid": True,
                "expires_at": "未知",
                "days_remaining": 0,
                "warning": True,
                "metadata_expired": False,
            }
            break

    return {
        "valid": False,
        "expires_at": "未知",
        "days_remaining": 0,
        "warning": True,
        "metadata_expired": False,
    }




if __name__ == "__main__":
    login()
