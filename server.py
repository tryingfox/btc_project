import json
from http.server import SimpleHTTPRequestHandler, HTTPServer
import urllib.request
import urllib.error
import ssl
import sys
import time
import os

# 尝试导入 requests 库，它能更好地处理系统代理和 SSL 证书
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("未安装 requests 库，建议运行: pip install requests 以获得更好的代理支持")

PORT = int(os.environ.get("PORT", "8080"))
CACHE = {}

class ProxyHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        # 预热缓存：/api/preload?symbols=BTCUSDT,ETHUSDT&interval=1w&limit=500
        if self.path.startswith('/api/preload'):
            try:
                import urllib.parse
                qs = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(qs)
                symbols = params.get('symbols', [''])[0]
                interval = params.get('interval', ['1w'])[0]
                limit = params.get('limit', ['500'])[0]
                sym_list = [s.strip().upper() for s in symbols.split(',') if s.strip()]
                if not sym_list:
                    raise ValueError("symbols 参数不能为空")
                # 异步预热，避免阻塞当前请求
                import threading
                def _preheat():
                    for s in sym_list:
                        try:
                            url = f"http://localhost:{PORT}/fapi/v1/klines?symbol={s}&interval={interval}&limit={limit}"
                            requests.get(url, timeout=6)
                        except Exception:
                            pass
                threading.Thread(target=_preheat, daemon=True).start()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "queued": sym_list, "interval": interval}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode('utf-8'))
            return

        if self.path.startswith('/api/scan_top10'):
            import urllib.parse
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            interval = params.get('interval', ['1w'])[0]
            if interval in ('1day', '1d', 'day'):
                interval = '1d'
            else:
                interval = '1w'
            try:
                import scan_macro_1w
                top10 = scan_macro_1w.run_scan(interval=interval, top_n=30)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(top10).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        # 拦截 /kucoin/ 开头的请求
        if self.path.startswith('/kucoin/'):
            kucoin_path = self.path.replace('/kucoin', '')
            import urllib.parse
            
            nodes = [
                ("kucoin.com", f"https://api.kucoin.com{kucoin_path}"),
                ("codetabs (http)", f"http://api.codetabs.com/v1/proxy/?quest={urllib.parse.quote(f'https://api.kucoin.com{kucoin_path}', safe='')}")
            ]
            
            success = False
            error_msgs = []
            
            for node_name, target_url in nodes:
                print(f"正在尝试通过 [{node_name}] 代理请求 Kucoin: {target_url}")
                
                if HAS_REQUESTS:
                    node_success = False
                    # 尝试策略：1. 默认（可能走系统代理） 2. 禁用代理直连
                    strategies = [
                        ("默认代理", None),
                        ("禁用代理", {"http": None, "https": None})
                    ]
                    
                    for strategy_name, proxies in strategies:
                        try:
                            response = requests.get(
                                target_url, 
                                headers={'User-Agent': 'Mozilla/5.0'},
                                timeout=10,
                                verify=False, # 忽略 SSL 验证
                                proxies=proxies
                            )
                            response.raise_for_status()
                            data = response.content
                            
                            self.send_response(200)
                            self.send_header('Content-Type', 'application/json')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(data)
                            print(f"✅ 成功获取 Kucoin 数据 ({node_name} - {strategy_name})")
                            success = True
                            node_success = True
                            break
                        except Exception as e:
                            error_msg = f"节点 {node_name} ({strategy_name}) 失败: {str(e)}"
                            print(f"❌ {error_msg}")
                            error_msgs.append(error_msg)
                            
                    if node_success:
                        break
            
            if not success:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                # 返回 JSON 格式的错误信息
                err_str = " | ".join(error_msgs)
                self.wfile.write(f'{{"msg": "Kucoin 代理全部失败: {err_str}"}}'.encode('utf-8'))
            return

        # 拦截 /fapi/ 开头的请求，代理到币安或更快的替代节点
        elif self.path.startswith('/fapi/'):
            import urllib.parse
            import concurrent.futures
            
            # 解析参数
            query_string = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query_string)
            symbol = params.get('symbol', [''])[0]
            interval = params.get('interval', [''])[0]
            limit = params.get('limit', ['500'])[0]
            if interval == '1day':
                interval = '1d'
            
            target_url_encoded = urllib.parse.quote(f"http://fapi.binance.com{self.path}", safe='')
            
            cache_key = f"{symbol}:{interval}:{limit}"
            ttl = 300 if interval in ('12h', '1w', '1d') else 60
            if cache_key in CACHE:
                ts, cached = CACHE[cache_key]
                if time.time() - ts < ttl:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(cached)
                    return
            
            # 准备并发请求的任务列表
            # 每个任务是一个字典: {"name": "...", "url": "...", "type": "...", "proxies": ...}
            fast_tasks = []
            slow_tasks = []
            
            if HAS_REQUESTS:
                if '/klines' in self.path and symbol and interval:
                    # 1. Gate.io 合约节点
                    gate_interval = interval
                    if interval == '1w': gate_interval = '7d'
                    elif interval == '1day' or interval == '1d': gate_interval = '1d'
                    elif interval == '4hour' or interval == '4h': gate_interval = '4h'
                    
                    gate_symbol = symbol.replace('USDT', '_USDT')
                    is_1000 = False
                    if gate_symbol.startswith('1000'):
                        gate_symbol = gate_symbol[4:]
                        is_1000 = True
                        
                    fast_tasks.append({
                        "name": "jina->gate.io (HTTP)",
                        "url": f"https://r.jina.ai/http://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={gate_symbol}&interval={gate_interval}&limit={limit}",
                        "type": "gateio",
                        "is_1000": is_1000,
                        "proxies": None,
                        "timeout": 2
                    })
                    fast_tasks.append({
                        "name": "gate.io (Futures, no proxy)",
                        "url": f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={gate_symbol}&interval={gate_interval}&limit={limit}",
                        "type": "gateio",
                        "is_1000": is_1000,
                        "proxies": {"http": None, "https": None},
                        "timeout": 2
                    })
                    fast_tasks.append({
                        "name": "gate.io (Futures, with proxy)",
                        "url": f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={gate_symbol}&interval={gate_interval}&limit={limit}",
                        "type": "gateio",
                        "is_1000": is_1000,
                        "proxies": None,
                        "timeout": 2
                    })

                    # 2. Kucoin 现货节点
                    kucoin_interval = interval
                    if interval == '1w': kucoin_interval = '1week'
                    elif interval == '1day' or interval == '1d': kucoin_interval = '1day'
                    elif interval == '12h': kucoin_interval = '12hour'
                    elif interval == '4hour' or interval == '4h': kucoin_interval = '4hour'
                    
                    kucoin_symbol = symbol.replace('USDT', '-USDT')
                    is_1000_kucoin = False
                    if kucoin_symbol.startswith('1000'):
                        kucoin_symbol = kucoin_symbol[4:]
                        is_1000_kucoin = True

                    fast_tasks.append({
                        "name": "jina->kucoin (HTTP)",
                        "url": f"https://r.jina.ai/http://api.kucoin.com/api/v1/market/candles?type={kucoin_interval}&symbol={kucoin_symbol}",
                        "type": "kucoin",
                        "is_1000": is_1000_kucoin,
                        "proxies": None,
                        "timeout": 2
                    })

                    slow_tasks.append({
                        "name": "binance.vision (Spot, no proxy)",
                        "url": f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}",
                        "type": "binance",
                        "proxies": {"http": None, "https": None},
                        "timeout": 4
                    })
                
                # 3. 币安备用直连 (禁用代理)
                slow_tasks.append({
                    "name": "binance.info (direct, no proxy)",
                    "url": f"https://fapi.binance.info{self.path}",
                    "type": "binance",
                    "proxies": {"http": None, "https": None},
                    "timeout": 4
                })
                
                # 4. 币安备用直连 (使用系统代理)
                slow_tasks.append({
                    "name": "binance.info (direct, with proxy)",
                    "url": f"https://fapi.binance.info{self.path}",
                    "type": "binance",
                    "proxies": None,
                    "timeout": 4
                })
                
                if '/klines' not in self.path:
                    slow_tasks.append({
                        "name": "codetabs (http proxy)",
                        "url": f"http://api.codetabs.com/v1/proxy/?quest={target_url_encoded}",
                        "type": "binance",
                        "proxies": None,
                        "timeout": 4
                    })
                
                def fetch_task(task):
                    try:
                        resp = requests.get(
                            task["url"], 
                            headers={'User-Agent': 'Mozilla/5.0'},
                            timeout=task.get("timeout", 3),
                            verify=False,
                            proxies=task["proxies"]
                        )
                        resp.raise_for_status()
                        # 优先按 JSON 解析；若为 Jina 包裹文本，回退到文本抽取
                        try:
                            data = resp.json()
                        except Exception:
                            text = resp.text or ""
                            start_idx = -1
                            for ch in ['[', '{']:
                                idx = text.find(ch)
                                if idx != -1:
                                    start_idx = idx if start_idx == -1 else min(start_idx, idx)
                            if start_idx == -1:
                                raise ValueError("No JSON found in text")
                            # 取到文本末尾，再尝试 json 解析；失败则逐步回退
                            candidate = text[start_idx:].strip()
                            import json as _json
                            parsed = None
                            try:
                                parsed = _json.loads(candidate)
                            except Exception:
                                # 截到最后一个 ] 或 } 再试
                                end_br = max(candidate.rfind(']'), candidate.rfind('}'))
                                if end_br != -1:
                                    candidate = candidate[:end_br+1]
                                    parsed = _json.loads(candidate)
                            if parsed is None:
                                raise ValueError("Jina wrapped response not JSON-decodable")
                            data = parsed
                        
                        # Gate.io 数据格式转换
                        if task["type"] == "gateio":
                            if isinstance(data, list) and len(data) > 0 and 'c' in data[0]:
                                formatted_data = []
                                multiplier = 1000 if task.get("is_1000") else 1
                                for k in data:
                                    formatted_data.append([
                                        k.get("t", 0) * 1000,
                                        str(float(k.get("o", "0")) * multiplier),
                                        str(float(k.get("h", "0")) * multiplier),
                                        str(float(k.get("l", "0")) * multiplier),
                                        str(float(k.get("c", "0")) * multiplier),
                                        str(k.get("v", "0")),
                                        k.get("t", 0) * 1000,
                                        str(k.get("sum", "0")),
                                        0, 0, 0, "0"
                                    ])
                                return task["name"], json.dumps(formatted_data).encode('utf-8')
                            raise ValueError("Invalid Gate.io data format")
                            
                        # Kucoin 数据格式转换
                        if task["type"] == "kucoin":
                            if "data" in data and isinstance(data["data"], list):
                                k_data = data["data"]
                                formatted_data = []
                                multiplier = 1000 if task.get("is_1000") else 1
                                for k in k_data:
                                    formatted_data.append([
                                        int(k[0]) * 1000,
                                        str(float(k[1]) * multiplier),
                                        str(float(k[3]) * multiplier),
                                        str(float(k[4]) * multiplier),
                                        str(float(k[2]) * multiplier),
                                        str(k[5]),
                                        int(k[0]) * 1000,
                                        str(k[6]),
                                        0, 0, 0, "0"
                                    ])
                                # Kucoin returns newest first, Binance returns oldest first
                                formatted_data.reverse()
                                # Limit the number of results
                                if limit:
                                    formatted_data = formatted_data[-int(limit):]
                                return task["name"], json.dumps(formatted_data).encode('utf-8')
                            raise ValueError("Invalid Kucoin data format")
                        
                        # 如果是币安或 Codetabs 返回的币安数据
                        if isinstance(data, (list, dict)):
                            if isinstance(data, dict) and 'code' in data and 'msg' in data:
                                raise ValueError(f"API Error: {data['msg']}")
                            return task["name"], resp.content
                            
                        raise ValueError("Invalid JSON data")
                    except Exception as e:
                        raise Exception(f"{task['name']} failed: {e}")

                success = False
                error_msgs = []
                
                def run_parallel(task_list):
                    if not task_list:
                        return None
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(len(task_list), 6))
                    try:
                        future_to_name = {executor.submit(fetch_task, task): task["name"] for task in task_list}
                        for future in concurrent.futures.as_completed(future_to_name):
                            try:
                                winner_name, result_data = future.result()
                                return winner_name, result_data
                            except Exception as e:
                                error_msgs.append(str(e))
                    finally:
                        executor.shutdown(wait=False, cancel_futures=True)
                    return None

                winner = run_parallel(fast_tasks)
                if winner is None:
                    winner = run_parallel(slow_tasks)

                if winner is not None:
                    winner_name, result_data = winner
                    CACHE[cache_key] = (time.time(), result_data)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(result_data)
                    success = True

                if not success:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    err_str = " | ".join(error_msgs)
                    self.wfile.write(f'{{"msg": "所有节点均请求失败: {err_str}"}}'.encode('utf-8'))
                
                return
            else:
                # Fallback if requests is not installed (should not happen based on earlier checks)
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"msg": "Please install requests module"}')
                return
        else:
            # 其他请求当做普通静态文件处理
            super().do_GET()

if __name__ == '__main__':
    # 消除 urllib3 禁用 SSL 验证时的警告
    if HAS_REQUESTS:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
    print(f"启动本地服务器: http://localhost:{PORT}")
    print("本服务器自带代理功能。如果网页直连失败，请在网页中选择【本地代理(需运行 server.py)】")
    server_address = ('', PORT)
    
    # 使用 ThreadingHTTPServer 支持多线程并发请求，防止预加载阻塞
    try:
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer(server_address, ProxyHTTPRequestHandler)
    except ImportError:
        # 兼容老版本 Python
        import socketserver
        class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
            daemon_threads = True
        httpd = ThreadingHTTPServer(server_address, ProxyHTTPRequestHandler)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
