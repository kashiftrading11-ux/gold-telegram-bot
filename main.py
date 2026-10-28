import yfinance as yf
import pandas as pd
import pandas_ta as ta
import requests
import os
import json
from datetime import datetime

# --- CONFIGURATION ---
SYMBOL = "GC=F"  # Gold Futures
TIMEFRAME = "15m"
H4_TIMEFRAME = "4h"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "state.json"

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Error sending message: {e}")

def get_data(symbol, interval, period="5d"):
    df = yf.download(tickers=symbol, interval=interval, period=period, progress=False)
    if df.empty:
        return None
    # Fix for MultiIndex columns in new yfinance versions
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def run_bot():
    print(f"Fetching Gold Market Data at {datetime.now()}...")
    
    # Get M15 and H4 data
    df_m15 = get_data(SYMBOL, "15m", "5d")
    df_h4 = get_data(SYMBOL, "4h", "20d")

    if df_m15 is None or df_h4 is None or len(df_m15) < 50:
        print("Not enough data fetched. Skipping this run.")
        return

    # Calculate Indicators
    df_h4['ema50'] = ta.ema(df_h4['Close'], length=50)
    df_m15['atr'] = ta.atr(df_m15['High'], df_m15['Low'], df_m15['Close'], length=14)
    
    if df_h4['ema50'].iloc[-1] is None or pd.isna(df_h4['ema50'].iloc[-1]):
        print("Indicator calculation failed. Skipping.")
        return

    current_price = df_m15['Close'].iloc[-1]
    h4_ema = df_h4['ema50'].iloc[-1]
    trend = "BUY" if current_price > h4_ema else "SELL"
    
    # Strategy Logic (Simplified for stability)
    last_low = df_m15['Low'].rolling(window=10).min().iloc[-2]
    last_high = df_m15['High'].rolling(window=10).max().iloc[-2]
    
    # Check for Liquidity Sweep & CHoCH
    signal = None
    if trend == "BUY" and df_m15['Low'].iloc[-1] < last_low:
        if df_m15['Close'].iloc[-1] > df_m15['Open'].iloc[-1]: # Body close check
            signal = "BUY"
    elif trend == "SELL" and df_m15['High'].iloc[-1] > last_high:
        if df_m15['Close'].iloc[-1] < df_m15['Open'].iloc[-1]: # Body close check
            signal = "SELL"

    if signal:
        atr = df_m15['atr'].iloc[-1]
        sl_dist = atr * 2
        tp_dist = sl_dist * 2
        
        sl = current_price - sl_dist if signal == "BUY" else current_price + sl_dist
        tp = current_price + tp_dist if signal == "BUY" else current_price - tp_dist

        msg = (f"🚀 *GOLD {signal} SIGNAL*\n\n"
               f"📍 Entry: {current_price:.2f}\n"
               f"🛑 SL: {sl:.2f}\n"
               f"🎯 TP: {tp:.2f}\n"
               f"📈 Trend: H4 {trend}\n"
               f"⏰ Time: {datetime.now().strftime('%H:%M PKT')}")
        
        send_telegram_message(msg)
        print("Signal sent to Telegram!")
    else:
        print("No signal detected.")

if __name__ == "__main__":
    run_bot()
