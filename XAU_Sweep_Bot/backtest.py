import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import config

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

def get_historical_candles(days_back=30):
    """Pull historical M5 candles for backtesting"""
    now = datetime.now()
    # Pull maximum needed bars: 30 days * 24 hours * 12 bars = 8640 bars
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

def get_session_high_low(df, current_time):
    """Calculate Previous Day and Session Highs/Lows relative to a given point in time"""
    sessions = {}
    
    # Filter only candles up to the current evaluation point
    df_past = df[df['time'] <= current_time]
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

    # Get Today's Sessions if they have passed or are active
    a_high, a_low = get_extremes(today_candles, ASIA_START, ASIA_END)
    if a_high is not None: sessions['Asia'] = {'High': a_high, 'Low': a_low}
        
    l_high, l_low = get_extremes(today_candles, LONDON_START, LONDON_END)
    if l_high is not None: sessions['London'] = {'High': l_high, 'Low': l_low}
        
    ny_high, ny_low = get_extremes(today_candles, NY_START, NY_END)
    if ny_high is not None: sessions['New York'] = {'High': ny_high, 'Low': ny_low}
        
    return sessions

def run_backtest(days_back=30):
    print(f"Starting Backtest on {SYMBOL} for last {days_back} days...")
    
    if not mt5.initialize():
        print(f"initialize() failed, error code = {mt5.last_error()}")
        return
        
    if not mt5.symbol_select(SYMBOL, True):
        print(f"Failed to select {SYMBOL}!")
        mt5.shutdown()
        return
        
    df = get_historical_candles(days_back)
    if df is None:
        print("Failed to pull historical data.")
        mt5.shutdown()
        return
        
    print(f"Loaded {len(df)} candles.")
    df = calculate_atr(df, period=config.ATR_PERIOD)
    
    alerts_triggered = []
    
    # We need to simulate time passing candle by candle
    # We need at least enough history to establish previous day & avg volume
    start_index = 500 # Wait enough candles to have pre-day history and ATR
    
    print("\n--- BEGINNING SIMULATION ---")
    
    for i in range(start_index, len(df)):
        # Simulate that the current time is the time of candle i
        current_candle_time = df.iloc[i]['time']
        
        # 'last_closed' in the live script is the candle previous to real-time. 
        # Here we assume df.iloc[i] is the newly formed closed candle that we are analyzing.
        last_closed = df.iloc[i]
        closed_time = last_closed['time']
        
        # Calculate session highs/lows based on data BEFORE this candle closed + including this candle?
        # Actually in live script, get_session_high_low uses data up to the current forming candle.
        sessions = get_session_high_low(df, closed_time)
        
        # Volume Spike Logic (20 candles prior to last_closed)
        avg_volume = df.iloc[i-20:i]['tick_volume'].mean()
        current_volume = last_closed['tick_volume']
        is_volume_spike = current_volume > (config.VOLUME_SPIKE_MULTIPLIER * avg_volume)
        
        # Displacement Logic
        atr_value = df.iloc[i]['ATR']
        candle_range = last_closed['high'] - last_closed['low']
        has_displacement = candle_range > (config.DISPLACEMENT_MULTIPLIER * atr_value)
        
        for session_name, levels in sessions.items():
            if levels['High'] is None or levels['Low'] is None:
                continue
                
            session_high = levels['High']
            session_low = levels['Low']
            
            # Liquidity Sweep Logic
            bullish_sweep = (last_closed['low'] < session_low) and (last_closed['close'] > session_low)
            bearish_sweep = (last_closed['high'] > session_high) and (last_closed['close'] < session_high)
            
            if bullish_sweep or bearish_sweep:
                direction = "BUY" if bullish_sweep else "SELL"
                sweep_level = "Low" if bullish_sweep else "High"
                
                # We check displacement + volume spike
                if is_volume_spike and has_displacement:
                    alert_desc = f"[{closed_time}] {session_name} {sweep_level} Sweep | DIR: {direction.ljust(4)} | Vol:{current_volume}/{avg_volume:.0f} | Dsp:{candle_range:.1f}/{atr_value:.1f}"
                    alerts_triggered.append({
                        'Time': closed_time,
                        'Session Swept': session_name,
                        'Level': sweep_level,
                        'Direction': direction,
                    })
                    print(alert_desc)
                    break # Don't double log multiple session sweeps from one candle
                    
    print("\n--- BACKTEST COMPLETE ---")
    print(f"Total Quality Signals Found: {len(alerts_triggered)}")
    
    if len(alerts_triggered) > 0:
        alerts_df = pd.DataFrame(alerts_triggered)
        alerts_df.to_csv("backtest_results.csv", index=False)
        print("Detailed results saved to 'backtest_results.csv'")

    mt5.shutdown()

if __name__ == "__main__":
    run_backtest(days_back=14)
