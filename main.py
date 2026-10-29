import yfinance as yf
import pandas as pd
import requests
import os
import json
from datetime import datetime
import pytz

# --- CONFIGURATION ---
SYMBOL = "GC=F"  # Gold Futures
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "state.json"
PKT = pytz.timezone('Asia/Karachi')

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Error sending message: {e}")

def load_state():
    today = datetime.now(PKT).strftime('%Y-%m-%d')
    default_state = {
        "date": today,
        "daily_trades": 0,
        "daily_wins": 0,
        "daily_losses": 0,
        "bot_locked": False,
        "active_trade": None
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

def get_clean_data(symbol, interval, period):
    df = yf.download(tickers=symbol, interval=interval, period=period, progress=False)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.dropna(inplace=True)
    return df

def run_bot():
    now_pkt = datetime.now(PKT)
    print(f"[{now_pkt.strftime('%Y-%m-%d %H:%M:%S PKT')}] Fetching Market Data...")

    state = load_state()

    # Rule: Max 3 trades per day & Stop on 2 Losses
    if state.get("bot_locked", False):
        print("Bot is locked for today due to 2 losses. No trading.")
        return
    if state.get("daily_trades", 0) >= 3:
        print("Maximum daily limit (3 trades) reached for today.")
        return

    # 1. Fetch Data
    df_m15 = get_clean_data(SYMBOL, "15m", "5d")
    df_h4 = get_clean_data(SYMBOL, "4h", "20d")

    if df_m15 is None or df_h4 is None or len(df_m15) < 30:
        print("Insufficient market data. Retrying next cycle.")
        return

    # 2. H4 Trend Filter (Native Pandas 50 EMA)
    df_h4['ema50'] = df_h4['Close'].ewm(span=50, adjust=False).mean()
    h4_last_close = df_h4['Close'].iloc[-1]
    h4_ema = df_h4['ema50'].iloc[-1]
    
    if pd.isna(h4_ema):
        print("H4 EMA calculation pending...")
        return

    h4_trend = "BULLISH" if h4_last_close > h4_ema else "BEARISH"

    # 3. M15 ATR Calculation (Native Pandas)
    tr1 = df_m15['High'] - df_m15['Low']
    tr2 = (df_m15['High'] - df_m15['Close'].shift(1)).abs()
    tr3 = (df_m15['Low'] - df_m15['Close'].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df_m15['atr'] = tr.rolling(window=14).mean()
    
    # Swings Calculation
    lookback = 15
    recent_lows = df_m15['Low'].iloc[-lookback:-2]
    recent_highs = df_m15['High'].iloc[-lookback:-2]
    
    swing_low = recent_lows.min()
    swing_high = recent_highs.max()

    curr_candle = df_m15.iloc[-1]
    prev_candle = df_m15.iloc[-2]
    c15_open = curr_candle['Open']
    c15_close = curr_candle['Close']
    c15_high = curr_candle['High']
    c15_low = curr_candle['Low']
    current_atr = df_m15['atr'].iloc[-1]

    signal = None
    entry_price = 0.0
    sl_price = 0.0
    tp_price = 0.0

    # --- SMC STRATEGY LOGIC ---
    # BULLISH SETUP: H4 Bullish + Liquidity Sweep + Body CHoCH + FVG Retest
    if h4_trend == "BULLISH":
        liquidity_swept = prev_candle['Low'] < swing_low or c15_low < swing_low
        body_choch = c15_close > swing_high and c15_close > c15_open
        
        c1_high = df_m15['High'].iloc[-3]
        c3_low = df_m15['Low'].iloc[-1]
        has_fvg = c3_low > c1_high
        
        if liquidity_swept and body_choch and has_fvg:
            fvg_50 = c1_high + ((c3_low - c1_high) * 0.5)
            if c15_low <= fvg_50 or c15_close <= fvg_50:
                signal = "BUY"
                entry_price = c15_close
                sl_distance = max((entry_price - c15_low) + 1.5, current_atr * 1.5)
                sl_price = entry_price - sl_distance
                tp_price = entry_price + (sl_distance * 2)

    # BEARISH SETUP: H4 Bearish + Liquidity Sweep + Body CHoCH + FVG Retest
    elif h4_trend == "BEARISH":
        liquidity_swept = prev_candle['High'] > swing_high or c15_high > swing_high
        body_choch = c15_close < swing_low and c15_close < c15_open
        
        c1_low = df_m15['Low'].iloc[-3]
        c3_high = df_m15['High'].iloc[-1]
        has_fvg = c1_low > c3_high
        
        if liquidity_swept and body_choch and has_fvg:
            fvg_50 = c3_high + ((c1_low - c3_high) * 0.5)
            if c15_high >= fvg_50 or c15_close >= fvg_50:
                signal = "SELL"
                entry_price = c15_close
                sl_distance = max((c15_high - entry_price) + 1.5, current_atr * 1.5)
                sl_price = entry_price + sl_distance
                tp_price = entry_price - (sl_distance * 2)

    # --- SEND TELEGRAM SIGNAL ---
    if signal:
        state["daily_trades"] = state.get("daily_trades", 0) + 1
        save_state(state)

        msg = (
            f"⚡ *GOLD {signal} SIGNAL (SMC Setup)* ⚡\n\n"
            f"📍 *Entry Price:* {entry_price:.2f}\n"
            f"🛑 *Stop Loss:* {sl_price:.2f}\n"
            f"🎯 *Take Profit:* {tp_price:.2f}\n\n"
            f"📊 *Trend (H4):* {h4_trend}\n"
            f"⚖️ *Risk:Reward:* 1:2\n"
            f"🕒 *Time:* {now_pkt.strftime('%H:%M PKT')}\n"
            f"📌 *Daily Trade:* {state['daily_trades']}/3"
        )
        send_telegram_message(msg)
        print(f"Signal generated and sent to Telegram: {signal}")
    else:
        print("Market scanned successfully: No Liquidity Sweep + FVG setup right now.")

if __name__ == "__main__":
    run_bot()
