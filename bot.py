import ccxt
import numpy as np
import pandas as pd
import time
from datetime import datetime
import lightgbm as lgb
import warnings

warnings.filterwarnings('ignore')

# ==========================================
# 1. CONFIGURATION STRATÉGIQUE & RAM
# ==========================================
TIMEFRAME = '3m'
SEQ_LEN = 60            # 60 bougies pour calculer les indicateurs
HORIZON = 45           # Durée max du trade (45 min)
TP_PCT = 0.009         # Take Profit : +1.2%
SL_PCT = 0.003         # Stop Loss : -0.8%
CANDLES_TO_FETCH = 1000 # Historique de données (~25h)

INITIAL_CAPITAL = 1000
TRADE_SIZE = 100
THRESHOLD = 0.53       # Seuil de probabilité IA pour entrer (55%)

TOKENS = [
    'RLC/USDT', 'DMC/USDT', 'MOVR/USDT', 'QUBIC/USDT', 'AIN/USDT', 'KAIO/USDT'
]

exchange = ccxt.bitget({'enableRateLimit': True})

# ==========================================
# 2. FEATURE ENGINEERING QUANTITATIF
# ==========================================
def compute_features(df):
    """Calcule 9 indicateurs stationnaires et pertinents pour le scalping 1m."""
    df = df.copy()
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']
    
    # 1. Log Returns (Momentum)
    df['ret_1'] = np.log(close / close.shift(1))
    df['ret_3'] = np.log(close / close.shift(3))
    df['ret_5'] = np.log(close / close.shift(5))
    df['ret_15'] = np.log(close / close.shift(15))
    
    # 2. RSI 14
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-8)
    df['rsi_14'] = 100 - (100 / (1 + rs))
    
    # 3. Normalized ATR (Volatilité)
    tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
    df['atr_norm'] = tr.rolling(14).mean() / close
    
    # 4. Bollinger Bands %B
    ma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    df['bb_pct'] = (close - (ma20 - 2 * std20)) / (4 * std20 + 1e-8)
    
    # 5. Volume Z-Score (Spikes de volume)
    vol_ma = volume.rolling(20).mean()
    vol_std = volume.rolling(20).std()
    df['vol_zscore'] = (volume - vol_ma) / (vol_std + 1e-8)
    
    # 6. Intraday Range / Spread High-Low
    df['hl_spread'] = (high - low) / close
    
    # Features finales nettoyées des NaN
    features = ['ret_1', 'ret_3', 'ret_5', 'ret_15', 'rsi_14', 'atr_norm', 'bb_pct', 'vol_zscore', 'hl_spread']
    return df, features

def label_sequence(df, entry_idx, horizon, tp_pct, sl_pct):
    entry_price = df['close'].iloc[entry_idx]
    tp_price = entry_price * (1 + tp_pct)
    sl_price = entry_price * (1 - sl_pct)
    
    window = df.iloc[entry_idx + 1 : entry_idx + 1 + horizon]
    for _, row in window.iterrows():
        if row['low'] <= sl_price:
            return 0
        if row['high'] >= tp_price:
            return 1
    return 0

def fetch_ohlcv_extended(symbol, timeframe, total_candles):
    limit = 200
    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    since = exchange.milliseconds() - total_candles * tf_ms
    all_rows = []
    
    for _ in range((total_candles // limit) + 2):
        try:
            batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
            if not batch:
                break
            all_rows += batch
            since = batch[-1][0] + tf_ms
            time.sleep(exchange.rateLimit / 1000)
            if len(all_rows) >= total_candles:
                break
        except Exception:
            break
            
    df = pd.DataFrame(all_rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df.tail(total_candles).reset_index(drop=True)

# ==========================================
# 3. ENTRAÎNEMENT DU MODÈLE LIGHTGBM
# ==========================================
print("⚡ Téléchargement des données d'entraînement...")
raw_data = {}
for symbol in TOKENS:
    df = fetch_ohlcv_extended(symbol, TIMEFRAME, CANDLES_TO_FETCH)
    if len(df) >= SEQ_LEN + HORIZON:
        df_feat, feature_cols = compute_features(df)
        raw_data[symbol] = (df_feat, feature_cols)
        print(f"    ✅ {symbol:<10} : {len(df)} bougies préparées.")

X, y = [], []
for symbol, (df, feature_cols) in raw_data.items():
    n = len(df)
    for entry_idx in range(SEQ_LEN, n - HORIZON):
        row_features = df.iloc[entry_idx][feature_cols].values
        if np.isnan(row_features).any():
            continue
        label = label_sequence(df, entry_idx, HORIZON, TP_PCT, SL_PCT)
        X.append(row_features)
        y.append(label)

X = np.array(X, dtype=np.float32)
y = np.array(y, dtype=np.int32)

print(f"\n📊 Échantillons d'entraînement : {X.shape[0]}")
print(f"🎯 Ratio Positif Historique : {np.mean(y)*100:.2f}%")

# Configuration LightGBM Ultra-Léger (< 20MB RAM)
model = lgb.LGBMClassifier(
    n_estimators=40,
    max_depth=3,
    num_leaves=7,
    learning_rate=0.05,
    class_weight='balanced',
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42,
    verbosity=-1
)

print("\n🏋️ Entraînement du modèle LightGBM...")
model.fit(X, y)
print("✅ Modèle LightGBM prêt !")

# ==========================================
# 4. MOTEUR DE PAPER TRADING
# ==========================================
capital = INITIAL_CAPITAL
open_positions = []
trade_history = []

def check_open_positions():
    global capital, open_positions, trade_history
    positions_to_remove = []
    
    for pos in open_positions:
        symbol = pos['symbol']
        try:
            ticker = exchange.fetch_ticker(symbol)
            current_price = ticker['last']
            elapsed_minutes = (datetime.now() - pos['entry_time']).total_seconds() / 60
            
            if current_price >= pos['tp']:
                profit = TRADE_SIZE * TP_PCT
                capital += profit
                trade_history.append({'symbol': symbol, 'result': 'WIN', 'pnl': profit})
                print(f"\n🎉 [WIN] {symbol} | Gain : +${profit:.2f}")
                positions_to_remove.append(pos)
                
            elif current_price <= pos['sl']:
                loss = TRADE_SIZE * SL_PCT
                capital -= loss
                trade_history.append({'symbol': symbol, 'result': 'LOSS', 'pnl': -loss})
                print(f"\n❌ [LOSS] {symbol} | Perte : -${loss:.2f}")
                positions_to_remove.append(pos)
                
            elif elapsed_minutes >= HORIZON:
                pnl_pct = (current_price - pos['entry']) / pos['entry']
                pnl_usd = TRADE_SIZE * pnl_pct
                capital += pnl_usd
                res = 'WIN' if pnl_usd >= 0 else 'LOSS'
                trade_history.append({'symbol': symbol, 'result': f'TIMEOUT_{res}', 'pnl': pnl_usd})
                print(f"\n⏰ [TIMEOUT] {symbol} | Sortie à {elapsed_minutes:.0f}m | PnL : ${pnl_usd:.2f}")
                positions_to_remove.append(pos)

        except Exception:
            pass
            
    for pos in positions_to_remove:
        open_positions.remove(pos)

def scan_and_trade():
    global open_positions
    print(f"\n🔍 Scan 1M - {datetime.now().strftime('%H:%M:%S')}")
    active_symbols = [p['symbol'] for p in open_positions]
    
    for symbol in TOKENS:
        if symbol in active_symbols:
            continue
            
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=SEQ_LEN)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            if len(df) < SEQ_LEN:
                continue
                
            df_feat, feature_cols = compute_features(df)
            latest_features = df_feat.iloc[-1][feature_cols].values.reshape(1, -1)
            
            if np.isnan(latest_features).any():
                continue
                
            prob = model.predict_proba(latest_features)[0][1] # Probabilité de succès
            current_price = df['close'].iloc[-1]
            
            print(f"    👉 {symbol:<10} | Confiance IA : {prob*100:.1f}%")
            
            if prob >= THRESHOLD:
                open_positions.append({
                    'symbol': symbol,
                    'entry': current_price,
                    'tp': current_price * (1 + TP_PCT),
                    'sl': current_price * (1 - SL_PCT),
                    'entry_time': datetime.now()
                })
                print(f"🚀 [NOUVEAU TRADE] {symbol} | Entrée: {current_price} | Confiance: {prob*100:.1f}%")
        except Exception:
            pass

print("\n🤖 BOT LIGHTGBM OPTIMISÉ ACTIF")
try:
    while True:
        check_open_positions()
        scan_and_trade()
        
        wins = sum(1 for t in trade_history if 'WIN' in t['result'])
        losses = sum(1 for t in trade_history if 'LOSS' in t['result'])
        win_rate = (wins / len(trade_history) * 100) if len(trade_history) > 0 else 0
        
        print(f"\n📊 --- STATISTIQUES GLOBALES ---")
        print(f"Capital: ${capital:.2f} | Positions: {len(open_positions)} | Trades: {len(trade_history)} | Win Rate: {win_rate:.1f}%")
        
        print(f"📌 --- STATISTIQUES PAR TOKEN ---")
        for token in TOKENS:
            token_trades = [t for t in trade_history if t['symbol'] == token]
            t_count = len(token_trades)
            t_wins = sum(1 for t in token_trades if 'WIN' in t['result'])
            t_pnl = sum(t['pnl'] for t in token_trades)
            t_wr = (t_wins / t_count * 100) if t_count > 0 else 0.0
            print(f"   • {token:<12} | Trades: {t_count:<3} | Win Rate: {t_wr:>5.1f}% | PnL: ${t_pnl:>+6.2f}")
            
        print("-" * 55)
        time.sleep(60)
except KeyboardInterrupt:
    print("\n🛑 Bot arrêté proprement.")
