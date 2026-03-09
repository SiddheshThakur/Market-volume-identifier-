import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
import requests
import sys

import config

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

def get_historical_candles_around(days_back=60):
    n_bars = days_back * 24 * 12
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

def calculate_atr(df, period=14):
    df = df.copy()
    df['H-L'] = df['high'] - df['low']
    df['H-PC'] = abs(df['high'] - df['close'].shift(1))
    df['L-PC'] = abs(df['low'] - df['close'].shift(1))
    df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    df['ATR'] = df['TR'].rolling(window=period).mean()
    return df

def get_session_high_low(df_past):
    sessions = {}
    if len(df_past) == 0:
        return sessions
        
    all_dates = df_past['time'].dt.date.unique()
    today = all_dates[-1] if len(all_dates) > 0 else df_past.iloc[-1]['time'].date()
    yesterday_date = all_dates[-2] if len(all_dates) >= 2 else today - timedelta(days=1)
        
    today_candles = df_past[df_past['time'].dt.date == today]
    yesterday_candles = df_past[df_past['time'].dt.date == yesterday_date]
    
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

    a_high, a_low = get_extremes(today_candles, ASIA_START, ASIA_END)
    if a_high is not None: sessions['Asia'] = {'High': a_high, 'Low': a_low}
        
    l_high, l_low = get_extremes(today_candles, LONDON_START, LONDON_END)
    if l_high is not None: sessions['London'] = {'High': l_high, 'Low': l_low}
        
    ny_high, ny_low = get_extremes(today_candles, NY_START, NY_END)
    if ny_high is not None: sessions['New York'] = {'High': ny_high, 'Low': ny_low}
        
    return sessions

def analyze_specific_candle(target_time_str):
    print("Connecting to MetaTrader 5...")
    if not mt5.initialize():
        print(f"initialize() failed, error code = {mt5.last_error()}")
        return

    try:
        target_time = pd.to_datetime(target_time_str)
    except Exception as e:
        print(f"[{target_time_str}] is an invalid date format. Use YYYY-MM-DD HH:MM. Error: {e}")
        mt5.shutdown()
        return

    print("Fetching historical data...")
    df = get_historical_candles_around(days_back=60)
    if df is None:
        print("Failed to pull historical data.")
        mt5.shutdown()
        return

    df = calculate_atr(df, period=config.ATR_PERIOD)
    
    # Check if the time exactly matches a candle open time
    candle_idx = df.index[df['time'] == target_time].tolist()
    if not candle_idx:
        print(f"Candle for time {target_time} not found in MT5 data.")
        print("Please check the time (it must be a 5-minute increment like 15:00, 15:05, 15:10) and try again.")
        mt5.shutdown()
        return
        
    i = candle_idx[0]
    target_candle = df.iloc[i]
    
    # Ensure there is enough data behind it
    if i < 20:
        print("Not enough history available before this candle to calculate volume spike accurately (Need 20 candles prior).")
        mt5.shutdown()
        return

    sessions = get_session_high_low(df.iloc[:i]) # sessions using data up to BEFORE the current candle
    
    avg_volume = df.iloc[i-20:i]['tick_volume'].mean()
    current_volume = target_candle['tick_volume']
    is_volume_spike = current_volume > (config.VOLUME_SPIKE_MULTIPLIER * avg_volume)
    
    atr_value = df.iloc[i]['ATR']
    candle_range = target_candle['high'] - target_candle['low']
    has_displacement = candle_range > (config.DISPLACEMENT_MULTIPLIER * atr_value)
    
    bullish_sweeps = []
    bearish_sweeps = []
    session_data = []
    
    for s_name, levels in sessions.items():
        if levels['High'] is None or levels['Low'] is None:
            continue
        sh, sl = levels['High'], levels['Low']
        session_data.append(f"{s_name} (High: {sh:.2f}, Low: {sl:.2f})")
        
        if target_candle['low'] < sl and target_candle['close'] > sl:
            bullish_sweeps.append(s_name)
        if target_candle['high'] > sh and target_candle['close'] < sh:
            bearish_sweeps.append(s_name)
            
    print("\n--- Candle Analysis Complete ---")
    report = {
        'Metric': [],
        'Value': []
    }
    
    def add(m, v):
        report['Metric'].append(m)
        report['Value'].append(v)
        
    add('Target Time', target_time)
    add('Open', target_candle['open'])
    add('High', target_candle['high'])
    add('Low', target_candle['low'])
    add('Close', target_candle['close'])
    add('--', '--')
    add('Tick Volume', current_volume)
    add('Avg Volume (Last 20)', round(avg_volume, 2))
    add('Required Vol Threshold', round(avg_volume * config.VOLUME_SPIKE_MULTIPLIER, 2))
    add('Volume Spike Detected?', 'YES' if is_volume_spike else 'NO')
    add('--', '--')
    add('Candle Range (Points/USD)', round(candle_range, 2))
    add('ATR (14 period)', round(atr_value, 2))
    add('Required Disp Threshold', round(atr_value * config.DISPLACEMENT_MULTIPLIER, 2))
    add('Displacement Detected?', 'YES' if has_displacement else 'NO')
    add('--', '--')
    add('Bullish Sweeps', ", ".join(bullish_sweeps) if bullish_sweeps else "None")
    add('Bearish Sweeps', ", ".join(bearish_sweeps) if bearish_sweeps else "None")
    add('--', '--')
    add('Active Sessions Before Close', " | ".join(session_data) if session_data else "None")
    
    df_report = pd.DataFrame(report)
    
    # Clean string format for filename
    file_time = target_time.strftime('%Y%m%d_%H%M')
    excel_filename = f"Candle_{file_time}_Report.xlsx"
    
    try:
        df_report.to_excel(excel_filename, index=False)
        print(f"\nExcel report saved successfully to: {excel_filename}")
        
        # Send to Telegram
        if hasattr(config, 'BOT_TOKEN') and config.BOT_TOKEN and config.BOT_TOKEN != "your_token_here":
            print("\nUploading Excel file to Telegram...")
            url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendDocument"
            with open(excel_filename, 'rb') as f:
                emoji = "📈" if bullish_sweeps else ("📉" if bearish_sweeps else "📊")
                caption = f"{emoji} <b>Candle Analysis for {target_time}</b>\n\nSweeps: {len(bullish_sweeps)+len(bearish_sweeps)}\nVol Spike: {'Yes' if is_volume_spike else 'No'}\nDisplacement: {'Yes' if has_displacement else 'No'}"
                response = requests.post(url, 
                                         data={'chat_id': config.CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, 
                                         files={'document': f})
                if response.status_code == 200:
                    print(f"SUCCESS: Excel file delivered to Telegram Chat ID {config.CHAT_ID}")
                else:
                    print(f"ERROR: Failed to send to Telegram. {response.text}")
        else:
            print("\nNotice: Telegram BOT_TOKEN is missing in config.py. Skipping upload to phone.")
            
    except Exception as e:
        print(f"\nError saving/sending excel: {e}")
        
    mt5.shutdown()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Received arguments from command line
        t = " ".join(sys.argv[1:])
    else:
        # Prompt user for input
        print("==============================================")
        print("         TELEGRAM CANDLE INSPECTOR            ")
        print("==============================================")
        t = input("Enter Candle Date and Time (YYYY-MM-DD HH:MM)\nExample: 2026-03-05 15:20\n> ")
        
    analyze_specific_candle(t)
    print("\nPress Enter to exit.")
    input()
