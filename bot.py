import ccxt
import numpy as np
import pandas as pd

# ==========================================
# 1. PARAMÈTRES DU BACKTEST
# ==========================================
SYMBOL = 'DMC/USDT'
TIMEFRAME = '1m'
CANDLES_TO_FETCH = 1000  # Nombre de bougies par appel Bitget

START_DATE = '2026-09-01 00:00:00'
END_DATE   = '2026-10-30 23:59:00'

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

start_ms = int(pd.Timestamp(START_DATE).timestamp() * 1000)
end_ms = int(pd.Timestamp(END_DATE).timestamp() * 1000)

all_ohlcv = []
current_ms = start_ms

while current_ms <= end_ms:
    ohlcv = exchange.fetch_ohlcv(
        SYMBOL,
        timeframe=TIMEFRAME,
        since=current_ms,
        limit=CANDLES_TO_FETCH
    )

    if not ohlcv:
        break

    all_ohlcv.extend(ohlcv)

    last_timestamp = ohlcv[-1][0]

    if last_timestamp >= end_ms:
        break

    # Bougie suivante pour éviter les doublons
    current_ms = last_timestamp + 60 * 1000

    print(
        f"   → {pd.to_datetime(last_timestamp, unit='ms')} "
        f"| {len(all_ohlcv)} bougies"
    )

df = pd.DataFrame(
    all_ohlcv,
    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
)

df = df.drop_duplicates(subset='timestamp')

df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')

# Garder exactement la période demandée
df = df[
    (df['datetime'] >= START_DATE) &
    (df['datetime'] <= END_DATE)
].copy()

print(f"✅ {len(df)} bougies chargées.")
print(f"📅 Période réelle : {df['datetime'].iloc[0]} ➔ {df['datetime'].iloc[-1]}")

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

    # Statistiques
    total_trades = len(trades_history)
    wins = sum(1 for t in trades_history if t['result'] == 'WIN')
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    pnl_total = capital - INITIAL_CAPITAL

    return capital, total_trades, win_rate, pnl_total

# ==========================================
# 4. EXÉCUTION COMPARATIVE
# ==========================================
cap_30, trades_30, wr_30, pnl_30 = run_backtest(df, window_size=30)
cap_60, trades_60, wr_60, pnl_60 = run_backtest(df, window_size=60)

print(f"\n📊 ==================== RÉSULTATS DU BACKTEST ({SYMBOL}) ====================")
print(f"Période analysée : {START_DATE} ➔ {END_DATE}")
print("-" * 75)
print(f"{'BOT':<15} | {'CAPITAL FINAL':<14} | {'PNL TOTAL ($)':<14} | {'TRADES':<8} | {'WIN RATE':<10}")
print("-" * 75)
print(f"{'DONCHIAN 30M':<15} | ${cap_30:<13.2f} | ${pnl_30:<+13.2f} | {trades_30:<8} | {wr_30:.1f}%")
print(f"{'DONCHIAN 60M':<15} | ${cap_60:<13.2f} | ${pnl_60:<+13.2f} | {trades_60:<8} | {wr_60:.1f}%")
print("=" * 75)
