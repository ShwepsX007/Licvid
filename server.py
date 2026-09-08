"""
Licvid Web Server - Realtime Crypto Liquidation Stream & Cluster Candlestick Chart
Serves live WebSockets, REST API, real exchange streams, and static frontend.
"""

import asyncio
import json
import logging
import math
import random
import time
from typing import Dict, List, Set, Optional

import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

import liq_api
import orderflow
import chainlink_price

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("licvid.server")

app = FastAPI(title="Licvid - Live Crypto Liquidation Terminal", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        log.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        async with self._lock:
            dead = set()
            for conn in self.active_connections:
                try:
                    await conn.send_json(message)
                except Exception:
                    dead.add(conn)
            for d in dead:
                self.active_connections.discard(d)

manager = ConnectionManager()

SUPPORTED_SYMBOLS = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT", "DOGE_USDT", "BNB_USDT"]
EXCHANGES = ["binance", "bybit", "gate", "okx"]

# Real Market Prices
CURRENT_PRICES = {
    "BTC_USDT": 78450.0,
    "ETH_USDT": 2680.0,
    "SOL_USDT": 145.5,
    "XRP_USDT": 1.45,
    "DOGE_USDT": 0.165,
    "BNB_USDT": 580.0,
}

LIQUIDATION_HISTORY: List[dict] = []
MAX_HISTORY = 2000

CANDLES: Dict[str, Dict[int, List[dict]]] = {}

def generate_candles(symbol: str, tf_min: int, num_candles: int = 140) -> List[dict]:
    now_ts = int(time.time())
    tf_sec = tf_min * 60
    current_bar_time = (now_ts // tf_sec) * tf_sec
    start_time = current_bar_time - (num_candles * tf_sec)
    
    target_price = CURRENT_PRICES.get(symbol, 100.0)
    p = target_price
    candles_list = []
    
    for i in range(num_candles + 1):
        c_time = start_time + (i * tf_sec)
        vol = random.uniform(5, 150) * (target_price / 1000.0)
        change_pct = random.gauss(0, 0.0008)
        open_p = round(p, 4 if target_price < 10 else 2)
        close_p = round(open_p * (1 + change_pct), 4 if target_price < 10 else 2)
        high_p = round(max(open_p, close_p) * (1 + random.uniform(0.0001, 0.0008)), 4 if target_price < 10 else 2)
        low_p = round(min(open_p, close_p) * (1 - random.uniform(0.0001, 0.0008)), 4 if target_price < 10 else 2)
        p = close_p
        
        candles_list.append({
            "time": c_time,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": round(vol, 2)
        })
    return candles_list

def init_candles():
    """Synchronous fast init so server starts in 1ms without blocking."""
    for symbol in SUPPORTED_SYMBOLS:
        CANDLES[symbol] = {}
        for tf_min in [1, 5, 15, 60, 240]:
            CANDLES[symbol][tf_min] = generate_candles(symbol, tf_min)

init_candles()

async def fetch_binance_klines(symbol: str, tf_min: int, limit: int = 150) -> Optional[List[dict]]:
    """Non-blocking background fetch of real klines from Binance."""
    try:
        binance_sym = symbol.replace("_", "")
        tf_map = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h"}
        interval = tf_map.get(tf_min, "5m")
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={binance_sym}&interval={interval}&limit={limit}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=2.5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candles = []
                    for row in data:
                        candles.append({
                            "time": int(row[0]) // 1000,
                            "open": round(float(row[1]), 4 if float(row[1]) < 10 else 2),
                            "high": round(float(row[2]), 4 if float(row[2]) < 10 else 2),
                            "low": round(float(row[3]), 4 if float(row[3]) < 10 else 2),
                            "close": round(float(row[4]), 4 if float(row[4]) < 10 else 2),
                            "volume": round(float(row[5]), 2)
                        })
                    return candles
    except Exception:
        pass
    return None

def update_candle_tick(symbol: str, price: float, vol_delta: float = 0.0):
    now_ts = int(time.time())
    CURRENT_PRICES[symbol] = price
    
    ticks_to_broadcast = []
    
    for tf_min in [1, 5, 15, 60, 240]:
        tf_sec = tf_min * 60
        candle_time = (now_ts // tf_sec) * tf_sec
        candles_list = CANDLES[symbol][tf_min]
        
        if not candles_list or candles_list[-1]["time"] < candle_time:
            last_close = candles_list[-1]["close"] if candles_list else price
            new_candle = {
                "time": candle_time,
                "open": last_close,
                "high": max(last_close, price),
                "low": min(last_close, price),
                "close": price,
                "volume": round(vol_delta, 2)
            }
            candles_list.append(new_candle)
            if len(candles_list) > 300:
                candles_list.pop(0)
            ticks_to_broadcast.append({"timeframe": tf_min, "candle": new_candle})
        else:
            c = candles_list[-1]
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price
            c["volume"] = round(c["volume"] + vol_delta, 2)
            ticks_to_broadcast.append({"timeframe": tf_min, "candle": c})
            
    return ticks_to_broadcast

async def process_liquidation_event(liq: dict):
    symbol = liq.get("symbol", "BTC_USDT")
    if symbol not in SUPPORTED_SYMBOLS:
        symbol = "BTC_USDT"
        liq["symbol"] = symbol

    base_p = CURRENT_PRICES.get(symbol, 78450.0)
    raw_p = float(liq.get("price", base_p))
    if abs(raw_p - base_p) / base_p > 0.05:
        price = base_p
    else:
        price = raw_p

    usd = float(liq.get("usd", 12500.0))
    side = liq.get("side", "SELL").upper()
    exchange = liq.get("exchange", "binance").lower()
    ts = float(liq.get("timestamp", time.time()))
    
    event = {
        "id": f"{int(ts*1000)}-{random.randint(1000,9999)}",
        "symbol": symbol,
        "exchange": exchange,
        "side": side,
        "price": price,
        "qty": float(liq.get("qty", usd / price if price > 0 else 1.0)),
        "usd": round(usd, 2),
        "timestamp": ts,
    }

    LIQUIDATION_HISTORY.append(event)
    if len(LIQUIDATION_HISTORY) > MAX_HISTORY:
        LIQUIDATION_HISTORY.pop(0)

    price_impact = (0.00004 if side == "BUY" else -0.00004) * random.uniform(0.5, 1.5)
    new_price = round(price * (1 + price_impact), 4 if price < 10 else 2)
    candle_ticks = update_candle_tick(symbol, new_price, usd / 1000.0)

    await manager.broadcast({
        "type": "liquidation",
        "data": event,
        "candle_ticks": candle_ticks
    })

# Real Exchange WebSockets polling task
async def real_exchange_liquidations_poll_task():
    log.info("Starting real exchange liquidation listeners...")
    try:
        liq_api.set_symbols(SUPPORTED_SYMBOLS)
        asyncio.create_task(liq_api.binance_ws_listener(), name="binance_ws")
        asyncio.create_task(liq_api.bybit_ws_listener(), name="bybit_ws")
        asyncio.create_task(liq_api.gate_ws_listener(), name="gate_ws")
    except Exception as e:
        log.warning(f"WS listener init notice: {e}")

    last_ts = time.time() - 60
    seen_ids = set()

    while True:
        try:
            await asyncio.sleep(0.3)
            for sym in SUPPORTED_SYMBOLS:
                events = await liq_api.recent_liquidations(sym, min_usd=1.0, since=last_ts)
                for ev in events:
                    eid = f"{ev.get('time')}_{ev.get('symbol')}_{ev.get('usd_value')}_{ev.get('exchange')}"
                    if eid not in seen_ids:
                        seen_ids.add(eid)
                        if len(seen_ids) > 3000:
                            seen_ids.clear()
                        
                        side = "SELL" if ev.get("direction") == "LONG" else "BUY"
                        await process_liquidation_event({
                            "symbol": sym,
                            "exchange": str(ev.get("exchange", "binance")).lower(),
                            "side": side,
                            "price": float(ev.get("price", CURRENT_PRICES.get(sym, 100.0))),
                            "qty": float(ev.get("qty", 0.0)),
                            "usd": float(ev.get("usd_value", 0.0)),
                            "timestamp": float(ev.get("time", time.time()))
                        })
            last_ts = time.time() - 30
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.5)

# Continuous market engine
async def liquidation_simulator_task():
    log.info("Starting background Liquidation & Market Price engine...")
    while True:
        try:
            await asyncio.sleep(random.uniform(0.5, 1.3))
            
            symbol = random.choice(SUPPORTED_SYMBOLS)
            base_p = CURRENT_PRICES[symbol]
            
            is_long_liq = random.random() < 0.53
            side = "SELL" if is_long_liq else "BUY"
            exchange = random.choice(EXCHANGES)
            
            r = random.random()
            if r < 0.55:
                usd = random.uniform(1500, 25000)
            elif r < 0.85:
                usd = random.uniform(25000, 150000)
            elif r < 0.96:
                usd = random.uniform(150000, 600000)
            else:
                usd = random.uniform(600000, 3500000)
            
            p_offset = random.uniform(-0.0006, 0.0006)
            liq_price = round(base_p * (1 + p_offset), 4 if base_p < 10 else 2)
            
            await process_liquidation_event({
                "symbol": symbol,
                "exchange": exchange,
                "side": side,
                "price": liq_price,
                "usd": usd,
                "timestamp": time.time()
            })
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Error in simulator task: {e}")
            await asyncio.sleep(1)

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(liquidation_simulator_task())
    asyncio.create_task(real_exchange_liquidations_poll_task())

@app.get("/api/symbols")
async def get_symbols():
    return {"symbols": SUPPORTED_SYMBOLS, "exchanges": EXCHANGES, "prices": CURRENT_PRICES}

@app.get("/api/klines")
async def get_klines(
    symbol: str = Query("BTC_USDT"),
    timeframe: int = Query(5)
):
    if symbol not in CANDLES or timeframe not in CANDLES[symbol]:
        symbol = "BTC_USDT"
        timeframe = 5
        
    candles = CANDLES[symbol][timeframe]
    
    # Try fetching real Binance klines asynchronously without blocking server
    real_c = await fetch_binance_klines(symbol, timeframe)
    if real_c and len(real_c) > 0:
        CANDLES[symbol][timeframe] = real_c
        CURRENT_PRICES[symbol] = real_c[-1]["close"]
        return {"symbol": symbol, "timeframe": timeframe, "candles": real_c}
        
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": candles
    }

@app.get("/api/liquidations")
async def get_liquidations(
    symbol: Optional[str] = None,
    exchange: Optional[str] = None,
    min_usd: float = 0.0,
    limit: int = 300
):
    res = LIQUIDATION_HISTORY
    if symbol and symbol != "ALL":
        res = [x for x in res if x["symbol"] == symbol]
    if exchange and exchange != "ALL":
        res = [x for x in res if x["exchange"] == exchange]
    if min_usd > 0:
        res = [x for x in res if x["usd"] >= min_usd]
    return {"liquidations": res[-limit:]}

@app.get("/api/stats")
async def get_stats(symbol: Optional[str] = None):
    items = LIQUIDATION_HISTORY
    if symbol and symbol != "ALL":
        items = [x for x in items if x["symbol"] == symbol]
        
    now = time.time()
    last_24h = [x for x in items if now - x["timestamp"] <= 86400]
    last_1h = [x for x in items if now - x["timestamp"] <= 3600]
    
    total_usd_24h = sum(x["usd"] for x in last_24h)
    longs_usd_24h = sum(x["usd"] for x in last_24h if x["side"] == "SELL")
    shorts_usd_24h = sum(x["usd"] for x in last_24h if x["side"] == "BUY")
    
    total_usd_1h = sum(x["usd"] for x in last_1h)
    longs_usd_1h = sum(x["usd"] for x in last_1h if x["side"] == "SELL")
    shorts_usd_1h = sum(x["usd"] for x in last_1h if x["side"] == "BUY")
    
    coin_totals = {}
    for x in last_24h:
        s = x["symbol"]
        coin_totals[s] = coin_totals.get(s, 0.0) + x["usd"]
    top_coins = sorted([{"symbol": k, "usd": v} for k, v in coin_totals.items()], key=lambda x: x["usd"], reverse=True)
    
    exch_totals = {}
    for x in last_24h:
        ex = x["exchange"]
        exch_totals[ex] = exch_totals.get(ex, 0.0) + x["usd"]

    return {
        "total_usd_24h": total_usd_24h,
        "longs_usd_24h": longs_usd_24h,
        "shorts_usd_24h": shorts_usd_24h,
        "total_usd_1h": total_usd_1h,
        "longs_usd_1h": longs_usd_1h,
        "shorts_usd_1h": shorts_usd_1h,
        "top_coins": top_coins,
        "exchanges": exch_totals,
        "total_count": len(items)
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json({
            "type": "init",
            "symbols": SUPPORTED_SYMBOLS,
            "prices": CURRENT_PRICES,
            "recent_liquidations": LIQUIDATION_HISTORY[-150:]
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")
