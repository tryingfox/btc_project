// 初始化图表实例
let chart;
let candlestickSeries;
let ma1Series;

// 全局缓存对象，用于预加载
const klineDataCache = {};

const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:8080' : window.location.origin;

// DOM 元素
const symbolInput = document.getElementById('symbol');
const intervalSelect = document.getElementById('interval');
const apiNodeSelect = document.getElementById('api-node');
const fetchBtn = document.getElementById('fetch-btn');
const loadingEl = document.getElementById('loading');
const errorEl = document.getElementById('error-message');
const chartContainer = document.getElementById('chart');

// 初始化图表
function initChart() {
    // 销毁旧图表
    if (chart) {
        chart.remove();
    }

    // 创建新图表
    chart = LightweightCharts.createChart(chartContainer, {
        width: chartContainer.clientWidth,
        height: chartContainer.clientHeight,
        layout: {
            background: { type: 'solid', color: '#1e222d' },
            textColor: '#d1d4dc',
        },
        grid: {
            vertLines: { color: '#2b313f' },
            horzLines: { color: '#2b313f' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: '#2b313f',
        },
        timeScale: {
            borderColor: '#2b313f',
            timeVisible: true,
        },
    });

    // 添加K线系列
    candlestickSeries = chart.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderVisible: false,
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
    });

    // 添加MA1均线系列
    ma1Series = chart.addLineSeries({
        color: '#2962FF',
        lineWidth: 2,
        title: 'MA1',
        crosshairMarkerVisible: true,
        lastValueVisible: true,
        priceLineVisible: true,
    });

    // 监听窗口大小变化
    window.addEventListener('resize', () => {
        chart.resize(chartContainer.clientWidth, chartContainer.clientHeight);
    });
}

// 获取币安U本位合约历史K线数据
// API文档: https://binance-docs.github.io/apidocs/futures/cn/#k-2
async function fetchKlines(symbol, interval, nodeOverride = null) {
    // 限制获取的数据条数
    const limit = 500;
    const apiNode = nodeOverride || apiNodeSelect.value;
    let url = '';
    
    // 构建不同节点的请求URL
    let useAlternativeApi = false;
    let formatAlternativeData = null;

    if (apiNode === 'htx') {
        useAlternativeApi = true;
        // 火币 API 符号格式为 btcusdt
        const htxSymbol = symbol.toLowerCase();
        // 映射时间周期
        let htxInterval = '1week';
        if (interval === '12h') {
            alert('注意：火币 HTX 接口不支持 12 小时 K 线，已自动降级为您请求 4 小时数据。如果需要 12 小时，请切换至币安或 Kucoin 节点。');
            htxInterval = '4hour'; // HTX 不支持12h，降级到4h
        }
        else if (interval === '1w') htxInterval = '1week';
        else if (interval === '1day') htxInterval = '1day';
        else if (interval === '4hour') htxInterval = '4hour';
        
        url = `https://api.huobi.pro/market/history/kline?period=${htxInterval}&size=${limit}&symbol=${htxSymbol}`;
        
        formatAlternativeData = (data) => {
            if (!data || data.status !== 'ok' || !data.data) {
                throw new Error(data?.err_msg || '获取火币数据失败');
            }
            return data.data.map(item => ({
                time: item.id, // HTX 返回的是秒级时间戳
                open: parseFloat(item.open),
                high: parseFloat(item.high),
                low: parseFloat(item.low),
                close: parseFloat(item.close)
            })).reverse(); // HTX 返回是按时间倒序的，我们需要正序
        };
    } else if (apiNode === 'kucoin') {
        useAlternativeApi = true;
        // Kucoin 符号格式为 BTCUSDTM (U本位永续合约) 或 BTC-USDT (现货)
        // 扫描出来的币种（如 BANANAS31USDT, ARIAUSDT）通常是币安的永续合约，在 Kucoin 可能不存在或命名不同
        // 为了兼容性，Kucoin 的合约命名一般是 XBTUSDTM 或 币种USDTM
        let kucoinSymbol = symbol.toUpperCase();
        if (kucoinSymbol === 'BTCUSDT') {
            kucoinSymbol = 'XBTUSDTM';
        } else if (kucoinSymbol.endsWith('USDT')) {
            kucoinSymbol = kucoinSymbol + 'M';
        } else {
            kucoinSymbol = kucoinSymbol.replace('USDT', '-USDT'); // 降级到现货格式尝试
        }

        let kucoinInterval = '1week';
        if (interval === '12h') kucoinInterval = '12hour';
        else if (interval === '1w') kucoinInterval = '1week';
        else if (interval === '1day') kucoinInterval = '1day';
        else if (interval === '4hour') kucoinInterval = '4hour';
        
        // 使用本地 server.py 进行代理，避免直接请求可能遇到的网络超时
        // 注意：Kucoin 合约 API 的路径是 /api/v1/kline/query
        url = `${API_BASE}/kucoin/api/v1/kline/query?symbol=${kucoinSymbol}&granularity=${interval === '12h' ? 720 : (interval === '1w' ? 10080 : 1440)}`;
        
        // 由于 Kucoin 合约和现货的 K线 API 完全不同，为了简便，如果用 Kucoin 节点查币安特有的合约经常会报错 Unsupported trading pair
        // 所以我们还是请求现货的 API 作为后备（虽然这可能导致找不到某些币安特有的纯合约币种）
        url = `${API_BASE}/kucoin/api/v1/market/candles?type=${kucoinInterval}&symbol=${symbol.toUpperCase().replace('USDT', '-USDT')}`;

        formatAlternativeData = (data) => {
            if (!data || data.code !== '200000' || !data.data) {
                // 处理 Unsupported trading pair 错误，给用户友好提示
                if (data && data.msg === 'Unsupported trading pair') {
                    throw new Error(`Kucoin 节点不支持该交易对: ${symbol}。请尝试切换到币安节点 (推荐: 币安-本地代理)。`);
                }
                throw new Error(data?.msg || '获取 Kucoin 数据失败');
            }
            return data.data.map(item => ({
                time: parseInt(item[0]), // Kucoin 返回的是秒级时间戳
                open: parseFloat(item[1]),
                high: parseFloat(item[3]),
                low: parseFloat(item[4]),
                close: parseFloat(item[2])
            })).reverse(); // Kucoin 也是倒序
        };
    } else if (apiNode === 'proxy1') {
        const targetUrl = encodeURIComponent(`https://fapi.binance.com/fapi/v1/klines?symbol=${symbol.toUpperCase()}&interval=${interval}&limit=${limit}`);
        url = `https://api.allorigins.win/raw?url=${targetUrl}`;
    } else if (apiNode === 'proxy2') {
        const targetUrl = encodeURIComponent(`https://fapi.binance.com/fapi/v1/klines?symbol=${symbol.toUpperCase()}&interval=${interval}&limit=${limit}`);
        url = `https://corsproxy.io/?${targetUrl}`;
    } else if (apiNode === 'codetabs') {
        // 使用 HTTP 版本的代理和目标地址以规避严格的 SSL 拦截
        const targetUrl = encodeURIComponent(`http://fapi.binance.com/fapi/v1/klines?symbol=${symbol.toUpperCase()}&interval=${interval}&limit=${limit}`);
        url = `http://api.codetabs.com/v1/proxy/?quest=${targetUrl}`;
    } else if (apiNode === 'local') {
        url = `${API_BASE}/fapi/v1/klines?symbol=${symbol.toUpperCase()}&interval=${interval}&limit=${limit}`;
    } else {
        url = `${apiNode}/fapi/v1/klines?symbol=${symbol.toUpperCase()}&interval=${interval}&limit=${limit}`;
    }

    try {
        const response = await fetch(url);
        
        if (!response.ok) {
            const errorText = await response.text();
            let errorMsg = '获取数据失败，请检查合约代码是否正确或尝试切换节点';
            try {
                const errorData = JSON.parse(errorText);
                if (errorData.msg) errorMsg = errorData.msg;
            } catch (e) {}
            throw new Error(errorMsg);
        }

        let data;
        if (apiNode === 'codetabs') {
            const dataText = await response.text();
            if (dataText.includes('Error') || dataText.includes('error')) {
                throw new Error('代理服务器返回错误: ' + dataText.substring(0, 100));
            }
            data = JSON.parse(dataText);
        } else {
            data = await response.json();
        }

        if (data.msg && data.msg.includes('代理请求失败')) {
            throw new Error(data.msg);
        }

        // 解析数据并更新图表
        let klineData;
        
        if (useAlternativeApi) {
            klineData = formatAlternativeData(data);
        } else {
            // 币安格式
            klineData = data.map(item => ({
                time: item[0] / 1000, // Lightweight Charts 需要秒级时间戳
                open: parseFloat(item[1]),
                high: parseFloat(item[2]),
                low: parseFloat(item[3]),
                close: parseFloat(item[4])
            }));
        }

        // 过滤掉无效的时间戳数据
        const validData = klineData.filter(item => !isNaN(item.time) && !isNaN(item.open) && !isNaN(item.high) && !isNaN(item.low) && !isNaN(item.close));

        return validData;
    } catch (error) {
        console.error('Fetch error:', error);
        throw error;
    }
}

// 处理查询操作
async function handleFetch() {
    const symbol = symbolInput.value.trim().toUpperCase();
    const interval = intervalSelect.value;
    const apiNode = apiNodeSelect.value;

    if (!symbol) {
        showError('请输入合约代码');
        return;
    }

    // 更新UI状态
    fetchBtn.disabled = true;
    loadingEl.classList.remove('hidden');
    errorEl.classList.add('hidden');
    chartContainer.style.opacity = '0.5';

    try {
        let data;
        // 简化 cacheKey，因为同一品种、同一周期的K线数据是固定的，和节点无关
        const cacheKey = `${symbol}_${interval}`;

        if (klineDataCache[cacheKey]) {
            // 如果缓存中已有数据（或正在请求的 Promise），直接 await 它
            // 这样可以防止在预加载还未完成时，用户点击导致重复请求
            data = await klineDataCache[cacheKey];
        } else {
            // 没有缓存时，发起请求并将 Promise 存入缓存
            const fetchPromise = fetchKlines(symbol, interval);
            klineDataCache[cacheKey] = fetchPromise;
            data = await fetchPromise;
        }
        
        if (data.length === 0) {
            throw new Error('未获取到该合约的数据');
        }

        // 重新初始化图表并设置数据
        initChart();
        candlestickSeries.setData(data);
        
        // 计算并设置 MA1 数据（MA1即每个周期的收盘价）
        const ma1Data = data.map(item => ({
            time: item.time,
            value: item.close
        }));
        ma1Series.setData(ma1Data);
        
        // 自动适应屏幕显示所有数据
        chart.timeScale().fitContent();
        
        // 同步侧栏高亮：如果列表中存在当前合约，保持蓝色选中态
        if (top10List && top10List.children.length > 0) {
            const children = Array.from(top10List.children);
            let matched = null;
            children.forEach(btn => {
                if (btn.dataset && btn.dataset.symbol === symbol) {
                    matched = btn;
                } else {
                    btn.classList.remove('active');
                }
            });
            if (matched) {
                matched.classList.add('active');
                window.__activeTop10Btn = matched;
            }
        }

    } catch (error) {
        showError(error.message);
    } finally {
        // 恢复UI状态
        fetchBtn.disabled = false;
        loadingEl.classList.add('hidden');
        chartContainer.style.opacity = '1';
    }
}

// 显示错误信息
function showError(msg) {
    errorEl.textContent = msg;
    errorEl.classList.remove('hidden');
    if (candlestickSeries) {
        candlestickSeries.setData([]);
    }
    if (ma1Series) {
        ma1Series.setData([]);
    }
}

// 绑定事件
fetchBtn.addEventListener('click', handleFetch);

const scanTop10Btn = document.getElementById('scan-top10-btn');
const sidebar = document.getElementById('sidebar');
const top10List = document.getElementById('top10-list');

scanTop10Btn.addEventListener('click', async () => {
    scanTop10Btn.disabled = true;
    scanTop10Btn.textContent = '正在扫描全市场...';
    sidebar.classList.remove('hidden');
    top10List.innerHTML = '<div style="color: #2962ff; text-align: center; padding: 20px;">正在扫描500+合约周线结构<br>预计需要10-15秒...</div>';

    try {
        const response = await fetch(`${API_BASE}/api/scan_top10`);
        if (!response.ok) {
            throw new Error('扫描失败');
        }
        const data = await response.json();
        
        top10List.innerHTML = '';
        data.forEach((item, index) => {
            const btn = document.createElement('button');
            btn.className = 'top10-item-btn';
            if (!window.__activeTop10Btn) {
                window.__activeTop10Btn = null;
            }
            btn.dataset.symbol = item.symbol;
            
            const nameSpan = document.createElement('span');
            nameSpan.className = 'symbol-name';
            nameSpan.textContent = `${index + 1}. ${item.symbol}`;
            
            const scoreSpan = document.createElement('span');
            scoreSpan.className = 'score-badge';
            scoreSpan.textContent = `+${item.chg_4w.toFixed(1)}%`;
            
            btn.appendChild(nameSpan);
            btn.appendChild(scoreSpan);
            
            btn.addEventListener('click', () => {
                if (window.__activeTop10Btn) {
                    window.__activeTop10Btn.classList.remove('active');
                }
                btn.classList.add('active');
                window.__activeTop10Btn = btn;
                
                symbolInput.value = item.symbol;
                intervalSelect.value = '1w'; // 自动切换到1w
                
                // 如果当前使用的是 Kucoin 或 HTX，自动切换到币安节点（因为扫描出的都是币安合约）
                const currentNode = apiNodeSelect.value;
                if (currentNode === 'kucoin' || currentNode === 'htx') {
                    apiNodeSelect.value = 'local'; // 默认切换到本地代理的币安节点
                }
                
                handleFetch();
            });
            
            top10List.appendChild(btn);
        });

        // 服务端预热缓存 + 前端本地并发预热，双重保障“秒开”
        if (data.length > 0) {
            const symbols = data.map(x => x.symbol).join(',');
            fetch(`${API_BASE}/api/preload?symbols=${encodeURIComponent(symbols)}&interval=1w&limit=500`).catch(()=>{});
        }
        data.forEach((item, idx) => {
            const cacheKey = `${item.symbol}_1w`;
            if (!klineDataCache[cacheKey]) {
                klineDataCache[cacheKey] = new Promise((resolve, reject) => {
                    const delay = idx < 3 ? idx * 200 : 800 + (idx - 3) * 400;
                    setTimeout(() => {
                        fetchKlines(item.symbol, '1w', 'local')
                            .then(klineData => {
                                resolve(klineData);
                            })
                            .catch(err => {
                                delete klineDataCache[cacheKey];
                                reject(err);
                            });
                    }, delay);
                });
            }
        });
        
        // 自动选中并加载第一个品种
        if (data.length > 0 && top10List.firstChild) {
            top10List.firstChild.click();
        }
        
    } catch (err) {
        top10List.innerHTML = `<div style="color: #ef5350; text-align: center;">${err.message}</div>`;
    } finally {
        scanTop10Btn.disabled = false;
        scanTop10Btn.textContent = '扫描强势 Top10 (1w)';
    }
});

symbolInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        handleFetch();
    }
});

// 当时间粒度或数据节点改变时，自动触发查询
intervalSelect.addEventListener('change', handleFetch);
apiNodeSelect.addEventListener('change', handleFetch);

// 页面加载完成时初始化并获取默认数据
document.addEventListener('DOMContentLoaded', () => {
    initChart();
    handleFetch();
});
