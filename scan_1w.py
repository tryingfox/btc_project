import requests
import urllib.parse
import concurrent.futures
import urllib3
import time

urllib3.disable_warnings()

def fetch_data(path):
    # Try direct binance.info first
    url1 = f"https://fapi.binance.info{path}"
    try:
        resp = requests.get(url1, timeout=5, verify=False, proxies={"http": None, "https": None})
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    
    # Fallback to codetabs HTTP proxy
    url2 = "http://api.codetabs.com/v1/proxy/?quest=" + urllib.parse.quote(f"http://fapi.binance.com{path}", safe='')
    try:
        resp = requests.get(url2, timeout=10, verify=False)
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None

def get_symbols():
    data = fetch_data("/fapi/v1/exchangeInfo")
    if data and 'symbols' in data:
        return [s['symbol'] for s in data['symbols'] if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING' and s['contractType'] == 'PERPETUAL']
    return []

def get_1w_data(symbol):
    klines = fetch_data(f"/fapi/v1/klines?symbol={symbol}&interval=1w&limit=2")
    if klines and len(klines) > 0:
        # Get the current 1w kline (the last one)
        k = klines[-1]
        open_p = float(k[1])
        close_p = float(k[4])
        if open_p > 0:
            # Calculate the percentage change from the weekly open
            pct = (close_p - open_p) / open_p * 100
            return {"symbol": symbol, "change": pct, "close": close_p}
    return None

if __name__ == "__main__":
    print("Starting market scan...")
    symbols = get_symbols()
    if not symbols:
        print("Failed to fetch symbols. Please check network.")
        exit(1)
    
    print(f"Found {len(symbols)} USDT perpetual contracts. Fetching 1w data...")
    results = []
    
    # Use ThreadPoolExecutor to fetch data concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(get_1w_data, sym): sym for sym in symbols}
        for i, f in enumerate(concurrent.futures.as_completed(futures)):
            res = f.result()
            if res:
                results.append(res)
            if (i+1) % 50 == 0:
                print(f"Processed {i+1}/{len(symbols)}...")
                
    # Sort by percentage change (highest first)
    results.sort(key=lambda x: x['change'], reverse=True)
    
    print("\n" + "="*30)
    print("TOP 10 STRONGEST CONTRACTS (1W)")
    print("="*30)
    for i, r in enumerate(results[:10]):
        print(f"{i+1:2d}. {r['symbol']:<12} {r['change']:>7.2f}%   (Current Close/MA1: {r['close']})")
    print("="*30)
