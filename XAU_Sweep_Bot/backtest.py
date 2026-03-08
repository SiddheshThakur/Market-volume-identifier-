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
                    entry_price = last_closed['close']
                    
                    if direction == "BUY":
                        sl_price = last_closed['low'] - config.SL_BUFFER_USD
                        tp_price = entry_price + (entry_price - sl_price) * config.RISK_REWARD_RATIO
                    else: # SELL
                        sl_price = last_closed['high'] + config.SL_BUFFER_USD
                        tp_price = entry_price - (sl_price - entry_price) * config.RISK_REWARD_RATIO
                        
                    # Simulate trade outcome
                    trade_outcome = "PENDING"
                    exit_price = 0.0
                    exit_time = None
                    pnl_r = 0.0
                    
                    # Scan forward in time starting from the very next candle (i + 1)
                    for j in range(i + 1, len(df)):
                        future_candle = df.iloc[j]
                        
                        if direction == "BUY":
                            # Note: To be precise, we check if low hits SL first, or high hits TP first
                            # For conservative backtesting, if both are hit in same candle, we count as LOSS.
                            if future_candle['low'] <= sl_price:
                                trade_outcome = "LOSS"
                                exit_price = sl_price
                                exit_time = future_candle['time']
                                pnl_r = -1.0
                                break
                            elif future_candle['high'] >= tp_price:
                                trade_outcome = "WIN"
                                exit_price = tp_price
                                exit_time = future_candle['time']
                                pnl_r = config.RISK_REWARD_RATIO
                                break
                                
                        elif direction == "SELL":
                            if future_candle['high'] >= sl_price:
                                trade_outcome = "LOSS"
                                exit_price = sl_price
                                exit_time = future_candle['time']
                                pnl_r = -1.0
                                break
                            elif future_candle['low'] <= tp_price:
                                trade_outcome = "WIN"
                                exit_price = tp_price
                                exit_time = future_candle['time']
                                pnl_r = config.RISK_REWARD_RATIO
                                break
                                
                    trade_duration = (exit_time - closed_time).total_seconds() / 60 if exit_time else 0
                    sl_pips = abs(entry_price - sl_price) * 10
                    tp_pips = abs(tp_price - entry_price) * 10
                    day_of_week = closed_time.strftime('%A')
                    
                    alert_desc = f"[{closed_time}] {session_name} {sweep_level} Sweep | DIR: {direction.ljust(4)} | Outcome: {trade_outcome} | Entry: {entry_price:.2f} | SL: {sl_price:.2f} | TP: {tp_price:.2f}"
                    alerts_triggered.append({
                        'Entry_Time': closed_time,
                        'Day_Of_Week': day_of_week,
                        'Trade_Duration_Mins': round(trade_duration, 2),
                        'Session_Swept': session_name,
                        'Level': sweep_level,
                        'Direction': direction,
                        'Candle_Size_USD': round(candle_range, 2),
                        'Volume': current_volume,
                        'Entry_Price': entry_price,
                        'Stop_Loss': sl_price,
                        'Take_Profit': tp_price,
                        'SL_Pips': round(sl_pips, 1),
                        'TP_Pips': round(tp_pips, 1),
                        'Exit_Time': exit_time,
                        'Exit_Price': exit_price,
                        'Outcome': trade_outcome,
                        'PnL_R': pnl_r
                    })
                    print(alert_desc)
                    break # Don't double log multiple session sweeps from one candle
                    
    print("\n--- BACKTEST COMPLETE ---")
    print(f"Total Quality Signals Found: {len(alerts_triggered)}")
    
    if len(alerts_triggered) > 0:
        alerts_df = pd.DataFrame(alerts_triggered)
        
        # Calculate overall stats
        completed_trades = alerts_df[alerts_df['Outcome'] != "PENDING"]
        wins = len(completed_trades[completed_trades['Outcome'] == 'WIN'])
        losses = len(completed_trades[completed_trades['Outcome'] == 'LOSS'])
        total_finished = wins + losses
        win_rate = (wins / total_finished * 100) if total_finished > 0 else 0
        total_pnl_r = completed_trades['PnL_R'].sum()
        
        print(f"Trades Completed: {total_finished}")
        print(f"Wins: {wins} | Losses: {losses} | Win Rate: {win_rate:.2f}%")
        print(f"Total Return in R-Multiples: {total_pnl_r:.2f} R")
        
        alerts_df.to_csv("backtest_results.csv", index=False)
        try:
            alerts_df.to_excel("backtest_results.xlsx", index=False)
            print("Detailed results saved to 'backtest_results.xlsx' and 'backtest_results.csv'")
        except ImportError:
            print("Detailed results saved to 'backtest_results.csv' (install openpyxl for .xlsx format)")

    mt5.shutdown()

if __name__ == "__main__":
    run_backtest(days_back=14)
