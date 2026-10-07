import ccxt
import numpy as np
import pandas as pd
import time
from datetime import datetime

# ==========================================
# 1. CONFIGURATION STRATÉGIQUE (1H)
# ==========================================
TIMEFRAME = '1h'
DONCHIAN_WINDOW = 20    # Canaux de Donchian (Plus haut/bas de 20h)
EMA_FILTER_PERIOD = 200 # Filtre de tendance globale
ATR_PERIOD = 14         # Période de mesure de la volatilité
ATR_MULTIPLIER = 2.5    # Distance du Trailing Stop (2.5x ATR)
CANDLES_TO_FETCH = 300  # Historique nécessaire (~12 jours)

INITIAL_CAPITAL = 1000
TRADE_SIZE = 150        # Taille de position par trade ($)

TOKENS = [
   'RLC/USDT', 'DMC/USDT', 'MOVR/USDT', 'QUBIC/USDT', 'AIN/USDT', 'KAIO/USDT'
]

exchange = ccxt.bitget({'enableRateLimit': True})

# ==========================================
# 2. CALCUL DES INDICATEURS QUANTITATIFS
# ==========================================
def compute_indicators(df):
    """Calcule la Tendance (EMA), le Breakout (Donchian) et la Volatilité (ATR)."""
    df = df.copy()
    close = df['close']
    high = df['high']
    low = df['low']
    
    # 1. Tendance Long-Terme (EMA 200)
    df['ema_200'] = close.ewm(span=EMA_FILTER_PERIOD, adjust=False).mean()
    
    # 2. Canaux de Donchian (Plus haut/bas sur 20 bougies)
    df['donchian_high'] = high.shift(1).rolling(window=DONCHIAN_WINDOW).max()
    df['donchian_low'] = low.shift(1).rolling(window=DONCHIAN_WINDOW).min()
    
    # 3. Average True Range (ATR 14) - Volatilité
    tr = np.maximum(
        high - low,
        np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1)))
    )
    df['atr'] = tr.rolling(window=ATR_PERIOD).mean()
    
    # 4. Filtre de Volume (Supérieur à la moyenne 20h)
    df['vol_ma'] = df['volume'].rolling(window=20).mean()
    
    return df

def fetch_ohlcv(symbol, timeframe, limit):
    """Récupère les données OHLCV depuis l'exchange."""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        print(f"⚠️ Erreur de téléchargement pour {symbol} : {e}")
        return None

# ==========================================
# 3. MOTEUR DE PAPER TRADING & TRAILING STOP
# ==========================================
capital = INITIAL_CAPITAL
open_positions = []
trade_history = []

def manage_positions():
    """Gère le Trailing Stop ATR dynamique et la clôture des positions."""
    global capital, open_positions, trade_history
    positions_to_remove = []
    
    for pos in open_positions:
        symbol = pos['symbol']
        try:
            ticker = exchange.fetch_ticker(symbol)
            current_price = ticker['last']
            
            # Mise à jour des indicateurs pour ajuster le Trailing Stop
            df = fetch_ohlcv(symbol, TIMEFRAME, limit=30)
            if df is None or len(df) < ATR_PERIOD:
                continue
            
            df = compute_indicators(df)
            current_atr = df['atr'].iloc[-1]
            
            # 1. Calcul du nouveau Stop Loss Théorique (Trailing Stop)
            new_sl = current_price - (current_atr * ATR_MULTIPLIER)
            
            # Le Stop Loss ne peut que MONTER, jamais descendre
            if new_sl > pos['sl']:
                pos['sl'] = new_sl
            
            # 2. Prise de Profit / Sortie sur Touche du Trailing Stop
            if current_price <= pos['sl']:
                pnl_pct = (pos['sl'] - pos['entry']) / pos['entry']
                pnl_usd = pos['size'] * pnl_pct
                capital += pnl_usd
                
                res = 'WIN' if pnl_usd >= 0 else 'LOSS'
                trade_history.append({'symbol': symbol, 'result': res, 'pnl': pnl_usd})
                
                print(f"\n🚨 [SORTIE {res}] {symbol} | Prix: ${current_price:.2f} | SL: ${pos['sl']:.2f} | PnL: ${pnl_usd:+.2f}")
                positions_to_remove.append(pos)
                
        except Exception as e:
            print(f"⚠️ Erreur lors du suivi de la position {symbol}: {e}")
            
    for pos in positions_to_remove:
        open_positions.remove(pos)

def scan_and_enter():
    """Détecte les opportunités de Breakout 1H."""
    global open_positions
    print(f"\n🔍 Scan 1H - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    active_symbols = [p['symbol'] for p in open_positions]
    
    for symbol in TOKENS:
        if symbol in active_symbols:
            continue
            
        df = fetch_ohlcv(symbol, TIMEFRAME, CANDLES_TO_FETCH)
        if df is None or len(df) < EMA_FILTER_PERIOD:
            continue
            
        df = compute_indicators(df)
        last_row = df.iloc[-1]
        
        close = last_row['close']
        ema_200 = last_row['ema_200']
        donchian_high = last_row['donchian_high']
        atr = last_row['atr']
        volume = last_row['volume']
        vol_ma = last_row['vol_ma']
        
        # --- CONDITIONS D'ENTRÉE ---
        # 1. Tendance Haussière (Prix > EMA 200)
        # 2. Cassure du plus haut de 20h (Donchian Breakout)
        # 3. Confirmation par le volume (> Moyenne 20h)
        is_uptrend = close > ema_200
        is_breakout = close > donchian_high
        is_volume_confirmed = volume > vol_ma
        
        print(f"   👉 {symbol:<10} | Prix: ${close:<8.2f} | EMA200: ${ema_200:<8.2f} | Donchian: ${donchian_high:<8.2f}")
        
        if is_uptrend and is_breakout and is_volume_confirmed:
            initial_sl = close - (atr * ATR_MULTIPLIER)
            open_positions.append({
                'symbol': symbol,
                'entry': close,
                'sl': initial_sl,
                'size': TRADE_SIZE,
                'entry_time': datetime.now()
            })
            print(f"🚀 [SIGNAL BREAKOUT 1H] Achat sur {symbol} | Entrée: ${close:.2f} | Stop Loss Initial: ${initial_sl:.2f}")

# ==========================================
# 4. BOUCLE D'EXÉCUTION DU BOT
# ==========================================
print("\n🤖 BOT BREAKOUT 1H ACTIF (Tendance + ATR Trailing Stop)")
try:
    while True:
        manage_positions()
        scan_and_enter()
        
        wins = sum(1 for t in trade_history if t['result'] == 'WIN')
        losses = sum(1 for t in trade_history if t['result'] == 'LOSS')
        win_rate = (wins / len(trade_history) * 100) if len(trade_history) > 0 else 0.0
        
        print(f"\n📊 --- STATISTIQUES GLOBAL ---")
        print(f"Capital: ${capital:.2f} | Positions Ouvertes: {len(open_positions)} | Total Trades: {len(trade_history)} | Win Rate: {win_rate:.1f}%")
        print("-" * 65)
        
        # Exécution toutes les 5 minutes pour surveiller le Trailing Stop
        time.sleep(300)
        
except KeyboardInterrupt:
    print("\n🛑 Bot arrêté proprement.")
