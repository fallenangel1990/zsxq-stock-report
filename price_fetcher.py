"""实时股价获取模块。

使用腾讯行情 API（qt.gtimg.cn）批量获取 A 股实时数据，
包括当前价格、涨跌幅、动态PE、市净率、总市值等。
"""

import requests
from typing import Optional


# 腾讯 API 字段索引（以 ~ 分隔）
_FIELD_NAME = 1
_FIELD_CODE = 2
_FIELD_PRICE = 3
_FIELD_PREV_CLOSE = 4
_FIELD_OPEN = 5
_FIELD_VOLUME = 6
_FIELD_CHANGE_PCT = 32
_FIELD_HIGH = 33
_FIELD_LOW = 34
_FIELD_TURNOVER = 38
_FIELD_PE = 39
_FIELD_MARKET_CAP = 45  # 单位：亿
_FIELD_PB = 46


def _code_to_tencent(code: str) -> str:
    """将 6 位 A 股代码转为腾讯 API 前缀格式。

    上海（6开头）→ sh，深圳（0/3开头）→ sz。
    港股（4-5位数字）返回空字符串。
    """
    code = code.strip()
    if not code or not code.isdigit():
        return ""
    if len(code) == 6:
        if code.startswith("6"):
            return f"sh{code}"
        elif code.startswith(("0", "3")):
            return f"sz{code}"
    return ""


def fetch_prices(codes: list[str], timeout: int = 10) -> dict:
    """批量获取 A 股实时行情。

    Args:
        codes: 6 位 A 股代码列表，如 ['600519', '000001']。
        timeout: HTTP 请求超时秒数。

    Returns:
        dict: {code: {name, price, change_pct, pe, pb, market_cap, ...}}
              获取失败的代码不在返回结果中。
    """
    # 过滤和转换代码
    valid_codes = []
    tencent_codes = []
    for c in codes:
        tc = _code_to_tencent(c)
        if tc:
            valid_codes.append(c)
            tencent_codes.append(tc)

    if not tencent_codes:
        return {}

    # 批量查询（腾讯 API 支持逗号分隔多只股票）
    url = f"http://qt.gtimg.cn/q={','.join(tencent_codes)}"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[价格获取] 请求失败: {e}")
        return {}

    # 解析响应
    result = {}
    raw = resp.text
    # 编码可能是 GBK，尝试用 GBK 解码；若失败则回退 UTF-8
    try:
        raw = raw.encode("latin-1").decode("gbk")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass

    for line in raw.strip().split("\n"):
        if not line.strip() or "=" not in line:
            continue
        try:
            # 格式: v_sh600519="1~贵州茅台~..."
            value_str = line.split("=", 1)[1].strip().strip('";')
            parts = value_str.split("~")
            if len(parts) < max(
                _FIELD_NAME, _FIELD_PRICE, _FIELD_CHANGE_PCT,
                _FIELD_PE, _FIELD_PB, _FIELD_MARKET_CAP,
            ) + 1:
                continue

            code = parts[_FIELD_CODE]
            price_str = parts[_FIELD_PRICE]
            if not price_str or price_str == "0.00":
                continue

            result[code] = {
                "name": parts[_FIELD_NAME],
                "code": code,
                "price": _safe_float(price_str),
                "prev_close": _safe_float(parts[_FIELD_PREV_CLOSE]),
                "open": _safe_float(parts[_FIELD_OPEN]),
                "change_pct": _safe_float(parts[_FIELD_CHANGE_PCT]),
                "pe": _safe_float(parts[_FIELD_PE]),
                "pb": _safe_float(parts[_FIELD_PB]),
                "market_cap_yi": _safe_float(parts[_FIELD_MARKET_CAP]),
                "turnover_rate": _safe_float(parts[_FIELD_TURNOVER]),
                "high": _safe_float(parts[_FIELD_HIGH]),
                "low": _safe_float(parts[_FIELD_LOW]),
            }
        except (IndexError, ValueError) as e:
            print(f"[价格获取] 解析失败: {e}")
            continue

    return result


def fetch_single_price(code: str, timeout: int = 10) -> Optional[dict]:
    """获取单只股票的实时行情。

    Args:
        code: 6 位 A 股代码。
        timeout: HTTP 请求超时秒数。

    Returns:
        dict 或 None（获取失败时）。
    """
    result = fetch_prices([code], timeout=timeout)
    return result.get(code)


def _safe_float(value: str) -> Optional[float]:
    """安全转换为 float，失败返回 None。"""
    if not value or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None
