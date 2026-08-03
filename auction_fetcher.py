"""开盘竞价数据获取模块。

通过东方财富和腾讯行情 API 获取 A 股集合竞价数据，包括：
- 竞价量/竞价额
- 竞价价格（开盘价）
- 竞价量比
- 竞价换手率
- 竞价趋势（9:15-9:25 价格走势）
- 竞价多空力量对比

数据源：
- 东方财富 push2.eastmoney.com（竞价行情）
- 腾讯 qt.gtimg.cn（分时数据）
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from price_fetcher import (
    _code_to_eastmoney,
    _eastmoney_scaled,
    _request_with_retry,
    _safe_float,
    _code_to_tencent,
)

logger = logging.getLogger(__name__)

# 东方财富竞价行情字段
# f43=最新价 f44=最高 f45=最低 f46=开盘 f47=成交量(手) f48=成交额
# f50=量比 f57=代码 f58=名称 f60=昨收 f116=总市值 f117=流通市值
# f168=换手率 f169=涨跌额 f170=涨跌幅 f171=振幅
# f19=竞价量(手) f20=竞价额(元)
AUCTION_FIELDS = "f19,f20,f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f116,f117,f168,f169,f170,f171"

# 批量竞价行情字段（ulist 接口）
AUCTION_LIST_FIELDS = "f2,f3,f10,f12,f14,f17,f18,f20,f23"


def fetch_auction_data(code: str, timeout: int = 10) -> Optional[dict]:
    """获取单只股票竞价数据。

    Args:
        code: 6 位 A 股代码
        timeout: 请求超时秒数

    Returns:
        dict: 竞价数据，失败返回 None
    """
    secid = _code_to_eastmoney(code)
    if not secid:
        return None

    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": secid,
        "fields": AUCTION_FIELDS,
        "ut": "fa5fd1943c7b386f172d6893dbbd1180",
    }

    try:
        resp = _request_with_retry(url, params=params, timeout=timeout)
        data = resp.json() or {}
    except (requests.RequestException, ValueError) as e:
        logger.error("竞价数据获取失败 %s: %s", code, e)
        return None

    d = data.get("data") or {}
    if not d:
        return None

    # 解析竞价数据
    auction_volume = _eastmoney_scaled(d.get("f19"), 100)  # 竞价量（手→股）
    auction_amount = d.get("f20")  # 竞价额（元）
    open_price = _eastmoney_scaled(d.get("f46"))  # 开盘价（竞价结果）
    prev_close = _eastmoney_scaled(d.get("f60"))  # 昨收
    volume = _eastmoney_scaled(d.get("f47"), 100)  # 总成交量（手→股）
    amount = d.get("f48")  # 总成交额
    volume_ratio = _eastmoney_scaled(d.get("f50"))  # 量比
    turnover_rate = _eastmoney_scaled(d.get("f168"))  # 换手率
    change_pct = _eastmoney_scaled(d.get("f170"))  # 涨跌幅
    amplitude = _eastmoney_scaled(d.get("f171"))  # 振幅
    high = _eastmoney_scaled(d.get("f44"))
    low = _eastmoney_scaled(d.get("f45"))
    total_market_cap = d.get("f116")  # 总市值（元）
    float_market_cap = d.get("f117")  # 流通市值（元）

    # 计算竞价衍生指标
    auction_turnover_rate = None
    if auction_volume and float_market_cap and float_market_cap > 0:
        # 竞价换手率 = 竞价量 / 流通股本
        # 流通股本 = 流通市值 / 昨收价
        if prev_close and prev_close > 0:
            float_shares = float_market_cap / prev_close
            if float_shares > 0:
                auction_turnover_rate = round(auction_volume / float_shares * 100, 4)

    # 竞价量占昨日总量比
    auction_volume_ratio = None
    if auction_volume and volume and volume > 0:
        # 注意：volume 此时包含竞价量
        # 竞价量占比 = 竞价量 / (总成交量)
        auction_volume_ratio = round(auction_volume / volume * 100, 2) if volume > 0 else None

    # 竞价涨幅
    auction_change_pct = None
    if open_price and prev_close and prev_close > 0:
        auction_change_pct = round((open_price - prev_close) / prev_close * 100, 2)

    return {
        "code": code,
        "name": d.get("f58", ""),
        "auction_volume": auction_volume,  # 竞价量（股）
        "auction_amount": auction_amount,  # 竞价额（元）
        "open_price": open_price,  # 开盘价
        "prev_close": prev_close,  # 昨收
        "volume": volume,  # 总成交量
        "amount": amount,  # 总成交额
        "volume_ratio": volume_ratio,  # 量比
        "turnover_rate": turnover_rate,  # 换手率
        "auction_turnover_rate": auction_turnover_rate,  # 竞价换手率
        "auction_volume_ratio": auction_volume_ratio,  # 竞价量占比
        "auction_change_pct": auction_change_pct,  # 竞价涨幅
        "change_pct": change_pct,  # 当前涨跌幅
        "amplitude": amplitude,  # 振幅
        "high": high,
        "low": low,
        "total_market_cap_yi": round(total_market_cap / 1e8, 2) if total_market_cap else None,
        "float_market_cap_yi": round(float_market_cap / 1e8, 2) if float_market_cap else None,
    }


def fetch_auction_batch(codes: list[str], timeout: int = 15) -> dict:
    """批量获取竞价数据。

    Args:
        codes: 6 位 A 股代码列表
        timeout: 请求超时秒数

    Returns:
        dict: {code: auction_data}
    """
    result = {}
    # 东方财富 ulist 接口支持批量，但字段有限
    # 对于详细竞价数据，需要逐只获取
    for code in codes:
        data = fetch_auction_data(code, timeout=timeout)
        if data:
            result[code] = data
        time.sleep(0.05)  # 避免请求过快
    return result


def fetch_auction_list(board: str = "a", sort_field: str = "f3",
                      sort_order: int = 0, count: int = 50,
                      timeout: int = 15) -> list[dict]:
    """获取竞价排行榜数据。

    通过东方财富的 ulist 接口获取全市场竞价数据。

    Args:
        board: 板块代码，"a"=A股全部
        sort_field: 排序字段，f3=涨跌幅，f2=最新价，f10=量比
        sort_order: 排序方向，0=降序，1=升序
        count: 返回数量
        timeout: 请求超时秒数

    Returns:
        list[dict]: 竞价数据列表
    """
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": 2,
        "secids": "",  # 空表示全市场
        "fields": AUCTION_LIST_FIELDS,
        "pn": 1,
        "pz": count,
        "po": sort_order,
        "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2,
        "invt": 2,
        "fid": sort_field,
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 沪深A股
    }

    try:
        resp = _request_with_retry(url, params=params, timeout=timeout)
        data = resp.json() or {}
    except (requests.RequestException, ValueError) as e:
        logger.error("竞价排行榜获取失败: %s", e)
        return []

    items = (data.get("data") or {}).get("diff") or []
    result = []
    for item in items:
        code = str(item.get("f12", ""))
        if not code:
            continue
        result.append({
            "code": code,
            "name": item.get("f14", ""),
            "price": item.get("f2"),
            "change_pct": item.get("f3"),
            "volume_ratio": item.get("f10"),  # 量比
            "open_price": item.get("f17"),
            "prev_close": item.get("f18"),
            "market_cap_yi": round(item.get("f20", 0) / 1e8, 2) if item.get("f20") else None,
            "pb": item.get("f23"),
        })
    return result


def fetch_call_auction_trend(code: str, timeout: int = 10) -> list[dict]:
    """获取竞价期间分时趋势（9:15-9:25 价格走势）。

    通过东方财富分时数据接口获取竞价期间的价格变化。

    Args:
        code: 6 位 A 股代码
        timeout: 请求超时秒数

    Returns:
        list[dict]: 分时数据点列表
    """
    secid = _code_to_eastmoney(code)
    if not secid:
        return []

    url = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "iscr": "0",
        "ndays": "1",
        "ut": "fa5fd1943c7b386f172d6893dbbd1180",
    }

    try:
        resp = _request_with_retry(url, params=params, timeout=timeout)
        data = resp.json() or {}
    except (requests.RequestException, ValueError) as e:
        logger.error("竞价趋势获取失败 %s: %s", code, e)
        return []

    trends = data.get("data", {}).get("trends") or []
    result = []
    for t in trends:
        parts = t.split(",")
        if len(parts) >= 8:
            result.append({
                "time": parts[0],  # 时间 HH:MM
                "price": _safe_float(parts[1]),  # 价格
                "avg_price": _safe_float(parts[2]),  # 均价
                "volume": _safe_float(parts[3]),  # 成交量
                "amount": _safe_float(parts[4]),  # 成交额
            })
    return result


def fetch_pre_market_volume_top(count: int = 100, timeout: int = 15) -> list[dict]:
    """获取竞价量比排行榜（全市场）。

    通过东方财富全市场竞价量比排序，找出竞价最活跃的股票。

    Args:
        count: 返回数量
        timeout: 请求超时秒数

    Returns:
        list[dict]: 按量比降序排列的股票列表
    """
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": 2,
        "fields": "f2,f3,f10,f12,f14,f17,f18,f20,f23",
        "pn": 1,
        "pz": count,
        "po": 0,  # 降序
        "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "invt": 2,
        "fid": "f10",  # 按量比排序
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 沪深A股
    }

    try:
        resp = _request_with_retry(url, params=params, timeout=timeout)
        data = resp.json() or {}
    except (requests.RequestException, ValueError) as e:
        logger.error("竞价量比排行获取失败: %s", e)
        return []

    items = (data.get("data") or {}).get("diff") or []
    result = []
    for item in items:
        code = str(item.get("f12", ""))
        volume_ratio = item.get("f10")
        if not code or volume_ratio is None:
            continue
        result.append({
            "code": code,
            "name": item.get("f14", ""),
            "price": item.get("f2"),
            "change_pct": item.get("f3"),
            "volume_ratio": volume_ratio,  # 量比
            "open_price": item.get("f17"),
            "prev_close": item.get("f18"),
            "market_cap_yi": round(item.get("f20", 0) / 1e8, 2) if item.get("f20") else None,
            "pb": item.get("f23"),
        })
    return result


def is_auction_time() -> bool:
    """判断当前是否处于竞价时间段。

    A 股竞价时间：9:15-9:25（集合竞价），9:25-9:30（开盘静默期）
    """
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    current_minutes = now.hour * 60 + now.minute
    # 9:15 = 555, 9:30 = 570
    return 555 <= current_minutes < 570


def is_premarket_time() -> bool:
    """判断当前是否处于盘前分析时间。

    盘前分析时间：8:50-9:15
    """
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    current_minutes = now.hour * 60 + now.minute
    return 530 <= current_minutes < 555
