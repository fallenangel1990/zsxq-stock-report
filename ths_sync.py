"""同花顺账户自选股同步模块。

将知识星球提取、评分达标的股票同步到同花顺自选股或指定分组。

API（基于逆向分析）：
  - 默认自选: t.10jqka.com.cn — getSelfStockWithMarket / modifySelfStock
  - 自定义分组: ugc.10jqka.com.cn — group/v1/query、content/v1/add
  - 需要已登录同花顺账户的 cookies

使用方法：
  1. 在 Chrome 中安装 EditThisCookie 扩展
  2. 登录 i.10jqka.com.cn（同花顺个人中心）
  3. 导出 cookies 到 cookies_ths.json
  4. 在 config.yaml 配置 ths（score_threshold、group_name 等）
  5. 运行 python main.py thssync
"""

import json
import logging
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import yaml

logger = logging.getLogger(__name__)

# 同花顺自选分组 API（ugc.10jqka.com.cn，与手机端分组同步）
UGC_API_BASE = "https://ugc.10jqka.com.cn"
UGC_QUERY_GROUPS = "/optdata/selfgroup/open/api/group/v1/query"
UGC_ADD_ITEM = "/optdata/selfgroup/open/api/content/v1/add"
UGC_ADD_GROUP = "/optdata/selfgroup/open/api/group/v1/add"
UGC_FROM_PARAM = "sjcg_gphone"

MARKET_CODE = {
    "SH": "17",
    "SZ": "33",
    "KC": "18",
    "CYB": "38",
    "BJ": "71",
}

MOBILE_UA = (
    "Hexin_Gphone/11.28.03 (Royal Flush) hxtheme/0 innerversion/G037.09.028.1.32 "
    "followPhoneSystemTheme/0 getHXAPPAccessibilityMode/0 "
    "hxNewFont/1 isVip/0 getHXAPPFontSetting/normal getHXAPPAdaptOldSetting/0 okhttp/3.14.9"
)


def make_daily_group_name(prefix: str = "知识星球") -> str:
    """生成当日分组名；prefix 为空时只使用日期，如 05-19。"""
    prefix = "" if prefix is None else str(prefix).strip()
    date_part = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%m-%d")
    return f"{prefix} {date_part}".strip() if prefix else date_part


def resolve_group_name(prefix: str = "知识星球", explicit: str = "auto") -> str:
    """解析目标分组名。explicit 为空或 auto 时使用当日命名。"""
    explicit = (explicit or "auto").strip()
    if explicit and explicit.lower() not in ("auto", ""):
        return explicit
    return make_daily_group_name(prefix)


def _infer_market_code(stock_code: str) -> str:
    """根据 6 位代码推断同花顺市场类型码。"""
    code = str(stock_code).zfill(6)
    if code.startswith("688"):
        return MARKET_CODE["KC"]
    if code.startswith("6"):
        return MARKET_CODE["SH"]
    if code.startswith(("0", "3")):
        return MARKET_CODE["SZ"]
    if code.startswith(("4", "8")):
        return MARKET_CODE["BJ"]
    return MARKET_CODE["SZ"]


def _load_config() -> dict:
    """加载同花顺配置（含默认值）。"""
    config_path = Path(__file__).parent / "config.yaml"
    config = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
    ths_config = config.get("ths", {})
    return {
        "enabled": ths_config.get("enabled", False),
        "cookies_path": ths_config.get("cookies_path", "cookies_ths.json"),
        "score_threshold": ths_config.get("score_threshold", 3.0),
        "request_delay": ths_config.get("request_delay", 0.3),
        "group_name_prefix": ths_config.get("group_name_prefix", "知识星球"),
        "group_name": resolve_group_name(
            ths_config.get("group_name_prefix", "知识星球"),
            ths_config.get("group_name", "auto"),
        ),
        "create_group_if_missing": ths_config.get("create_group_if_missing", True),
        "also_add_to_watchlist": ths_config.get("also_add_to_watchlist", False),
    }


class THSClient:
    """同花顺自选股管理客户端。

    通过同花顺网页版 JSONP API 操作自选股（添加/查询）。
    需要用户导出的同花顺登录 cookies。

    Attributes:
        score_threshold: 推荐指数阈值，高于此值的股票视为"重点推荐"。
        api_base: 同花顺个人中心 API 基址。
    """

    # 同花顺自选股 API 端点
    API_GET_LIST = "/newcircle/group/getSelfStockWithMarket/"
    API_MODIFY = "/newcircle/group/modifySelfStock/"

    def __init__(
        self,
        cookies_path: str = "cookies_ths.json",
        score_threshold: float = 3.0,
        request_delay: float = 0.3,
        group_name: str = "",
        create_group_if_missing: bool = True,
        also_add_to_watchlist: bool = False,
    ):
        """
        Args:
            cookies_path: 同花顺 cookies 文件路径。
            score_threshold: 推荐指数阈值，>= 此值的股票会同步。
            request_delay: 每次 API 请求间隔（秒），避免被限流。
            group_name: 目标分组名称；为空则仅写入默认自选股。
            create_group_if_missing: 分组不存在时是否自动创建。
            also_add_to_watchlist: 写入分组后是否同时加入默认自选股。
        """
        self.cookies_path = Path(cookies_path)
        self.score_threshold = score_threshold
        self.request_delay = request_delay
        self.group_name = (group_name or "").strip()
        self.create_group_if_missing = create_group_if_missing
        self.also_add_to_watchlist = also_add_to_watchlist
        self.api_base = ""
        self._group_version: Optional[str] = None
        self._groups_cache: dict[str, dict] = {}

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://i.10jqka.com.cn/",
        })
        self._ready = self._load_cookies()
        if self._ready:
            self._resolve_api_base()

    @property
    def ready(self) -> bool:
        """客户端是否就绪（cookies 已加载）。"""
        return self._ready

    def _load_cookies(self) -> bool:
        """从文件加载同花顺 cookies。

        支持两种格式：
          1. 列表 — [{"name": "...", "value": "...", "domain": "...", ...}]
          2. 字典 — {"cookie_name": "cookie_value", ...}
        """
        if not self.cookies_path.exists():
            logger.warning("同花顺 cookies 文件不存在: %s", self.cookies_path)
            return False

        with open(self.cookies_path, "r") as f:
            cookies_data = json.load(f)

        if isinstance(cookies_data, list):
            for c in cookies_data:
                name = c.get("name", c.get("key", ""))
                value = c.get("value", "")
                domain = c.get("domain", ".10jqka.com.cn")
                path = c.get("path", "/")
                self._session.cookies.set(
                    name,
                    value,
                    domain=domain,
                    path=path,
                )
                if name and domain.lstrip(".").endswith("10jqka.com.cn") and domain != ".10jqka.com.cn":
                    self._session.cookies.set(
                        name,
                        value,
                        domain=".10jqka.com.cn",
                        path=path,
                    )
        elif isinstance(cookies_data, dict):
            for name, value in cookies_data.items():
                self._session.cookies.set(name, value, domain=".10jqka.com.cn")
        else:
            logger.error("不支持的 cookies 格式，请使用 list 或 dict")
            return False

        logger.info(
            "同花顺 cookies 加载完成（%d 个）",
            len(self._session.cookies),
        )
        return True

    def _resolve_api_base(self) -> None:
        """通过访问 i.10jqka.com.cn 确定实际 API 基址。

        同花顺会将 i.10jqka.com.cn 重定向到 t.10jqka.com.cn，
        需要通过一次访问来确定正确的域名。
        """
        try:
            resp = self._session.get(
                "https://i.10jqka.com.cn/",
                timeout=10,
                allow_redirects=True,
            )
            # 从最终 URL 确定 api_base
            final_url = resp.url
            from urllib.parse import urlparse
            parsed = urlparse(final_url)
            self.api_base = f"{parsed.scheme}://{parsed.netloc}"
            logger.info("同花顺 API 基址: %s", self.api_base)
        except Exception as e:
            logger.warning("解析 API 基址失败，使用默认域名: %s", e)
            self.api_base = "https://t.10jqka.com.cn"

    def check_login(self) -> bool:
        """验证同花顺登录状态是否有效。"""
        if not self._ready:
            return False
        if not self.api_base:
            return False
        try:
            resp = self._session.get(
                f"{self.api_base}{self.API_GET_LIST}",
                params={"callback": "selfStock", "_": int(time.time() * 1000)},
                timeout=10,
            )
            resp.raise_for_status()
            return '"errorCode":0' in resp.text
        except Exception as e:
            logger.warning("同花顺登录状态检查失败: %s", e)
            return False

    def get_stock_codes(self) -> set[str]:
        """获取当前自选股列表中的所有股票代码。

        Returns:
            股票代码的集合，如 {"600519", "000858"}。
        """
        if not self.api_base:
            return set()
        try:
            resp = self._session.get(
                f"{self.api_base}{self.API_GET_LIST}",
                params={"callback": "selfStock", "_": int(time.time() * 1000)},
                timeout=10,
            )
            resp.raise_for_status()
            codes = set(re.findall(r'"code":"(\d+)"', resp.text))
            return codes
        except Exception as e:
            logger.error("获取自选股列表失败: %s", e)
            return set()

    def add_stock(self, code: str, stock_name: str = "") -> tuple[bool, str]:
        """将股票添加到同花顺自选股。

        使用同花顺网页版的 modifySelfStock JSONP API。
        API 端点: GET /newcircle/group/modifySelfStock/?op=add&stockcode=CODE

        Args:
            code: 6 位股票代码。
            stock_name: 股票名称（仅用于日志）。

        Returns:
            (是否成功, 消息)。
        """
        if not self.api_base:
            return False, "API 基址未就绪"

        time.sleep(self.request_delay)
        display = f"{stock_name}({code})" if stock_name else code

        try:
            resp = self._session.get(
                f"{self.api_base}{self.API_MODIFY}",
                params={
                    "callback": "modifyStock",
                    "op": "add",
                    "stockcode": code,
                },
                timeout=10,
            )
            resp.raise_for_status()

            # 解析 JSONP 响应: modifyStock({"errorCode":0,"errorMsg":"修改成功",...})
            match = re.search(r'\{.*\}', resp.text)
            if not match:
                return False, "无法解析返回数据"

            data = json.loads(match.group())
            if data.get("errorCode") == 0:
                logger.info("添加成功: %s", display)
                return True, "添加成功"
            else:
                msg = data.get("errorMsg", "未知错误")
                logger.warning("添加失败 %s: %s", display, msg)
                return False, msg

        except Exception as e:
            logger.error("添加异常 %s: %s", display, e)
            return False, str(e)

    def ensure_stock_in_watchlist(self, code: str, stock_name: str = "") -> tuple[bool, str]:
        """添加默认自选并二次确认，避免接口返回成功但实际未落库。"""
        code = str(code).zfill(6)
        display = f"{stock_name}({code})" if stock_name else code
        existing = self.get_stock_codes()
        if code in existing:
            return True, "已在自选股中"

        messages = []
        for attempt in range(2):
            ok, msg = self.add_stock(code, stock_name)
            messages.append(msg)
            refreshed = self.get_stock_codes()
            if code in refreshed:
                return True, "已加入自选股" if ok else f"已加入自选股（确认成功，接口消息: {msg}）"
            if attempt == 0:
                logger.warning("默认自选添加后未确认到 %s，准备重试", display)
                time.sleep(max(self.request_delay, 0.5))
        return False, "默认自选添加后未确认: " + " / ".join(messages)

    def _ugc_post(self, endpoint: str, payload: dict) -> dict:
        """调用 ugc 分组 API（form-urlencoded POST）。"""
        url = f"{UGC_API_BASE}{endpoint}"
        headers = {
            "User-Agent": MOBILE_UA,
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        data = {**payload, "from": UGC_FROM_PARAM}
        resp = self._session.post(url, data=data, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status_code") != 0:
            msg = body.get("status_msg", "未知业务错误")
            raise RuntimeError(msg)
        return body.get("data") or {}

    def _ugc_get(self, endpoint: str, params: dict) -> dict:
        """调用 ugc 分组 API（GET）。"""
        url = f"{UGC_API_BASE}{endpoint}"
        headers = {"User-Agent": MOBILE_UA}
        params = {**params, "from": UGC_FROM_PARAM}
        resp = self._session.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status_code") != 0:
            msg = body.get("status_msg", "未知业务错误")
            raise RuntimeError(msg)
        return body.get("data") or {}

    def _parse_group_codes(self, content: str) -> set[str]:
        """从分组 content 字段解析已有股票代码。

        后端返回格式: "600519,17|000858,33"（代码,市场码|代码,市场码）
        需要先按 | 分割条目，再取每个条目的第一部分作为代码。
        """
        if not content:
            return set()
        entries = content.split("|")
        return {entry.split(",")[0] for entry in entries if entry}

    def query_groups(self, refresh: bool = False) -> dict[str, dict]:
        """获取所有自选股分组 {名称: {id, codes, version}}。"""
        if self._groups_cache and not refresh:
            return self._groups_cache

        data = self._ugc_get(
            UGC_QUERY_GROUPS,
            {"types": "0,1"},
        )
        if "version" in data:
            self._group_version = str(data["version"])

        groups: dict[str, dict] = {}
        for g in data.get("group_list", []):
            name = g.get("name")
            gid = g.get("id")
            if not name or not gid:
                continue
            groups[name] = {
                "id": gid,
                "codes": self._parse_group_codes(g.get("content", "")),
            }
        self._groups_cache = groups
        logger.info("已加载 %d 个同花顺自选分组", len(groups))
        return groups

    def _ensure_group_version(self) -> str:
        if not self._group_version:
            self.query_groups(refresh=True)
        if not self._group_version:
            raise RuntimeError("无法获取自选分组版本号")
        return self._group_version

    def create_group(self, name: str) -> str:
        """创建自选分组，返回 group_id。"""
        version = self._ensure_group_version()
        data = self._ugc_post(
            UGC_ADD_GROUP,
            {"name": name, "type": "0", "version": version},
        )
        if "version" in data:
            self._group_version = str(data["version"])
        group_id = data.get("group_id") or data.get("groupid") or data.get("id")
        if not group_id:
            self.query_groups(refresh=True)
            group_id = self._groups_cache.get(name, {}).get("id")
        if not group_id:
            raise RuntimeError(f"创建分组「{name}」后未获取到 group_id")
        logger.info("已创建分组: %s (%s)", name, group_id)
        self.query_groups(refresh=True)
        return str(group_id)

    def resolve_group_id(self, name: str) -> str:
        """按名称查找分组 ID，不存在时可自动创建。"""
        groups = self.query_groups(refresh=True)
        if name in groups:
            return groups[name]["id"]
        if self.create_group_if_missing:
            return self.create_group(name)
        raise RuntimeError(f"未找到分组「{name}」，请在同花顺客户端创建或开启 create_group_if_missing")

    def add_stock_to_group(self, group_id: str, code: str, stock_name: str = "") -> tuple[bool, str]:
        """将股票添加到指定自选分组。"""
        display = f"{stock_name}({code})" if stock_name else code
        market = _infer_market_code(code)
        code = str(code).zfill(6)

        groups = self.query_groups()
        for g in groups.values():
            if g["id"] == group_id and code in g["codes"]:
                return True, "已在分组中"

        for attempt in range(2):
            try:
                version = self._ensure_group_version()
                time.sleep(self.request_delay)
                data = self._ugc_post(
                    UGC_ADD_ITEM,
                    {
                        "id": group_id,
                        "content": f"{code},{market}",
                        "num": "1",
                        "version": version,
                    },
                )
                if "version" in data:
                    self._group_version = str(data["version"])
                refreshed = self.query_groups(refresh=True)
                for g in refreshed.values():
                    if g["id"] == group_id and code in g["codes"]:
                        logger.info("分组添加成功并已确认: %s", display)
                        return True, "已加入分组"
                raise RuntimeError("接口返回成功但刷新分组后未确认到股票")
            except Exception as e:
                if attempt == 0:
                    logger.warning("分组添加失败，刷新版本后重试: %s", e)
                    self.query_groups(refresh=True)
                    continue
                logger.error("分组添加异常 %s: %s", display, e)
                return False, str(e)
        return False, "重试后仍失败"

    def filter_top_stocks(self, stocks: list[dict]) -> list[dict]:
        """按推荐指数阈值过滤重点推荐股票，并确保有有效代码。

        Args:
            stocks: enriched 股票列表（含 score, code, name 字段）。

        Returns:
            过滤后的股票列表，按评分降序排列。
        """
        top = []
        for s in stocks:
            score = s.get("score", 0)
            code = str(s.get("code", "")).strip()
            if score >= self.score_threshold and code and len(code) == 6 and code.isdigit():
                top.append(s)
        top.sort(key=lambda x: x.get("score", 0), reverse=True)
        return top

    def sync_stocks(self, stocks: list[dict]) -> dict:
        """将评分达标的股票同步到同花顺自选或指定分组。

        Args:
            stocks: 增强后的股票列表（enriched list, 含 score/code/name 字段）。

        Returns:
            同步结果字典。
        """
        if not self._ready:
            return {
                "status": "skipped",
                "reason": f"cookies 文件不存在: {self.cookies_path}",
            }

        top_stocks = self.filter_top_stocks(stocks)
        if not top_stocks:
            return {
                "status": "skipped",
                "reason": f"无推荐指数 >= {self.score_threshold} 且代码有效的股票",
            }

        use_group = bool(self.group_name)
        target_label = self.group_name if use_group else "默认自选股"

        group_error = ""
        if use_group:
            try:
                group_id = self.resolve_group_id(self.group_name)
            except Exception as e:
                group_error = f"分组「{self.group_name}」: {e}"
                logger.error(group_error)
                if not self.also_add_to_watchlist:
                    return {"status": "error", "reason": group_error}
                use_group = False
                target_label = "默认自选股"
                group_id = ""

        if not use_group:
            if not self.api_base:
                self._resolve_api_base()
            if not self.api_base:
                return {"status": "error", "reason": "无法解析 API 基址"}
            if not self.check_login():
                return {
                    "status": "error",
                    "reason": "同花顺登录已过期，请重新导出 cookies",
                }

        existing_codes: set[str] = set()
        if not use_group:
            existing_codes = self.get_stock_codes()
            logger.info("当前自选股: %d 只", len(existing_codes))
        elif self.also_add_to_watchlist:
            if not self.api_base:
                self._resolve_api_base()
            if self.check_login():
                existing_codes = self.get_stock_codes()

        results = []
        for s in top_stocks:
            code = str(s["code"]).zfill(6)
            name = s.get("name", "未知")
            group_ok = None
            group_msg = ""
            watchlist_ok = None
            watchlist_msg = ""

            if use_group:
                group_ok, group_msg = self.add_stock_to_group(group_id, code, name)
                if self.also_add_to_watchlist:
                    watchlist_ok, watchlist_msg = self.ensure_stock_in_watchlist(code, name)
                    if watchlist_ok:
                        existing_codes.add(code)
                success = bool(group_ok and (watchlist_ok is not False))
                parts = [group_msg]
                if self.also_add_to_watchlist:
                    parts.append(f"默认自选: {watchlist_msg}")
                msg = "；".join(part for part in parts if part)
            elif code in existing_codes:
                success, msg = True, "已在自选股中"
                watchlist_ok, watchlist_msg = True, msg
            else:
                success, msg = self.ensure_stock_in_watchlist(code, name)
                watchlist_ok, watchlist_msg = success, msg
                if success:
                    existing_codes.add(code)

            results.append({
                "name": name,
                "code": code,
                "score": s.get("score"),
                "success": success,
                "msg": msg,
                "group_success": group_ok,
                "watchlist_success": watchlist_ok,
            })

        success_count = sum(1 for r in results if r["success"])
        already_count = sum(
            1 for r in results if "已在" in r.get("msg", "")
        )
        failed = [r for r in results if not r["success"]]

        logger.info(
            "同花顺同步完成 [%s]: %d/%d 成功（%d 已存在）",
            target_label,
            success_count,
            len(results),
            already_count,
        )

        status = "partial" if failed else "success"
        return {
            "status": status,
            "target": target_label,
            "warning": group_error,
            "added": success_count - already_count,
            "already": already_count,
            "total": len(top_stocks),
            "failed": failed,
            "details": results,
        }


def format_sync_result(result: dict) -> str:
    """将同步结果格式化为人类可读的文本。"""
    status = result.get("status", "unknown")
    lines = [f"[同花顺同步] 状态: {status}"]

    if status == "skipped":
        lines.append(f"  原因: {result.get('reason', '未配置')}")
    elif status == "error":
        lines.append(f"  原因: {result.get('reason', '未知错误')}")
    elif status in ("success", "partial"):
        target = result.get("target", "自选股")
        added = result.get("added", 0)
        already = result.get("already", 0)
        total = result.get("total", 0)
        lines.append(f"  目标: {target}")
        if result.get("warning"):
            lines.append(f"  警告: {result['warning']}")
        lines.append(f"  新增: {added} 只")
        lines.append(f"  已存在: {already} 只")
        lines.append(f"  共 {total} 只（评分 >= 阈值）")

        failed = result.get("failed", [])
        if failed:
            names = ", ".join(f"{f['name']}({f['code']})" for f in failed)
            lines.append(f"  失败: {names}")

        details = result.get("details", [])
        if details:
            lines.append("  详情:")
            for d in details:
                if "已在" in d.get("msg", ""):
                    mark = "📌"
                elif d["success"]:
                    mark = "✅"
                else:
                    mark = "❌"
                lines.append(f"    {mark} {d['name']} ({d['code']}) 评分: {d.get('score', '-')} {d.get('msg', '')}")

    return "\n".join(lines)
