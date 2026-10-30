import yfinance as yf
import pandas as pd
import requests
import os
import json
from datetime import datetime
import pytz

# --- CONFIGURATION ---
SYMBOL = "GC=F"  # Gold Futures (XAU/USD)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "state.json"
PKT = pytz.timezone('Asia/Karachi')

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}")

def load_state():
    today = datetime.now(PKT).strftime('%Y-%m-%d')
    default_state = {
        "date": today,
        "daily_trades": 0,
        "daily_wins": 0,
        "daily_losses": 0,
        "bot_locked": False,
        "last_signal_time": ""
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                if state.get("date") != today:
                    return default_state
                return state
        except Exception:
            return default_state
    return default_state

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

def fetch_market_data():
    df_m15 = yf.download(SYMBOL, interval="15m", period="5d", progress=False)
    df_h4 = yf.download(SYMBOL, interval="4h", period="20d", progress=False)
    
    if df_m15.empty or df_h4.empty:
        return None, None
        
    if isinstance(df_m15.columns, pd.MultiIndex):
        df_m15.columns = df_m15.columns.get_level_values(0)
    if isinstance(df_h4.columns, pd.MultiIndex):
        df_h4.columns = df_h4.columns.get_level_values(0)
        
    df_m15.dropna(inplace=True)
    df_h4.dropna(inplace=True)
    return df_m15, df_h4

def run_bot():
    now_pkt = datetime.now(PKT)
    print(f"[{now_pkt.strftime('%Y-%m-%d %H:%M:%S PKT')}] Scanning Gold Market...")

    state = load_state()

    # Rule: Max 3 trades per day & Stop on 2 Losses
    if state.get("bot_locked", False):
        print("Bot is locked for today due to 2 losses.")
        return
    if state.get("daily_trades", 0) >= 3:
        print("Maximum daily limit (3 trades) reached.")
        return

    df_m15, df_h4 = fetch_market_data()
    if df_m15 is None or df_h4 is None or len(df_m15) < 30:
        print("Market data fetching issue. Will try next run.")
        return

    # 1. H4 Trend Identification (50 EMA)
    df_h4['ema50'] = df_h4['Close'].ewm(span=50, adjust=False).mean()
    h4_trend = "BULLISH" if df_h4['Close'].iloc[-1] > df_h4['ema50'].iloc[-1] else "BEARISH"

    # 2. M15 SMC & Engulfing Logic
    lookback = 12
    recent_sw_low = df_m15['Low'].iloc[-lookback:-1].min()
    recent_sw_high = df_m15['High'].iloc[-lookback:-1].max()

    curr = df_m15.iloc[-1]
    prev = df_m15.iloc[-2]

    curr_candle_time = str(df_m15.index[-1])
    if state.get("last_signal_time") == curr_candle_time:
        print("Already processed signal for this candle.")
        return

    signal = None
    entry_price = float(curr['Close'])
    sl_price = 0.0
    tp_price = 0.0

    # --- BUY SETUP (Tight SL & 1:2 TP) ---
    if h4_trend == "BULLISH":
        liquidity_swept = (prev['Low'] < recent_sw_low) or (curr['Low'] < recent_sw_low)
        bullish_engulfing = (curr['Close'] > curr['Open']) and (curr['Close'] > prev['High'])
        
        if liquidity_swept and bullish_engulfing:
            signal = "BUY"
            # Tight SL: Lowest point of sweep - $1.50 buffer
            sl_raw = min(curr['Low'], prev['Low']) - 1.50
            # Cap maximum SL at $4.00
            if (entry_price - sl_raw) > 4.0:
                sl_price = entry_price - 3.50
            else:
                sl_price = sl_raw
            
            sl_distance = entry_price - sl_price
            tp_price = entry_price + (sl_distance * 2.0)  # 1:2 RR

    # --- SELL SETUP (Tight SL & 1:2 TP) ---
    elif h4_trend == "BEARISH":
        liquidity_swept = (prev['High'] > recent_sw_high) or (curr['High'] > recent_sw_high)
        bearish_engulfing = (curr['Close'] < curr['Open']) and (curr['Close'] < prev['Low'])
        
        if liquidity_swept and bearish_engulfing:
            signal = "SELL"
            # Tight SL: Highest point of sweep + $1.50 buffer
            sl_raw = max(curr['High'], prev['High']) + 1.50
            # Cap maximum SL at $4.00
            if (sl_raw - entry_price) > 4.0:
                sl_price = entry_price + 3.50
            else:
                sl_price = sl_raw
                
            sl_distance = sl_price - entry_price
            tp_price = entry_price - (sl_distance * 2.0)  # 1:2 RR

    # --- TRIGGER TELEGRAM SIGNAL ---
    if signal:
        state["daily_trades"] += 1
        state["last_signal_time"] = curr_candle_time
        save_state(state)

        risk_pts = abs(entry_price - sl_price)
        reward_pts = abs(tp_price - entry_price)

        msg = (
            f"🔔 *NEW GOLD {signal} SIGNAL* 🔔\n\n"
            f"📍 *Entry:* `{entry_price:.2f}`\n"
            f"🛑 *Stop Loss:* `{sl_price:.2f}` ({risk_pts:.1f} pts)\n"
            f"🎯 *Take Profit:* `{tp_price:.2f}` ({reward_pts:.1f} pts)\n\n"
            f"📊 *H4 Trend:* {h4_trend}\n"
            f"⚖️ *Risk:Reward:* 1:2 Target\n"
            f"🕒 *Time:* {now_pkt.strftime('%I:%M %p PKT')}\n"
            f"📌 *Daily Signal Count:* {state['daily_trades']}/3\n\n"
            f"🔗 [Open TradingView Gold Chart](https://www.tradingview.com/chart/?symbol=OANDA:XAUUSD)"
        )
        send_telegram_message(msg)
        print(f"SUCCESS: {signal} signal sent to Telegram!")
    else:
        print("Scan finished: No high-probability setup on this 15M candle.")

if __name__ == "__main__":
    run_bot()
