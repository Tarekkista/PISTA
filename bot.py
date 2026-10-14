import ccxt
import numpy as np
import pandas as pd
from datetime import datetime

# ==========================================
# 1. CONFIGURATION DU BACKTEST
# ==========================================
SYMBOL = 'RVV/USDT'          # Paire à backtester
TIMEFRAME = '1m'             # Unité de temps
FAST_LEN = 500               # MA Rapide
SLOW_LEN = 3                 # MA Lente
MA_TYPE = 'EMA'              # Type ('EMA' ou 'SMA')
SL_PERCENT = 5.0             # Stop Loss en %

INITIAL_CAPITAL = 1000.0
TRADE_SIZE_PCT = 1.0         # 100% du capital par trade (comme dans ton script Pine)

exchange = ccxt.bitget({'enableRateLimit': True})

# ==========================================
# 2. CHARGEMENT DES DONNÉES HISTORIQUES
# ==========================================
print(f"📥 Téléchargement des données historiques pour {SYMBOL} ({TIMEFRAME})...")
try:
    # Récupération max de bougies disponibles via l'API
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=1000)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
except Exception as e:
    print(f"⚠️ Erreur lors du téléchargement : {e}")
    exit()

# ==========================================
# 3. CALCUL DES INDICATEURS
# ==========================================
print("⚙️ Calcul des indicateurs (MAs)...")
if MA_TYPE == 'EMA':
    df['ma_fast'] = df['close'].ewm(span=FAST_LEN, adjust=False).mean()
    df['ma_slow'] = df['close'].ewm(span=SLOW_LEN, adjust=False).mean()
else:
    df['ma_fast'] = df['close'].rolling(window=FAST_LEN).mean()
    df['ma_slow'] = df['close'].rolling(window=SLOW_LEN).mean()

# ==========================================
# 4. MOTEUR DE BACKTEST (BOUCLE HISTORIQUE)
# ==========================================
print("🚀 Lancement de la simulation...")

capital = INITIAL_CAPITAL
position = None  # None ou dict {'entry_price': float, 'entry_time': datetime, 'sl': float}
trade_history = []

for i in range(1, len(df)):
    row = df.iloc[i]
    prev_row = df.iloc[i-1]
    
    current_time = row['datetime']
    open_price = row['open']
    high_price = row['high']
    low_price = row['low']
    close_price = row['close']
    
    ma_fast = row['ma_fast']
    ma_slow = row['ma_slow']
    prev_ma_fast = prev_row['ma_fast']
    prev_ma_slow = prev_row['ma_slow']
    
    # Conditions de croisement
    crossover = (prev_ma_fast <= prev_ma_slow) and (ma_fast > ma_slow)
    crossunder = (prev_ma_fast >= prev_ma_slow) and (ma_fast < ma_slow)
    
    # --- GESTION DE LA POSITION OUVERTE ---
    if position is not None:
        entry_price = position['entry_price']
        sl_price = position['sl']
        
        exit_triggered = False
        exit_price = 0.0
        exit_reason = ""
        
        # 1. Vérification du Stop Loss en premier (touche le bas de la bougie)
        if low_price <= sl_price:
            exit_triggered = True
            exit_price = sl_price
            exit_reason = "Stop Loss (%)"
            
        # 2. Vérification du Signal Inverse (croisement baissier)
        elif crossunder:
            exit_triggered = True
            exit_price = close_price
            exit_reason = "Signal Inverse"
            
        if exit_triggered:
            # Calcul du PnL
            pnl_pct = (exit_price - entry_price) / entry_price
            pnl_usd = capital * TRADE_SIZE_PCT * pnl_pct
            capital += pnl_usd
            
            res = 'WIN' if pnl_usd >= 0 else 'LOSS'
            trade_history.append({
                'symbol': SYMBOL,
                'entry_time': position['entry_time'],
                'exit_time': current_time,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'result': res,
                'pnl_usd': round(pnl_usd, 2),
                'exit_reason': exit_reason
            })
            
            position = None

    # --- RECHERCHE D'ENTRÉE ---
    if position is None and not np.isnan(ma_fast) and not np.isnan(ma_slow):
        if crossover:
            entry_price = open_price  # Entrée à l'ouverture de la bougie suivant le signal
            sl_price = entry_price * (1.0 - SL_PERCENT / 100.0)
            
            position = {
                'entry_price': entry_price,
                'entry_time': current_time,
                'sl': sl_price
            }

# ==========================================
# 5. RÉSULTATS DU BACKTEST
# ==========================================
df_trades = pd.DataFrame(trade_history)
filename_backtest = "backtest_resultats.csv"
df_trades.to_csv(filename_backtest, index=False)

total_trades = len(df_trades)
wins = len(df_trades[df_trades['result'] == 'WIN']) if total_trades > 0 else 0
win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
net_profit = capital - INITIAL_CAPITAL

print("\n" + "="*40)
print("📊 RAPPORT FINAL DE BACKTEST")
print("="*40)
print(f"Capital Initial : ${INITIAL_CAPITAL:.2f}")
print(f"Capital Final   : ${capital:.2f}")
print(f"Profit Net      : ${net_profit:+.2f}")
print(f"Total Trades    : {total_trades}")
print(f"Win Rate        : {win_rate:.1f}%")
print(f"Rapport sauvegardé dans : {filename_backtest}")
print("="*40)
