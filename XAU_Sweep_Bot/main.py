from datetime import datetime, time as datetime_time, timedelta
import pytz
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import requests
import time
import math
import logging
import sys

import config

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_activity.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

# STEP 5 - Define Trading Sessions
def parse_time(time_str):
    return datetime.strptime(time_str, "%H:%M").time()

ASIA_START = parse_time(config.ASIA_START)
ASIA_END = parse_time(config.ASIA_END)

LONDON_START = parse_time(config.LONDON_START)
LONDON_END = parse_time(config.LONDON_END)

NY_START = parse_time(config.NY_START)
NY_END = parse_time(config.NY_END)

SYMBOL = config.SYMBOL
TIMEFRAME = mt5.TIMEFRAME_M5

def send_telegram_alert(message):
    """STEP 10 - Telegram Alert Function"""
    if config.BOT_TOKEN == "your_token_here" or config.CHAT_ID == "your_chat_id_here":
        logging.warning("Telegram Alert (Not Sent - Please configure config.py):\n" + message)
        return
        
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logging.error(f"Failed to send Telegram alert: {e}")

def calculate_position(sl_distance_usd):
    """Calculate the lot size based on fixed USD amount."""
    if config.RISK_PER_TRADE_USD <= 0:
        return config.MIN_LOT_SIZE
        
    symbol_info = mt5.symbol_info(config.SYMBOL)
    if symbol_info is None:
        return config.MIN_LOT_SIZE
        
    contract_size = symbol_info.trade_contract_size if hasattr(symbol_info, 'trade_contract_size') else 100.0
    
    if sl_distance_usd == 0:
        return config.MIN_LOT_SIZE
        
    lot_size = config.RISK_PER_TRADE_USD / (sl_distance_usd * contract_size)
    lot_size = max(config.MIN_LOT_SIZE, min(float(config.MAX_LOT_SIZE), lot_size))
    
    # Round to MT5 step
    step = symbol_info.volume_step if hasattr(symbol_info, 'volume_step') else 0.01
    lot_size = round(lot_size / step) * step
    return lot_size

def execute_trade(direction, entry_price, sl_price, tp_price):
    """Execute the live trade via MT5."""
    symbol_info = mt5.symbol_info(config.SYMBOL)
    if symbol_info is None or not symbol_info.visible:
        if not mt5.symbol_select(config.SYMBOL, True):
            logging.error(f"symbol_select({config.SYMBOL}) failed")
            return None

    # Calculate Lot Size
    sl_distance = abs(entry_price - sl_price)
    lot = calculate_position(sl_distance)
    
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    price = mt5.symbol_info_tick(config.SYMBOL).ask if direction == "BUY" else mt5.symbol_info_tick(config.SYMBOL).bid
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": config.SYMBOL,
        "volume": float(lot),
        "type": order_type,
        "price": price,
        "sl": float(sl_price),
        "tp": float(tp_price),
        "deviation": 20,
        "magic": config.MAGIC_NUMBER,
        "comment": f"XAUSweep {direction}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logging.error(f"Order failed, retcode={result.retcode}. Trying alternative fill modes...")
        for mode in [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]:
            request["type_filling"] = mode
            res = mt5.order_send(request)
            if res.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f"Order placed with fallback mode {mode}: Ticket {res.order}")
                return res
        return None
        
    logging.info(f"Order placed successfully: Ticket {result.order}")
    return result

def get_recent_candles(n=1000):
    """STEP 4 - Pull M5 candles (increased to 1000 to get prev day)"""
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, n)
    if rates is None or len(rates) == 0:
        return None
        
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def calculate_atr(df, period=14):
    """STEP 8 - Displacement Filter"""
    df = df.copy()
    df['H-L'] = df['high'] - df['low']
    df['H-PC'] = abs(df['high'] - df['close'].shift(1))
    df['L-PC'] = abs(df['low'] - df['close'].shift(1))
    df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    df['ATR'] = df['TR'].rolling(window=period).mean()
    return df

def get_session_high_low(df):
    """STEP 5 - Calculate Previous Day and Session Highs/Lows"""
    sessions = {}
    
    # Get all unique dates to find previous trading day safely
    all_dates = df['time'].dt.date.unique()
    today = all_dates[-1] if len(all_dates) > 0 else df.iloc[-1]['time'].date()
    yesterday_date = all_dates[-2] if len(all_dates) >= 2 else today - timedelta(days=1)
        
    today_candles = df[df['time'].dt.date == today]
    yesterday_candles = df[df['time'].dt.date == yesterday_date]
    
    if not yesterday_candles.empty:
        sessions['Previous Day'] = {
            'High': yesterday_candles['high'].max(),
            'Low': yesterday_candles['low'].min()
        }
        
    def get_extremes(candles, start_t, end_t):
        if start_t < end_t:
            sc = candles[(candles['time'].dt.time >= start_t) & (candles['time'].dt.time < end_t)]
        else:
            sc = candles[(candles['time'].dt.time >= start_t) | (candles['time'].dt.time < end_t)]
        if sc.empty: return None, None
        return sc['high'].max(), sc['low'].min()

    # Get Today's Sessions if they have passed or are active
    a_high, a_low = get_extremes(today_candles, ASIA_START, ASIA_END)
    if a_high is not None: sessions['Asia'] = {'High': a_high, 'Low': a_low}
        
    l_high, l_low = get_extremes(today_candles, LONDON_START, LONDON_END)
    if l_high is not None: sessions['London'] = {'High': l_high, 'Low': l_low}
        
    ny_high, ny_low = get_extremes(today_candles, NY_START, NY_END)
    if ny_high is not None: sessions['New York'] = {'High': ny_high, 'Low': ny_low}
        
    return sessions

# We need a way to track alerts so we don't spam the same candle
last_alert_time = None

def analyze_market():
    """STEP 9 - Combine All Conditions"""
    global last_alert_time
    
    df = get_recent_candles(1000)
    if df is None or len(df) < 50:
        logging.warning("Not enough data to analyze")
        return
        
    df = calculate_atr(df, period=config.ATR_PERIOD)
    sessions = get_session_high_low(df)
    
    # We look at the last fully closed candle. 
    # df.iloc[-1] is the current forming candle. df.iloc[-2] is the last closed.
    last_closed = df.iloc[-2]
    closed_time = last_closed['time']
    
    # If we already processed this specific candle, don't spam
    if last_alert_time == closed_time:
        return
        
    # STEP 7 - Volume Spike Logic
    # 20-candle average volume of previously closed candles
    avg_volume = df.iloc[-22:-2]['tick_volume'].mean()
    current_volume = last_closed['tick_volume']
    is_volume_spike = current_volume > (config.VOLUME_SPIKE_MULTIPLIER * avg_volume)
    
    # STEP 8 - Displacement Filter
    atr_value = df.iloc[-2]['ATR']
    candle_range = last_closed['high'] - last_closed['low']
    has_displacement = candle_range > (config.DISPLACEMENT_MULTIPLIER * atr_value)
    
    # Check sweeps for both sessions
    for session_name, levels in sessions.items():
        if levels['High'] is None or levels['Low'] is None:
            continue
            
        session_high = levels['High']
        session_low = levels['Low']
        
        # STEP 6 - Liquidity Sweep Logic
        bullish_sweep = (last_closed['low'] < session_low) and (last_closed['close'] > session_low)
        bearish_sweep = (last_closed['high'] > session_high) and (last_closed['close'] < session_high)
        
        if bullish_sweep or bearish_sweep:
            # We detected a sweep on this candle. Mark this candle as seen.
            last_alert_time = closed_time
            
            direction = "BUY" if bullish_sweep else "SELL"
            sweep_level = "Low" if bullish_sweep else "High"
            
            # STEP 12 - Print for Testing Phase validation
            logging.info(f"[{closed_time}] {session_name} {sweep_level} Sweep detected. Direction: {direction}")
            logging.info(f"Volume Spike: {'YES' if is_volume_spike else 'NO'} (Vol: {current_volume}, Avg: {avg_volume:.2f})")
            logging.info(f"Displacement: {'YES' if has_displacement else 'NO'} (Range: {candle_range:.2f}, ATR: {atr_value:.2f})")
            
            # Condition check
            if is_volume_spike and has_displacement:
                 entry_price = last_closed['close']
                 
                 # Calculate Stop Loss and Take Profit
                 if direction == "BUY":
                     sl_price = last_closed['low'] - config.SL_BUFFER_USD
                     tp_price = entry_price + (entry_price - sl_price) * config.RISK_REWARD_RATIO
                 else:
                     sl_price = last_closed['high'] + config.SL_BUFFER_USD
                     tp_price = entry_price - (sl_price - entry_price) * config.RISK_REWARD_RATIO
                     
                 # Execute Live Trade
                 trade_result = execute_trade(direction, entry_price, sl_price, tp_price)
                 trade_status = "SUCCESS" if trade_result else "FAILED"
                 ticket_num = getattr(trade_result, 'order', 'N/A') if trade_result else "N/A"
                 lot_qty = getattr(trade_result, 'volume', 'N/A') if trade_result else "N/A"

                 message = (
                    f"🚨 <b>XAUUSD Live Trade Executed</b> 🚨\n\n"
                    f"<b>Session:</b> {session_name} {sweep_level}\n"
                    f"<b>Direction:</b> {direction}\n"
                    f"<b>Execution:</b> {trade_status}\n"
                    f"<b>Ticket:</b> #{ticket_num} ({lot_qty} Lots)\n"
                    f"<b>Entry:</b> {entry_price:.2f}\n"
                    f"<b>Stop Loss:</b> {sl_price:.2f}\n"
                    f"<b>Take Profit:</b> {tp_price:.2f}\n\n"
                    f"<i>Volume Spike & Displacement Confirmed</i>"
                )
                 send_telegram_alert(message)
                 logging.info(f"--> Live Trade Attempted: {trade_status}")
                 
                 # Break out once we send an alert to avoid double-firing for two sessions simultaneously
                 break

def main():
    logging.info("Starting XAUUSD Liquidity Sweep Detector...")
    
    # STEP 4 - Initialize MT5
    if not mt5.initialize():
        logging.error(f"initialize() failed, error code = {mt5.last_error()}")
        return
        
    logging.info(f"MT5 Initialized. Version: {mt5.version()}")
    
    # Select Symbol
    if not mt5.symbol_select(SYMBOL, True):
        logging.error(f"Failed to select {SYMBOL}!")
        mt5.shutdown()
        return
        
    logging.info(f"Successfully selected {SYMBOL}. Listening for sweeps...")
    logging.info("Press Ctrl+C to exit.")
    
    # STEP 11 - Run Bot Continuously
    # We check periodically. A new M5 candle appears every 5 minutes.
    try:
        while True:
            # We add a try-except to prevent crashes breaking the loop
            try:
                # Disconnection Check
                ti = mt5.terminal_info()
                if ti is None or not ti.connected:
                    logging.warning("MT5 disconnected! Attempting to reconnect...")
                    mt5.initialize()
                    mt5.symbol_select(SYMBOL, True)
                    
                analyze_market()
            except Exception as e:
                logging.error(f"Error during analysis: {e}")
                
            time.sleep(60) # check every 60 seconds
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()
