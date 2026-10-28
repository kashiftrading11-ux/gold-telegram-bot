import os
import json
import requests
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime, timezone, timedelta

# --- TELEGRAM SECRETS ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(message):
    if TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload)

# --- STATE MEMORY FILE ---
STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {
        "date": "",
        "daily_trades": 0,
        "daily_wins": 0,
        "daily_losses": 0,
        "bot_locked": False,
        "active_trade": None # Stores active trade to track SL/TP
    }

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# --- TIME & SESSION (PKT = UTC+5) ---
utc_now = datetime.now(timezone.utc)
pkt_now = utc_now + timedelta(hours=5)
today_str = pkt_now.strftime("%Y-%m-%d")
time_str = pkt_now.strftime("%H:%M")
pkt_hour = pkt_now.hour

# Session Check (London: 12-21 PKT | NY: 17-02 PKT)
in_session = (12 <= pkt_hour < 21) or (pkt_hour >= 17 or pkt_hour < 2)
session_name = "London+NY 🔥" if (17 <= pkt_hour < 21) else "London" if (12 <= pkt_hour < 17) else "New York"

state = load_state()

# Daily Reset
if state.get("date") != today_str:
    state["date"] = today_str
    state["daily_trades"] = 0
    state["daily_wins"] = 0
    state["daily_losses"] = 0
    state["bot_locked"] = False
    state["active_trade"] = None

# Max Daily Loss Lock Engine
if state["daily_losses"] >= 2:
    state["bot_locked"] = True

# --- FETCH MARKET DATA ---
print("Fetching Gold Market Data...")
gold_m15 = yf.download(tickers="GC=F", period="5d", interval="15m", progress=False)
gold_h4 = yf.download(tickers="GC=F", period="1mo", interval="1h", progress=False) # H1/H4 Trend

if gold_m15.empty or len(gold_m15) < 50:
    print("Market Data unavailable.")
    save_state(state)
    exit()

# Flatten columns if multi-index
if isinstance(gold_m15.columns, pd.MultiIndex):
    gold_m15.columns = gold_m15.columns.get_level_values(0)
if isinstance(gold_h4.columns, pd.MultiIndex):
    gold_h4.columns = gold_h4.columns.get_level_values(0)

df = gold_m15.copy()

# H4 Trend Filter (EMA 50)
h4_ema = ta.trend.ema_indicator(gold_h4['Close'], window=50)
h4_bullish = gold_h4['Close'].iloc[-1] > h4_ema.iloc[-1]
h4_bearish = gold_h4['Close'].iloc[-1] < h4_ema.iloc[-1]

# --- MONITOR ACTIVE TRADE (AUTO SL/TP TRACKING) ---
if state["active_trade"]:
    trade = state["active_trade"]
    latest_high = df['High'].iloc[-1]
    latest_low = df['Low'].iloc[-1]
    
    if trade["dir"] == "BUY":
        if latest_high >= trade["tp"]:
            state["daily_wins"] += 1
            state["active_trade"] = None
            send_telegram(f"✅ *WIN (+1:2 RR)*\nTarget Hit @ {trade['tp']:.2f}\nTotal Today: {state['daily_wins']}W / {state['daily_losses']}L")
        elif latest_low <= trade["sl"]:
            state["daily_losses"] += 1
            state["active_trade"] = None
            if state["daily_losses"] >= 2:
                state["bot_locked"] = True
                send_telegram(f"❌ *LOSS HIT @ {trade['sl']:.2f}*\n⚠️ *BOT LOCKED FOR TODAY* (Max 2 Losses Hit).")
            else:
                send_telegram(f"❌ *LOSS HIT @ {trade['sl']:.2f}*\nSearching for Next Strict Setup (Trade {state['daily_trades']}/3)...")

    elif trade["dir"] == "SELL":
        if latest_low <= trade["tp"]:
            state["daily_wins"] += 1
            state["active_trade"] = None
            send_telegram(f"✅ *WIN (+1:2 RR)*\nTarget Hit @ {trade['tp']:.2f}\nTotal Today: {state['daily_wins']}W / {state['daily_losses']}L")
        elif latest_high >= trade["sl"]:
            state["daily_losses"] += 1
            state["active_trade"] = None
            if state["daily_losses"] >= 2:
                state["bot_locked"] = True
                send_telegram(f"❌ *LOSS HIT @ {trade['sl']:.2f}*\n⚠️ *BOT LOCKED FOR TODAY* (Max 2 Losses Hit).")
            else:
                send_telegram(f"❌ *LOSS HIT @ {trade['sl']:.2f}*\nSearching for Next Strict Setup (Trade {state['daily_trades']}/3)...")

# --- SIGNAL GENERATION LOGIC ---
can_trade = in_session and not state["bot_locked"] and (state["daily_trades"] < 3) and (state["active_trade"] is None)

if can_trade:
    # Swings
    swing_len = 10
    df['sw_high'] = df['High'].rolling(swing_len*2+1, center=True).apply(lambda x: x[swing_len] if max(x) == x[swing_len] else None)
    df['sw_low'] = df['Low'].rolling(swing_len*2+1, center=True).apply(lambda x: x[swing_len] if min(x) == x[swing_len] else None)
    
    recent_lows = df['sw_low'].dropna().tail(3).values
    recent_highs = df['sw_high'].dropna().tail(3).values
    
    curr_close = df['Close'].iloc[-1]
    curr_open = df['Open'].iloc[-1]
    curr_high = df['High'].iloc[-1]
    curr_low = df['Low'].iloc[-1]
    atr = ta.volatility.average_true_range(df['High'], df['Low'], df['Close'], window=14).iloc[-1]
    
    # Tight Buffer
    sl_buffer = 2.5
    
    # 🟢 BULLISH SETUP (Sweep + CHoCH + Retest)
    if h4_bullish and len(recent_lows) > 0:
        sweep_level = recent_lows[-1]
        if curr_low < sweep_level and curr_close > sweep_level: # Sweep
            if curr_close > curr_open and (curr_close - curr_open) > (atr * 0.3): # Body CHoCH
                entry = curr_close
                sl = sweep_level - sl_buffer
                sl_dist = entry - sl
                tp = entry + (sl_dist * 2.0) # Fixed 1:2 RR Target
                
                state["daily_trades"] += 1
                state["active_trade"] = {"dir": "BUY", "entry": entry, "sl": sl, "tp": tp}
                
                msg = f"🟢 *BUY XAUUSD (1:2 RR)*\n" \
                      f"⏰ Time: {time_str} PKT ({session_name})\n" \
                      f"📊 Signal: Trade {state['daily_trades']}/3\n\n" \
                      f"📍 *Entry:* {entry:.2f}\n" \
                      f"🛑 *SL:* {sl:.2f} (Tight SL)\n" \
                      f"🎯 *TP:* {tp:.2f} (Fast Target)\n\n" \
                      f"✔ H4 Bullish Alignment\n✔ Liquidity Sweep & Retest"
                send_telegram(msg)

    # 🔴 BEARISH SETUP (Sweep + CHoCH + Retest)
    elif h4_bearish and len(recent_highs) > 0 and (state["active_trade"] is None):
        sweep_level = recent_highs[-1]
        if curr_high > sweep_level and curr_close < sweep_level: # Sweep
            if curr_close < curr_open and (curr_open - curr_close) > (atr * 0.3): # Body CHoCH
                entry = curr_close
                sl = sweep_level + sl_buffer
                sl_dist = sl - entry
                tp = entry - (sl_dist * 2.0) # Fixed 1:2 RR Target
                
                state["daily_trades"] += 1
                state["active_trade"] = {"dir": "SELL", "entry": entry, "sl": sl, "tp": tp}
                
                msg = f"🔴 *SELL XAUUSD (1:2 RR)*\n" \
                      f"⏰ Time: {time_str} PKT ({session_name})\n" \
                      f"📊 Signal: Trade {state['daily_trades']}/3\n\n" \
                      f"📍 *Entry:* {entry:.2f}\n" \
                      f"🛑 *SL:* {sl:.2f} (Tight SL)\n" \
                      f"🎯 *TP:* {tp:.2f} (Fast Target)\n\n" \
                      f"✔ H4 Bearish Alignment\n✔ Liquidity Sweep & Retest"
                send_telegram(msg)

save_state(state)
print("Bot Check Completed Successfully.")
