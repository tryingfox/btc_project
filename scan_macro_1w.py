import requests
import concurrent.futures
import urllib3
import urllib.parse

urllib3.disable_warnings()

def _normalize_interval(interval):
    if interval in ("1day", "1d", "day"):
        return "1d"
    return "1w"

def _get_limits(interval):
    if interval == "1d":
        return {"limit": 61, "idx_short": 20, "idx_long": 60, "badge": "20d"}
    return {"limit": 13, "idx_short": 4, "idx_long": 12, "badge": "4w"}

def fetch_data(path, symbol="", interval="1w", limit=13):
    interval = _normalize_interval(interval)
    # 策略1：币安公有现货数据节点 (最快，国内通常不墙)
    # 虽然是现货，但 1w 的宏观趋势与合约几乎完全一致
    url_spot = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        resp = requests.get(url_spot, timeout=5, verify=False)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass

    # 策略2：Gate.io 合约节点 (备用，国内通常不墙)
    gate_symbol = symbol.replace('USDT', '_USDT')
    gate_interval = "7d" if interval == "1w" else "1d"
    url_gate = f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={gate_symbol}&interval={gate_interval}&limit={limit}"
    try:
        resp = requests.get(url_gate, timeout=5, verify=False)
        if resp.status_code == 200:
            # Gate.io 格式转换: [{"c": "123", ...}] -> [[0,0,0,0,"123"], ...]
            data = resp.json()
            return [[0, 0, 0, 0, k['c']] for k in data]
    except:
        pass

    # 策略3：币安合约节点 (可能被墙)
    url_fapi = f"https://fapi.binance.info/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        resp = requests.get(url_fapi, timeout=5, verify=False, proxies={"http": None, "https": None})
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
        
    # 策略4：免费代理
    url_proxy = "http://api.codetabs.com/v1/proxy/?quest=" + urllib.parse.quote(f"http://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}", safe='')
    try:
        resp = requests.get(url_proxy, timeout=10, verify=False)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass

    return None

def get_symbols():
    # 优先使用 Gate.io 获取全量合约，速度最快且不被墙
    try:
        resp = requests.get("https://api.gateio.ws/api/v4/futures/usdt/contracts", timeout=5, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            return [s['name'].replace('_USDT', 'USDT') for s in data if s.get('name', '').endswith('_USDT') and s.get('status') == 'trading']
    except:
        pass
        
    # 备用：币安合约接口
    try:
        resp = requests.get("https://fapi.binance.info/fapi/v1/exchangeInfo", timeout=5, verify=False, proxies={"http": None, "https": None})
        if resp.status_code == 200:
            data = resp.json()
            if 'symbols' in data:
                return [s['symbol'] for s in data['symbols'] if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING' and s['contractType'] == 'PERPETUAL']
    except:
        pass
        
    return []

def get_macro_trend(symbol):
    interval = "1w"
    if isinstance(symbol, tuple):
        symbol, interval = symbol
    interval = _normalize_interval(interval)
    cfg = _get_limits(interval)
    klines = fetch_data("", symbol, interval=interval, limit=cfg["limit"])
    if klines and len(klines) >= cfg["limit"]:
        # MA1 is just the close prices
        closes = [float(k[4]) for k in klines]
        
        current_close = closes[-1]
        close_1w_ago = closes[-2]
        close_4w_ago = closes[-(cfg["idx_short"] + 1)]
        close_12w_ago = closes[-(cfg["idx_long"] + 1)]
        
        chg_1w = (current_close - close_1w_ago) / close_1w_ago * 100
        chg_4w = (current_close - close_4w_ago) / close_4w_ago * 100
        chg_12w = (current_close - close_12w_ago) / close_12w_ago * 100
        
        is_bullish = chg_12w > 0 and chg_4w > 0
        score = chg_4w + (chg_12w * 0.5)
        
        return {
            "symbol": symbol,
            "interval": interval,
            "badge_label": cfg["badge"],
            "chg_1w": chg_1w,
            "chg_4w": chg_4w,
            "chg_12w": chg_12w,
            "score": score,
            "is_bullish": is_bullish,
            "close": current_close
        }
    return None

def run_scan(interval="1w"):
    interval = _normalize_interval(interval)
    symbols = get_symbols()
    if not symbols:
        return []
    
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(get_macro_trend, (sym, interval)): sym for sym in symbols}
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                results.append(res)
                
    bullish_candidates = [r for r in results if r['is_bullish']]
    bullish_candidates.sort(key=lambda x: x['score'], reverse=True)
    return bullish_candidates[:10]

if __name__ == "__main__":
    print("正在扫描全市场周线 (MA1) 宏观趋势结构...")
    symbols = get_symbols()
    if not symbols:
        print("网络请求失败，请检查。")
        exit(1)
    
    print(f"找到 {len(symbols)} 个 USDT 永续合约。正在提取过去12周的数据...")
    results = []
    btc_stats = None
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(get_macro_trend, sym): sym for sym in symbols}
        for i, f in enumerate(concurrent.futures.as_completed(futures)):
            res = f.result()
            if res:
                results.append(res)
                if res['symbol'] == 'BTCUSDT':
                    btc_stats = res
            if (i+1) % 100 == 0:
                print(f"已处理 {i+1}/{len(symbols)}...")
                
    # 筛选出多头品种并按得分排序
    bullish_candidates = [r for r in results if r['is_bullish']]
    bullish_candidates.sort(key=lambda x: x['score'], reverse=True)
    
    print("\n" + "="*60)
    print("📈 基于周线 MA1 结构选出的 TOP 10 强势多头品种 📈")
    print("条件: 12周重心上移 (长牛) 且 4周重心上移 (近一个月未破位)")
    print("="*60)
    print(f"{'合约':<12} | {'本周涨跌':>8} | {'近4周结构涨幅':>12} | {'近12周宏观涨幅':>12}")
    print("-" * 60)
    
    for i, r in enumerate(bullish_candidates[:10]):
        print(f"{i+1:2d}. {r['symbol']:<9} | {r['chg_1w']:>7.2f}% | {r['chg_4w']:>11.2f}% | {r['chg_12w']:>11.2f}%")
        
    print("\n" + "="*60)
    if btc_stats:
        status = "多头结构" if btc_stats['is_bullish'] else "空头结构/回调中"
        print(f"💡 BTCUSDT 当前周线 MA1 状态判定: {status}")
        print(f"   本周涨跌: {btc_stats['chg_1w']:.2f}% | 近4周涨幅: {btc_stats['chg_4w']:.2f}% | 近12周涨幅: {btc_stats['chg_12w']:.2f}%")
    print("="*60)
