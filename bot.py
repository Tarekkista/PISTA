import ccxt
import numpy as np
import pandas as pd
import time
from datetime import datetime

# ==========================================
# 1. CONFIGURATION OPTIMISÉE POUR PETITE RAM
# ==========================================
TIMEFRAME = '1m'
SEQ_LEN = 60           # 60 minutes de contexte
HORIZON = 45           # Horizon max de trade = 10 minutes
TP_PCT = 0.009        # Target +0.65%
SL_PCT = 0.003       # Stop Loss -0.25%
CANDLES_TO_FETCH = 1500 # Réduit à 500 bougies (~8h) pour ne pas saturer la RAM

INITIAL_CAPITAL = 1000
TRADE_SIZE = 100
THRESHOLD = 0.3       # Seuil d'achat IA (55%)

TOKENS = [
   'PUMPBTC/USDT', 'KAIO/USDT', 'MOVR/USDT', 'QUBIC/USDT', 'AIN/USDT', 'SOON/USDT'
]

exchange = ccxt.bitget({'enableRateLimit': True})

# ==========================================
# 2. MODÈLE IA PURE NUMPY (ULTRA-LIGHT)
# ==========================================
class LightweightTreeEnsemble:
    def __init__(self, n_trees=15, max_depth=3):
        self.n_trees = n_trees
        self.max_depth = max_depth
        self.trees = []

    def _build_tree(self, X, y, depth):
        if depth >= self.max_depth or len(y) < 5 or len(np.unique(y)) == 1:
            return np.mean(y) if len(y) > 0 else 0.5

        n_samples, n_features = X.shape
        feature_indices = np.random.choice(n_features, int(np.sqrt(n_features)), replace=False)
        
        best_gain = -1
        best_split = None
        current_uncertainty = np.var(y)

        for feat in feature_indices:
            thresholds = np.percentile(X[:, feat], [25, 50, 75])
            for thresh in thresholds:
                left_mask = X[:, feat] <= thresh
                right_mask = ~left_mask
                if np.sum(left_mask) < 2 or np.sum(right_mask) < 2:
                    continue

                gain = current_uncertainty - (
                    (np.sum(left_mask) / n_samples) * np.var(y[left_mask]) +
                    (np.sum(right_mask) / n_samples) * np.var(y[right_mask])
                )

                if gain > best_gain:
                    best_gain = gain
                    best_split = (feat, thresh, left_mask, right_mask)

        if best_gain <= 0 or best_split is None:
            return np.mean(y)

        feat, thresh, left_mask, right_mask = best_split
        return {
            'feature': feat,
            'threshold': thresh,
            'left': self._build_tree(X[left_mask], y[left_mask], depth + 1),
            'right': self._build_tree(X[right_mask], y[right_mask], depth + 1)
        }

    def fit(self, X, y):
        self.trees = []
        n_samples = len(X)
        for _ in range(self.n_trees):
            indices = np.random.choice(n_samples, int(n_samples * 0.8), replace=True)
            tree = self._build_tree(X[indices], y[indices], depth=0)
            self.trees.append(tree)

    def _predict_tree(self, tree, x):
        if not isinstance(tree, dict):
            return tree
        if x[tree['feature']] <= tree['threshold']:
            return self._predict_tree(tree['left'], x)
        return self._predict_tree(tree['right'], x)

    def predict_proba(self, X):
        probs = []
        for x in X:
            tree_preds = [self._predict_tree(t, x) for t in self.trees]
            probs.append(np.mean(tree_preds))
        return np.array(probs)

# ==========================================
# 3. EXTRACTION DE FEATURES ET PRÉPARATIONS
# ==========================================
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

def extract_features(window_df):
    base_price = window_df['close'].iloc[0]
    norm_prices = (window_df[['open', 'high', 'low', 'close']] / base_price - 1.0).values.flatten()
    vol = window_df['volume'].values
    vol_std = np.std(vol)
    norm_vol = (vol - np.mean(vol)) / (vol_std if vol_std > 1e-8 else 1e-8)
    return np.hstack([norm_prices, norm_vol])

# ==========================================
# 4. ENTRAÎNEMENT DU MODÈLE
# ==========================================
print("⚡ Téléchargement des 500 dernières bougies...")
raw_data = {}
for symbol in TOKENS:
    df = fetch_ohlcv_extended(symbol, TIMEFRAME, CANDLES_TO_FETCH)
    if len(df) >= SEQ_LEN + HORIZON:
        raw_data[symbol] = df
        print(f"   ✅ {symbol:<10} : {len(df)} bougies.")

X, y = [], []
# Utilisation d'un pas (stride) de 2 pour limiter la consommation de RAM
for symbol, df in raw_data.items():
    n = len(df)
    for entry_idx in range(SEQ_LEN, n - HORIZON, 2):
        window = df.iloc[entry_idx - SEQ_LEN : entry_idx]
        features = extract_features(window)
        label = label_sequence(df, entry_idx, HORIZON, TP_PCT, SL_PCT)
        X.append(features)
        y.append(label)

X = np.array(X, dtype=np.float32)
y = np.array(y, dtype=np.float32)

print(f"\n📊 Total échantillons d'entraînement : {X.shape[0]}")
print(f"🎯 Ratio de succès historique : {np.mean(y)*100:.2f}%")

model = LightweightTreeEnsemble(n_trees=15, max_depth=3)
print("\n🏋️ Entraînement du modèle (Consommation RAM : ~15 MB)...")
model.fit(X, y)
print("✅ Modèle prêt !")

# ==========================================
# 5. MOTEUR DE PAPER TRADING
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
                
            current_price = df['close'].iloc[-1]
            features = extract_features(df).reshape(1, -1)
            
            prob = model.predict_proba(features)[0]
            print(f"   👉 {symbol:<10} | Confiance IA : {prob*100:.1f}%")
            
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

print("\n🤖 BOT OPTIMISÉ ACTIF")
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
            print(f"  • {token:<12} | Trades: {t_count:<3} | Win Rate: {t_wr:>5.1f}% | PnL: ${t_pnl:>+6.2f}")
            
        print("-" * 55)
        time.sleep(60)
except KeyboardInterrupt:
    print("\n🛑 Bot arrêté proprement.")
