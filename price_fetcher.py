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
_technical_cache: dict[str, Optional[dict]] = {}
_market_environment_cache: Optional[dict] = None


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


def _code_to_eastmoney(code: str) -> str:
    """将 6 位 A 股代码转为东方财富 secid。"""
    code = code.strip()
    if not code or not code.isdigit() or len(code) != 6:
        return ""
    if code.startswith("6"):
        return f"1.{code}"
    if code.startswith(("0", "3")):
        return f"0.{code}"
    return ""


def _eastmoney_scaled(value, scale: float = 100.0) -> Optional[float]:
    """东方财富行情字段常用整数缩放，'-' 或 None 返回 None。"""
    if value in (None, "", "-"):
        return None
    try:
        return float(value) / scale
    except (TypeError, ValueError):
        return None


def _fetch_eastmoney_quotes(codes: list[str], timeout: int = 10) -> dict:
    """使用东方财富行情接口补充实时行情和总市值。

    f20 为总市值（元），这里统一转为亿元。
    """
    secid_to_code = {}
    secids = []
    for code in codes:
        secid = _code_to_eastmoney(code)
        if secid:
            secids.append(secid)
            secid_to_code[secid] = code
    if not secids:
        return {}

    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "secids": ",".join(secids),
        "fields": "f12,f14,f2,f3,f9,f17,f18,f20,f21,f23",
    }
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[价格兜底] 东方财富请求失败: {e}", flush=True)
        return {}
    except ValueError:
        print("[价格兜底] 东方财富 JSON 解析失败", flush=True)
        return {}

    result = {}
    for item in (data.get("data") or {}).get("diff") or []:
        code = str(item.get("f12") or "")
        if not code:
            continue
        market_cap_yi = None
        try:
            f20 = item.get("f20")
            if f20 not in (None, "", "-"):
                market_cap_yi = round(float(f20) / 100000000, 2)
        except (TypeError, ValueError):
            market_cap_yi = None
        result[code] = {
            "name": item.get("f14") or "",
            "code": code,
            "price": _eastmoney_scaled(item.get("f2")),
            "prev_close": _eastmoney_scaled(item.get("f18")),
            "open": _eastmoney_scaled(item.get("f17")),
            "change_pct": _eastmoney_scaled(item.get("f3")),
            "pe": _eastmoney_scaled(item.get("f9")),
            "pb": _eastmoney_scaled(item.get("f23")),
            "market_cap_yi": market_cap_yi,
        }
    return result


def _merge_quote_info(primary: dict, fallback: dict) -> dict:
    """只用 fallback 补齐 primary 的缺失字段。"""
    merged = dict(primary or {})
    for key, value in (fallback or {}).items():
        if key not in merged or merged.get(key) in (None, "", 0):
            if value not in (None, ""):
                merged[key] = value
    return merged


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
    raw = ""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[价格获取] 请求失败: {e}")
    else:
        raw = resp.text

    # 解析响应
    result = {}
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

    # 东方财富兜底：补齐腾讯缺失的总市值，必要时补齐整只股票行情。
    need_fallback = [
        c for c in valid_codes
        if c not in _price_cache or _price_cache[c].get("market_cap_yi") in (None, 0)
    ]
    if need_fallback:
        fallback_quotes = _fetch_eastmoney_quotes(need_fallback, timeout=timeout)
        filled_market_caps = 0
        for code, fallback in fallback_quotes.items():
            before = _price_cache.get(code, {}).get("market_cap_yi")
            merged = _merge_quote_info(_price_cache.get(code, {}), fallback)
            if not merged.get("code"):
                merged["code"] = code
            _price_cache[code] = merged
            result[code] = merged
            after = merged.get("market_cap_yi")
            if before in (None, 0) and after not in (None, 0):
                filled_market_caps += 1
        if filled_market_caps:
            print(f"[价格兜底] 东方财富补齐 {filled_market_caps} 只股票总市值", flush=True)

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
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
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

        stock_data = (data.get("data") or {}).get(tc, {})
        # 优先使用前复权数据
        klines = (stock_data.get("qfqday") or []) or (stock_data.get("day") or [])

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


def _ma(values: list[float], n: int) -> Optional[float]:
    """计算最近 n 日均线。"""
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _atr(closes: list[float], highs: list[float], lows: list[float], n: int = 14) -> Optional[float]:
    """计算 ATR（平均真实波幅）。

    TR = max(high - low, |high - prev_close|, |low - prev_close|)
    ATR = SMA(TR, n)
    """
    if len(closes) < n + 1 or len(highs) < n + 1 or len(lows) < n + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def _rsi(closes: list[float], n: int = 14) -> Optional[float]:
    """计算 RSI（相对强弱指标）。

    RSI = 100 - 100 / (1 + avg_gain / avg_loss)
    使用 Wilder 平滑（指数移动平均）。
    """
    if len(closes) < n + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(0, delta))
        losses.append(max(0, -delta))
    # 初始平均
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    # Wilder 平滑
    for i in range(n, len(gains)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def _macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """计算 MACD（指数平滑异同移动平均线）。

    Returns:
        (macd_line, signal_line, histogram)
        - macd_line = EMA(fast) - EMA(slow)
        - signal_line = EMA(macd_line, signal)
        - histogram = macd_line - signal_line
    """
    if len(closes) < slow + signal:
        return None, None, None

    def _ema(values: list[float], n: int) -> list[float]:
        if len(values) < n:
            return []
        k = 2 / (n + 1)
        result = [sum(values[:n]) / n]
        for v in values[n:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    if not ema_fast or not ema_slow:
        return None, None, None
    # 对齐长度
    offset = len(ema_fast) - len(ema_slow)
    macd_line = [ema_fast[offset + i] - ema_slow[i] for i in range(len(ema_slow))]
    if len(macd_line) < signal:
        return None, None, None
    signal_line = _ema(macd_line, signal)
    if not signal_line:
        return None, None, None
    offset2 = len(macd_line) - len(signal_line)
    histogram = macd_line[-1] - signal_line[-1]
    return round(macd_line[-1], 3), round(signal_line[-1], 3), round(histogram, 3)


def _fetch_one_technical(tc: str, code: str, timeout: int) -> Optional[dict]:
    """获取单只股票技术指标快照。"""
    try:
        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={tc},day,,,40,qfq"
        )
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            print(f"[技术指标] {code} API 返回异常 code={data.get('code')}", flush=True)
            return None

        stock_data = (data.get("data") or {}).get(tc, {})
        klines = (stock_data.get("qfqday") or []) or (stock_data.get("day") or [])
        if len(klines) < 20:
            print(f"[技术指标] {code} K 线数据不足（仅 {len(klines)} 条）", flush=True)
            return None

        closes = [float(k[2]) for k in klines if len(k) >= 3]
        highs = [float(k[3]) for k in klines if len(k) >= 4]
        lows = [float(k[4]) for k in klines if len(k) >= 5]
        volumes = [float(k[5]) for k in klines if len(k) >= 6]
        if len(closes) < 20 or len(lows) < 20:
            return None

        close = closes[-1]
        ma5 = _ma(closes, 5)
        ma10 = _ma(closes, 10)
        ma20 = _ma(closes, 20)
        low20 = min(lows[-20:])
        high20 = max(float(k[3]) for k in klines[-20:] if len(k) >= 4)
        change_5d = round((close / closes[-6] - 1) * 100, 2) if len(closes) >= 6 and closes[-6] else None
        change_20d = round((close / closes[-20] - 1) * 100, 2) if closes[-20] else None
        vol_ratio = None
        if len(volumes) >= 6:
            avg_vol5_prev = sum(volumes[-6:-1]) / 5
            if avg_vol5_prev > 0:
                vol_ratio = round(volumes[-1] / avg_vol5_prev, 2)

        above_ma5 = ma5 is not None and close >= ma5
        above_ma10 = ma10 is not None and close >= ma10
        above_ma20 = ma20 is not None and close >= ma20
        ma_bullish = ma5 is not None and ma10 is not None and ma20 is not None and ma5 >= ma10 >= ma20
        distance_ma5_pct = round((close / ma5 - 1) * 100, 2) if ma5 else None
        distance_ma20_pct = round((close / ma20 - 1) * 100, 2) if ma20 else None
        position_20d = None
        if high20 > low20:
            position_20d = round((close - low20) / (high20 - low20) * 100, 1)

        # ATR(14) 及距5日线的 ATR 归一化距离
        atr_14 = _atr(closes, highs, lows, 14)
        distance_ma5_atr = None
        if ma5 and atr_14 and atr_14 > 0:
            distance_ma5_atr = round((close - ma5) / atr_14, 2)

        # RSI(14)
        rsi_14 = _rsi(closes, 14)

        # MACD(12, 26, 9)
        macd_line, macd_signal, macd_hist = _macd(closes, 12, 26, 9)

        return {
            "close": close,
            "ma5": round(ma5, 3) if ma5 else None,
            "ma10": round(ma10, 3) if ma10 else None,
            "ma20": round(ma20, 3) if ma20 else None,
            "above_ma5": above_ma5,
            "above_ma10": above_ma10,
            "above_ma20": above_ma20,
            "ma_bullish": ma_bullish,
            "distance_ma5_pct": distance_ma5_pct,
            "distance_ma20_pct": distance_ma20_pct,
            "position_20d": position_20d,
            "change_5d": change_5d,
            "change_20d": change_20d,
            "volume_ratio": vol_ratio,
            "atr_14": round(atr_14, 3) if atr_14 else None,
            "distance_ma5_atr": distance_ma5_atr,
            "rsi_14": rsi_14,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
        }
    except requests.Timeout:
        print(f"[技术指标] {code} 请求超时（{timeout}s）", flush=True)
    except requests.RequestException as e:
        print(f"[技术指标] {code} 请求失败: {e}", flush=True)
    except (ValueError, IndexError, TypeError) as e:
        print(f"[技术指标] {code} 数据格式异常: {e}", flush=True)
    return None


def fetch_technical_indicators(
    codes: list[str],
    timeout: int = 10,
    max_workers: int = 5,
) -> dict:
    """批量获取 A 股技术指标快照。"""
    result = {}
    uncached = []
    for code in codes:
        if code in _technical_cache:
            cached = _technical_cache[code]
            if cached is not None:
                result[code] = cached
        else:
            uncached.append(code)

    tasks = []
    for code in uncached:
        tc = _code_to_tencent(code)
        if tc:
            tasks.append((tc, code))
        else:
            _technical_cache[code] = None

    if tasks:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
            future_map = {
                executor.submit(_fetch_one_technical, tc, code, timeout): code
                for tc, code in tasks
            }
            for future in as_completed(future_map):
                code = future_map[future]
                try:
                    indicators = future.result()
                except Exception as e:
                    print(f"[技术指标] {code} 线程异常: {e}", flush=True)
                    indicators = None
                _technical_cache[code] = indicators
                if indicators is not None:
                    result[code] = indicators

    return result


def fetch_market_environment(timeout: int = 10) -> dict:
    """获取主要指数技术环境，返回买入过滤所需的市场状态。"""
    global _market_environment_cache
    if _market_environment_cache is not None:
        return _market_environment_cache

    indices = [
        ("sh000001", "上证指数"),
        ("sz399001", "深证成指"),
        ("sz399006", "创业板指"),
        ("sh000300", "沪深300"),
        ("sh000688", "科创50"),
    ]
    snapshots = []
    for tc, name in indices:
        technical = _fetch_one_technical(tc, tc, timeout)
        if technical:
            snapshots.append({"name": name, **technical})

    if not snapshots:
        _market_environment_cache = {
            "level": "未知",
            "desc": "主要指数技术数据不足，暂按候选池环境判断",
            "buy_penalty": 0.0,
            "buy_bonus": 0.0,
            "indices": [],
        }
        return _market_environment_cache

    total = len(snapshots)
    above20 = sum(1 for item in snapshots if item.get("above_ma20")) / total
    bullish = sum(1 for item in snapshots if item.get("ma_bullish")) / total
    avg_change_5d = sum(item.get("change_5d") or 0 for item in snapshots) / total
    overheated = sum(
        1 for item in snapshots
        if (item.get("change_5d") is not None and item["change_5d"] > 6)
        or (item.get("position_20d") is not None and item["position_20d"] > 88)
        or (item.get("distance_ma20_pct") is not None and item["distance_ma20_pct"] > 7)
    ) / total

    if above20 >= 0.6 and bullish >= 0.4 and avg_change_5d > -1 and overheated <= 0.4:
        level = "偏强"
        penalty = 0.0
        bonus = 0.25
    elif above20 <= 0.4 or avg_change_5d < -3:
        level = "偏弱"
        penalty = 0.8
        bonus = 0.0
    elif overheated > 0.5:
        level = "过热"
        penalty = 0.55
        bonus = 0.0
    else:
        level = "中性"
        penalty = 0.25
        bonus = 0.0

    desc = (
        f"主要指数站上20日线占比{above20:.0%}，均线多头占比{bullish:.0%}，"
        f"5日平均涨跌{avg_change_5d:+.2f}%，过热占比{overheated:.0%}"
    )
    _market_environment_cache = {
        "level": level,
        "desc": desc,
        "buy_penalty": penalty,
        "buy_bonus": bonus,
        "indices": snapshots,
    }
    return _market_environment_cache


def _safe_float(value: str) -> Optional[float]:
    """安全转换为 float，失败返回 None。"""
    if not value or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None
