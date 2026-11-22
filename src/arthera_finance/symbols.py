"""Symbol and company-name resolution across A-shares, HK, US, EU, indices, crypto, FX and futures.

Migrated from aria-code so the core coding CLI stays domain-agnostic while
financial-service resolves Chinese/English company names and bare 6-digit A-share
codes deterministically without network access.
"""
from __future__ import annotations

import re

_VALID_TICKER = re.compile(r"^[A-Za-z0-9.^=/:-]{1,24}$")
_BARE_ASHARE = re.compile(r"^\d{6}$")
_HK_CODE = re.compile(r"^(\d{1,5})\.HK$", re.IGNORECASE)
_CN_PREFIX = re.compile(r"^(SH|SZ|BJ)(\d{6})$", re.IGNORECASE)

# Canonical alias -> (ticker, canonical_name, market)
ALIASES: dict[str, tuple[str, str, str]] = {
    # A-shares — Consumer, Banks, Insurance, Tech, EV, Pharma, Energy
    "贵州茅台": ("600519.SS", "贵州茅台", "CN"),
    "茅台": ("600519.SS", "贵州茅台", "CN"),
    "五粮液": ("000858.SZ", "五粮液", "CN"),
    "泸州老窖": ("000568.SZ", "泸州老窖", "CN"),
    "山西汾酒": ("600809.SS", "山西汾酒", "CN"),
    "伊利股份": ("600887.SS", "伊利股份", "CN"),
    "海天味业": ("603288.SS", "海天味业", "CN"),
    "美的集团": ("000333.SZ", "美的集团", "CN"),
    "格力电器": ("000651.SZ", "格力电器", "CN"),
    "宁德时代": ("300750.SZ", "宁德时代", "CN"),
    "catl": ("300750.SZ", "宁德时代", "CN"),
    "比亚迪": ("002594.SZ", "比亚迪", "CN"),
    "byd": ("002594.SZ", "比亚迪", "CN"),
    "隆基绿能": ("601012.SS", "隆基绿能", "CN"),
    "阳光电源": ("300274.SZ", "阳光电源", "CN"),
    "通威股份": ("600438.SS", "通威股份", "CN"),
    "工商银行": ("601398.SS", "工商银行", "CN"),
    "工行": ("601398.SS", "工商银行", "CN"),
    "农业银行": ("601288.SS", "农业银行", "CN"),
    "农行": ("601288.SS", "农业银行", "CN"),
    "中国银行": ("601988.SS", "中国银行", "CN"),
    "中行": ("601988.SS", "中国银行", "CN"),
    "建设银行": ("601939.SS", "建设银行", "CN"),
    "建行": ("601939.SS", "建设银行", "CN"),
    "交通银行": ("601328.SS", "交通银行", "CN"),
    "交行": ("601328.SS", "交通银行", "CN"),
    "招商银行": ("600036.SS", "招商银行", "CN"),
    "招行": ("600036.SS", "招商银行", "CN"),
    "兴业银行": ("601166.SS", "兴业银行", "CN"),
    "平安银行": ("000001.SZ", "平安银行", "CN"),
    "宁波银行": ("002142.SZ", "宁波银行", "CN"),
    "中国平安": ("601318.SS", "中国平安", "CN"),
    "中国人寿": ("601628.SS", "中国人寿", "CN"),
    "中国太保": ("601601.SS", "中国太保", "CN"),
    "中信证券": ("600030.SS", "中信证券", "CN"),
    "东方财富": ("300059.SZ", "东方财富", "CN"),
    "恒瑞医药": ("600276.SS", "恒瑞医药", "CN"),
    "迈瑞医疗": ("300760.SZ", "迈瑞医疗", "CN"),
    "药明康德": ("603259.SS", "药明康德", "CN"),
    "片仔癀": ("600436.SS", "片仔癀", "CN"),
    "云南白药": ("000538.SZ", "云南白药", "CN"),
    "四环生物": ("000518.SZ", "四环生物", "CN"),
    "斯迪克": ("300806.SZ", "斯迪克", "CN"),
    "中芯国际": ("688981.SS", "中芯国际", "CN"),
    "海康威视": ("002415.SZ", "海康威视", "CN"),
    "立讯精密": ("002475.SZ", "立讯精密", "CN"),
    "京东方": ("000725.SZ", "京东方A", "CN"),
    "工业富联": ("601138.SS", "工业富联", "CN"),
    "寒武纪": ("688256.SS", "寒武纪", "CN"),
    "海光信息": ("688041.SS", "海光信息", "CN"),
    "中际旭创": ("300308.SZ", "中际旭创", "CN"),
    "科大讯飞": ("002230.SZ", "科大讯飞", "CN"),
    "中国神华": ("601088.SS", "中国神华", "CN"),
    "长江电力": ("600900.SS", "长江电力", "CN"),
    "紫金矿业": ("601899.SS", "紫金矿业", "CN"),
    "中国石油": ("601857.SS", "中国石油", "CN"),
    "中国石化": ("600028.SS", "中国石化", "CN"),
    "中国海油": ("600938.SS", "中国海油", "CN"),
    "中国移动": ("600941.SS", "中国移动", "CN"),
    "中国电信": ("601728.SS", "中国电信", "CN"),
    "中国中免": ("601888.SS", "中国中免", "CN"),
    "万华化学": ("600309.SS", "万华化学", "CN"),
    # HK & China ADRs
    "腾讯": ("0700.HK", "腾讯控股", "HK"),
    "腾讯控股": ("0700.HK", "腾讯控股", "HK"),
    "tencent": ("0700.HK", "Tencent Holdings", "HK"),
    "阿里巴巴": ("BABA", "Alibaba Group", "US"),
    "阿里": ("BABA", "Alibaba Group", "US"),
    "alibaba": ("BABA", "Alibaba Group", "US"),
    "美团": ("3690.HK", "美团-W", "HK"),
    "meituan": ("3690.HK", "Meituan", "HK"),
    "小米": ("1810.HK", "小米集团-W", "HK"),
    "小米集团": ("1810.HK", "小米集团-W", "HK"),
    "xiaomi": ("1810.HK", "Xiaomi Corp", "HK"),
    "京东": ("JD", "JD.com", "US"),
    "拼多多": ("PDD", "PDD Holdings", "US"),
    "百度": ("BIDU", "Baidu", "US"),
    "网易": ("NTES", "NetEase", "US"),
    "携程": ("TCOM", "Trip.com", "US"),
    "理想汽车": ("LI", "Li Auto", "US"),
    "蔚来": ("NIO", "NIO Inc", "US"),
    "小鹏汽车": ("XPEV", "XPeng Inc", "US"),
    "快手": ("1024.HK", "快手-W", "HK"),
    "港交所": ("0388.HK", "香港交易所", "HK"),
    "友邦保险": ("1299.HK", "友邦保险", "HK"),
    "汇丰控股": ("0005.HK", "汇丰控股", "HK"),
    # US & Global equities
    "苹果": ("AAPL", "Apple Inc.", "US"),
    "apple": ("AAPL", "Apple Inc.", "US"),
    "微软": ("MSFT", "Microsoft Corp.", "US"),
    "microsoft": ("MSFT", "Microsoft Corp.", "US"),
    "英伟达": ("NVDA", "NVIDIA Corp.", "US"),
    "nvidia": ("NVDA", "NVIDIA Corp.", "US"),
    "特斯拉": ("TSLA", "Tesla Inc.", "US"),
    "tesla": ("TSLA", "Tesla Inc.", "US"),
    "谷歌": ("GOOGL", "Alphabet Inc.", "US"),
    "google": ("GOOGL", "Alphabet Inc.", "US"),
    "alphabet": ("GOOGL", "Alphabet Inc.", "US"),
    "亚马逊": ("AMZN", "Amazon.com Inc.", "US"),
    "amazon": ("AMZN", "Amazon.com Inc.", "US"),
    "meta": ("META", "Meta Platforms Inc.", "US"),
    "脸书": ("META", "Meta Platforms Inc.", "US"),
    "台积电": ("TSM", "Taiwan Semiconductor", "US"),
    "tsmc": ("TSM", "Taiwan Semiconductor", "US"),
    "博通": ("AVGO", "Broadcom Inc.", "US"),
    "broadcom": ("AVGO", "Broadcom Inc.", "US"),
    "超威半导体": ("AMD", "Advanced Micro Devices", "US"),
    "amd": ("AMD", "Advanced Micro Devices", "US"),
    "英特尔": ("INTC", "Intel Corp.", "US"),
    "intel": ("INTC", "Intel Corp.", "US"),
    "奈飞": ("NFLX", "Netflix Inc.", "US"),
    "netflix": ("NFLX", "Netflix Inc.", "US"),
    "阿斯麦": ("ASML", "ASML Holding", "US"),
    "asml": ("ASML", "ASML Holding", "US"),
    "甲骨文": ("ORCL", "Oracle Corp.", "US"),
    "伯克希尔": ("BRK-B", "Berkshire Hathaway", "US"),
    "摩根大通": ("JPM", "JPMorgan Chase", "US"),
    "高盛": ("GS", "Goldman Sachs", "US"),
    "礼来": ("LLY", "Eli Lilly", "US"),
    "诺和诺德": ("NVO", "Novo Nordisk", "US"),
    "路易威登": ("MC.PA", "LVMH Moet Hennessy Louis Vuitton SE", "EU"),
    "路易斯威登": ("MC.PA", "LVMH Moet Hennessy Louis Vuitton SE", "EU"),
    "lvmh": ("MC.PA", "LVMH Moet Hennessy Louis Vuitton SE", "EU"),
    "louis vuitton": ("MC.PA", "LVMH Moet Hennessy Louis Vuitton SE", "EU"),
    "爱马仕": ("RMS.PA", "Hermes International SCA", "EU"),
    "hermes": ("RMS.PA", "Hermes International SCA", "EU"),
    "开云集团": ("KER.PA", "Kering SA", "EU"),
    "古驰": ("KER.PA", "Kering SA", "EU"),
    # Indices
    "上证指数": ("000001.SS", "上证综合指数", "INDEX"),
    "上证": ("000001.SS", "上证综合指数", "INDEX"),
    "深证成指": ("399001.SZ", "深证成份指数", "INDEX"),
    "创业板指": ("399006.SZ", "创业板指数", "INDEX"),
    "沪深300": ("000300.SS", "沪深300指数", "INDEX"),
    "中证500": ("000905.SS", "中证500指数", "INDEX"),
    "恒生指数": ("^HSI", "Hang Seng Index", "INDEX"),
    "恒指": ("^HSI", "Hang Seng Index", "INDEX"),
    "标普500": ("^GSPC", "S&P 500", "INDEX"),
    "标普": ("^GSPC", "S&P 500", "INDEX"),
    "纳斯达克": ("^IXIC", "NASDAQ Composite", "INDEX"),
    "纳指": ("^IXIC", "NASDAQ Composite", "INDEX"),
    "道琼斯": ("^DJI", "Dow Jones Industrial Average", "INDEX"),
    "道指": ("^DJI", "Dow Jones Industrial Average", "INDEX"),
    "罗素2000": ("^RUT", "Russell 2000", "INDEX"),
    "恐慌指数": ("^VIX", "CBOE Volatility Index", "INDEX"),
    "vix": ("^VIX", "CBOE Volatility Index", "INDEX"),
    "日经225": ("^N225", "Nikkei 225", "INDEX"),
    "富时100": ("^FTSE", "FTSE 100", "INDEX"),
    "德国dax": ("^GDAXI", "DAX", "INDEX"),
    # Crypto, FX, Commodities
    "比特币": ("BTC-USD", "Bitcoin", "CRYPTO"),
    "bitcoin": ("BTC-USD", "Bitcoin", "CRYPTO"),
    "btc": ("BTC-USD", "Bitcoin", "CRYPTO"),
    "以太坊": ("ETH-USD", "Ethereum", "CRYPTO"),
    "ethereum": ("ETH-USD", "Ethereum", "CRYPTO"),
    "eth": ("ETH-USD", "Ethereum", "CRYPTO"),
    "索拉纳": ("SOL-USD", "Solana", "CRYPTO"),
    "solana": ("SOL-USD", "Solana", "CRYPTO"),
    "狗狗币": ("DOGE-USD", "Dogecoin", "CRYPTO"),
    "黄金": ("GC=F", "Gold Futures", "FUTURES"),
    "gold": ("GC=F", "Gold Futures", "FUTURES"),
    "白银": ("SI=F", "Silver Futures", "FUTURES"),
    "silver": ("SI=F", "Silver Futures", "FUTURES"),
    "原油": ("CL=F", "WTI Crude Oil Futures", "FUTURES"),
    "wti原油": ("CL=F", "WTI Crude Oil Futures", "FUTURES"),
    "布伦特原油": ("BZ=F", "Brent Crude Oil Futures", "FUTURES"),
    "天然气": ("NG=F", "Natural Gas Futures", "FUTURES"),
    "铜期货": ("HG=F", "Copper Futures", "FUTURES"),
    "美元人民币": ("CNY=X", "USD/CNY", "FX"),
    "人民币汇率": ("CNY=X", "USD/CNY", "FX"),
    "美元指数": ("DX-Y.NYB", "US Dollar Index", "FX"),
    "欧元美元": ("EURUSD=X", "EUR/USD", "FX"),
    "美元日元": ("JPY=X", "USD/JPY", "FX"),
}


def _ashare_suffix(code: str) -> str:
    if code.startswith(("6", "9", "5")):
        return f"{code}.SS"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


_DYNAMIC_UNIVERSE_CACHE: list[tuple[str, str, str]] | None = None


def _load_dynamic_akshare_universe() -> list[tuple[str, str, str]]:
    """Fetch A-share and HK name-to-code rows via akshare when installed and networking is enabled."""
    global _DYNAMIC_UNIVERSE_CACHE
    if _DYNAMIC_UNIVERSE_CACHE is not None:
        return _DYNAMIC_UNIVERSE_CACHE
    rows: list[tuple[str, str, str]] = []
    try:
        import akshare as ak
        frame = ak.stock_info_a_code_name()
        cols = {str(c).lower(): c for c in frame.columns}
        name_col = next((cols[c] for c in ("name", "证券简称", "股票简称", "名称") if c in cols), None)
        code_col = next((cols[c] for c in ("code", "证券代码", "股票代码", "代码") if c in cols), None)
        if name_col and code_col:
            for _, row in frame.iterrows():
                name = str(row.get(name_col, "")).strip()
                code = str(row.get(code_col, "")).strip()
                if name and _BARE_ASHARE.fullmatch(code):
                    rows.append((name, _ashare_suffix(code), "CN"))
    except Exception:
        pass
    _DYNAMIC_UNIVERSE_CACHE = rows
    return rows


def resolve_symbol(query: str, *, allow_network: bool = False) -> dict:
    """Resolve a company name, Chinese alias, bare 6-digit A-share code, or ticker into a standard symbol."""
    if not isinstance(query, str) or not query.strip() or len(query) > 80:
        raise ValueError("query must be a non-empty string of at most 80 characters")
    raw = query.strip()
    key = raw.lower()
    if raw in ALIASES:
        sym, name, market = ALIASES[raw]
        return {"success": True, "query": raw, "symbol": sym, "name": name, "market": market, "resolution": "alias"}
    if key in ALIASES:
        sym, name, market = ALIASES[key]
        return {"success": True, "query": raw, "symbol": sym, "name": name, "market": market, "resolution": "alias"}
    if _BARE_ASHARE.fullmatch(raw):
        sym = _ashare_suffix(raw)
        return {"success": True, "query": raw, "symbol": sym, "name": raw, "market": "CN", "resolution": "ashare_code"}
    m_cn = _CN_PREFIX.fullmatch(raw)
    if m_cn:
        prefix, code = m_cn.group(1).upper(), m_cn.group(2)
        suffix = "SS" if prefix == "SH" else prefix
        sym = f"{code}.{suffix}"
        return {"success": True, "query": raw, "symbol": sym, "name": code, "market": "CN", "resolution": "ashare_prefix"}
    m_hk = _HK_CODE.fullmatch(raw)
    if m_hk:
        sym = f"{int(m_hk.group(1)):04d}.HK"
        return {"success": True, "query": raw, "symbol": sym, "name": sym, "market": "HK", "resolution": "hk_padded"}
    for alias, (sym, name, market) in ALIASES.items():
        if len(alias) >= 2 and (alias in raw or alias in key):
            return {"success": True, "query": raw, "symbol": sym, "name": name, "market": market, "resolution": "substring_alias"}
    if allow_network:
        for name, sym, market in _load_dynamic_akshare_universe():
            if name == raw or (len(name) >= 2 and name in raw):
                return {"success": True, "query": raw, "symbol": sym, "name": name, "market": market, "resolution": "akshare_universe"}
    candidate = raw.upper()
    if _VALID_TICKER.fullmatch(candidate) and ".." not in candidate and candidate[0] not in "./":
        market = ("CN" if candidate.endswith((".SS", ".SZ", ".BJ")) else
                  "HK" if candidate.endswith(".HK") else
                  "INDEX" if candidate.startswith("^") else
                  "FUTURES" if candidate.endswith("=F") else
                  "FX" if candidate.endswith("=X") else
                  "CRYPTO" if candidate.endswith("-USD") else "US")
        return {"success": True, "query": raw, "symbol": candidate, "name": candidate, "market": market, "resolution": "passthrough"}
    raise ValueError("Could not resolve query to a supported market symbol")


def normalize_symbol(symbol: str) -> str:
    """Resolve Chinese/English company aliases or bare 6-digit A-share codes if recognized; otherwise return as-is."""
    if not isinstance(symbol, str):
        return symbol
    raw = symbol.strip()
    if raw in ALIASES:
        return ALIASES[raw][0]
    if raw.lower() in ALIASES:
        return ALIASES[raw.lower()][0]
    if _BARE_ASHARE.fullmatch(raw):
        return _ashare_suffix(raw)
    m_cn = _CN_PREFIX.fullmatch(raw)
    if m_cn:
        prefix, code = m_cn.group(1).upper(), m_cn.group(2)
        return f"{code}.{'SS' if prefix == 'SH' else prefix}"
    m_hk = _HK_CODE.fullmatch(raw)
    if m_hk:
        return f"{int(m_hk.group(1)):04d}.HK"
    return symbol
