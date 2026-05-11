"""实时股价获取模块。

使用腾讯行情 API（qt.gtimg.cn）批量获取 A 股实时数据，
包括当前价格、涨跌幅、动态PE、市净率、总市值等。
"""

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# ── 内存缓存：同一 code 在同一次运行中不重复请求 ──
_price_cache: dict[str, dict] = {}
_change_5d_cache: dict[str, Optional[float]] = {}


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
    """批量获取 A 股实时行情（带内存缓存）。

    Args:
        codes: 6 位 A 股代码列表，如 ['600519', '000001']。
        timeout: HTTP 请求超时秒数。

    Returns:
        dict: {code: {name, price, change_pct, pe, pb, market_cap, ...}}
              获取失败的代码不在返回结果中。
    """
    # 过滤已缓存的代码
    uncached = [c for c in codes if c not in _price_cache]
    if not uncached:
        return {c: _price_cache[c] for c in codes if c in _price_cache}

    # 转换代码格式
    valid_codes = []
    tencent_codes = []
    for c in uncached:
        tc = _code_to_tencent(c)
        if tc:
            valid_codes.append(c)
            tencent_codes.append(tc)

    if not tencent_codes:
        return {c: _price_cache[c] for c in codes if c in _price_cache}

    # 批量查询（腾讯 API 支持逗号分隔多只股票）
    url = f"http://qt.gtimg.cn/q={','.join(tencent_codes)}"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[价格获取] 请求失败: {e}")
        return {c: _price_cache[c] for c in codes if c in _price_cache}

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

            info = {
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
            result[code] = info
            _price_cache[code] = info  # 写入缓存
        except (IndexError, ValueError) as e:
            print(f"[价格获取] 解析失败: {e}")
            continue

    # 合并缓存结果
    full_result = {}
    for c in codes:
        if c in _price_cache:
            full_result[c] = _price_cache[c]
    return full_result


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


def _fetch_one_5day_change(tc: str, code: str, timeout: int) -> Optional[float]:
    """获取单只股票的 5 日涨跌幅（内部函数，供并发调用）。

    Args:
        tc: 腾讯 API 前缀代码（如 sh600519）。
        code: 原始 6 位代码。
        timeout: HTTP 请求超时秒数。

    Returns:
        (code, change_pct) 或 (code, None)（获取失败时）。
    """
    try:
        url = (
            f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={tc},day,,,10,qfq"
        )
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()

        try:
            data = resp.json()
        except ValueError:
            print(f"[5日涨跌] {code} JSON 解析失败", flush=True)
            return None

        if data.get("code") != 0:
            print(f"[5日涨跌] {code} API 返回异常 code={data.get('code')}", flush=True)
            return None

        stock_data = data.get("data", {}).get(tc, {})
        # 优先使用前复权数据
        klines = stock_data.get("qfqday", []) or stock_data.get("day", [])

        if not klines or len(klines) < 2:
            print(f"[5日涨跌] {code} K 线数据不足（仅 {len(klines)} 条）", flush=True)
            return None

        # K 线格式: [日期, 开盘, 收盘, 最高, 最低, 成交量]
        latest_close = float(klines[-1][2])
        # 取最近第 6 条 K 线（≈5 个交易日前）
        idx_5d_ago = max(0, len(klines) - 6)
        close_5d_ago = float(klines[idx_5d_ago][2])

        if close_5d_ago <= 0:
            print(f"[5日涨跌] {code} 5 日前收盘价为 0", flush=True)
            return None

        return round((latest_close / close_5d_ago - 1) * 100, 2)

    except requests.Timeout:
        print(f"[5日涨跌] {code} 请求超时（{timeout}s）", flush=True)
        return None
    except requests.ConnectionError:
        print(f"[5日涨跌] {code} 网络连接失败", flush=True)
        return None
    except requests.HTTPError as e:
        print(f"[5日涨跌] {code} HTTP {e.response.status_code if e.response else '?'}", flush=True)
        return None
    except (ValueError, IndexError, TypeError) as e:
        print(f"[5日涨跌] {code} 数据格式异常: {e}", flush=True)
        return None
    except Exception as e:
        print(f"[5日涨跌] {code} 未知错误: {type(e).__name__}: {e}", flush=True)
        return None


def fetch_5day_changes(codes: list[str], timeout: int = 10, max_workers: int = 5) -> dict:
    """批量获取 A 股最近 5 个交易日涨跌幅（并发请求 + 内存缓存）。

    使用腾讯前复权日 K 线 API，比较最新收盘价与 5 个交易日前收盘价。

    Args:
        codes: 6 位 A 股代码列表。
        timeout: HTTP 请求超时秒数。
        max_workers: 并发线程数。

    Returns:
        dict: {code: change_pct (float)}，获取失败的代码不在结果中。
    """
    # 分离已缓存和待请求
    result = {}
    uncached = []
    for code in codes:
        if code in _change_5d_cache:
            cached = _change_5d_cache[code]
            if cached is not None:
                result[code] = cached
        else:
            uncached.append(code)

    if not uncached:
        return result

    # 准备并发任务
    tasks = []
    for code in uncached:
        tc = _code_to_tencent(code)
        if tc:
            tasks.append((tc, code))
        else:
            _change_5d_cache[code] = None  # 无效代码标记

    if not tasks:
        return result

    # 并发请求 K 线数据
    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        future_map = {
            executor.submit(_fetch_one_5day_change, tc, code, timeout): code
            for tc, code in tasks
        }
        for future in as_completed(future_map):
            code = future_map[future]
            try:
                change_pct = future.result()
                _change_5d_cache[code] = change_pct
                if change_pct is not None:
                    result[code] = change_pct
            except Exception as e:
                print(f"[5日涨跌] {code} 线程异常: {e}", flush=True)
                _change_5d_cache[code] = None

    return result


def _safe_float(value: str) -> Optional[float]:
    """安全转换为 float，失败返回 None。"""
    if not value or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None
