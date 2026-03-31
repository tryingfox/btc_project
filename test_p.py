import requests
import time
import concurrent.futures
import urllib.parse
import urllib3
import json

urllib3.disable_warnings()

def fetch(task):
    start = time.time()
    try:
        res = requests.get(task['url'], timeout=5, verify=False, proxies=task['proxies'])
        res.raise_for_status()
        data = res.json()
        
        if task['type'] == 'gateio':
            if isinstance(data, list) and len(data) > 0 and 'c' in data[0]:
                formatted = []
                for k in data:
                    formatted.append([
                        k.get("t", 0) * 1000,
                        k.get("o", "0"),
                        k.get("h", "0"),
                        k.get("l", "0"),
                        k.get("c", "0"),
                        str(k.get("v", "0")),
                        k.get("t", 0) * 1000,
                        k.get("sum", "0"),
                        0, 0, 0, "0"
                    ])
                data = formatted
            else:
                raise Exception("Invalid Gate.io data")
        
        print(f"✅ {task['name']} finished in {time.time()-start:.2f}s with {len(data)} records")
        return task['name'], data
    except Exception as e:
        print(f"❌ {task['name']} failed in {time.time()-start:.2f}s: {e}")
        return task['name'], None

symbol = "BTCUSDT"
gate_symbol = symbol.replace("USDT", "_USDT")
interval = "12h"
limit = 10

tasks = [
    {
        "name": "Binance Spot", 
        "url": f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}",
        "type": "binance",
        "proxies": None
    },
    {
        "name": "Gate.io Futures",
        "url": f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={gate_symbol}&interval={interval}&limit={limit}",
        "type": "gateio",
        "proxies": None
    },
    {
        "name": "Binance FAPI (Direct)",
        "url": f"https://fapi.binance.info/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}",
        "type": "binance",
        "proxies": {"http": None, "https": None}
    }
]

start_all = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(fetch, task) for task in tasks]
    for future in concurrent.futures.as_completed(futures):
        name, data = future.result()
        if data:
            print(f"WINNER: {name} in {time.time()-start_all:.2f}s")
            break
