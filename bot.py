import ccxt
import numpy as np
import pandas as pd
from datetime import datetime

# ==========================================
# 1. PARAMÈTRES DU BACKTEST
# ==========================================
SYMBOL = 'DMC/USDT'
TIMEFRAME = '1m'

START_DATE = '2026-07-01'  # Date début
END_DATE = '2026-12-31'    # Date fin

EMA_PERIOD = 100
ATR_PERIOD = 14
ATR_MULTIPLIER = 1.8

INITIAL_CAPITAL = 1000.0
TRADE_SIZE = 150.0

# ==========================================
# 2. CHARGEMENT DES DONNÉES HISTORIQUES
# ==========================================
print(f"📥 Téléchargement de {SYMBOL} du {START_DATE} au {END_DATE}...")
exchange = ccxt.bitget()

start_ts = exchange.parse8601(START_DATE + 'T00:00:00Z')
end_ts = exchange.parse8601(END_DATE + 'T23:59:59Z')

all_candles = []
since = start_ts
while since < end_ts:
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, since=since, limit=1000)
    if not ohlcv:
        break
    all_candles.extend(ohlcv)
    since = ohlcv[-1][0] + 60000

df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
df['month'] = df['datetime'].dt.to_period('M')

# ==========================================
# 3. MOTEUR DE BACKTEST
# ==========================================
def run_backtest(df_input, window_size):
    df = df_input.copy()
    
    # Indicateurs
    df['ema'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    df['donchian_high'] = df['high'].shift(1).rolling(window=window_size).max()
    
    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
    )
    df['atr'] = tr.rolling(window=ATR_PERIOD).mean()
    df['vol_ma'] = df['volume'].rolling(window=window_size).mean()

    capital = INITIAL_CAPITAL
    in_position = False
    entry_price = 0.0
    trailing_sl = 0.0
    trades_history = []

    # Simulation bougie par bougie
    for i in range(max(window_size, EMA_PERIOD), len(df)):
        row = df.iloc[i]
        close = row['close']
        high = row['high']
        low = row['low']
        atr = row['atr']
        month = row['month']
        
        # 1. Gestion de la position ouverte
        if in_position:
            # Mise à jour du Trailing Stop (Ratchet)
            potential_sl = close - (atr * ATR_MULTIPLIER)
            if potential_sl > trailing_sl:
                trailing_sl = potential_sl
            
            # Vérification de sortie (si le prix bas de la bougie touche le SL)
            if low <= trailing_sl:
                exit_price = trailing_sl
                pnl_pct = (exit_price - entry_price) / entry_price
                pnl_usd = TRADE_SIZE * pnl_pct
                capital += pnl_usd
                
                trades_history.append({
                    'month': month,
                    'entry_time': df.iloc[i]['datetime'],
                    'result': 'WIN' if pnl_usd >= 0 else 'LOSS',
                    'pnl': pnl_usd
                })
                in_position = False

        # 2. Conditions d'entrée si pas de position active
        elif not in_position:
            is_uptrend = close > row['ema']
            is_breakout = close > row['donchian_high']
            is_vol = row['volume'] > row['vol_ma']
            
            if is_uptrend and is_breakout and is_vol:
                in_position = True
                entry_price = close
                trailing_sl = entry_price - (atr * ATR_MULTIPLIER)

    return capital, trades_history, df

# ==========================================
# 4. EXÉCUTION ET RÉSULTATS PAR MOIS
# ==========================================
cap_30, trades_30, df_30 = run_backtest(df, window_size=30)
cap_60, trades_60, df_60 = run_backtest(df, window_size=60)

print(f"\n📊 ==================== RÉSULTATS PAR MOIS ({SYMBOL}) ====================")
print(f"Période analysée : {START_DATE} ➔ {END_DATE}")
print("=" * 100)

for window, trades in [("DONCHIAN 30M", trades_30), ("DONCHIAN 60M", trades_60)]:
    print(f"\n{window}")
    print("-" * 100)
    print(f"{'MOIS':<12} | {'TRADES':<8} | {'GAGNANTS':<10} | {'PERDANTS':<10} | {'PNL TOTAL ($)':<15} | {'WIN RATE':<10}")
    print("-" * 100)
    
    trades_df = pd.DataFrame(trades)
    if len(trades_df) > 0:
        for month, group in trades_df.groupby('month'):
            total = len(group)
            wins = len(group[group['result'] == 'WIN'])
            losses = len(group[group['result'] == 'LOSS'])
            pnl = group['pnl'].sum()
            wr = (wins / total * 100) if total > 0 else 0
            
            print(f"{str(month):<12} | {total:<8} | {wins:<10} | {losses:<10} | ${pnl:<+14.2f} | {wr:<10.1f}%")
    else:
        print("Aucun trade")
    
    print("=" * 100)
