import ccxt
import numpy as np
import pandas as pd
import time
import os
from datetime import datetime

# ==========================================
# 1. CONFIGURATION STRATÉGIQUE (1M)
# ==========================================
TIMEFRAME = '1m'
EMA_FILTER_PERIOD = 100 # Filtre de tendance sur 200 bougies
ATR_PERIOD = 14          # Période ATR
ATR_MULTIPLIER = 1.8    # Trailing Stop multiplier
CANDLES_TO_FETCH = 250  # Historique suffisant pour l'EMA 200

INITIAL_CAPITAL = 1000.0
TRADE_SIZE = 150.0

TOKENS = [
    'RLC/USDT', 'DMC/USDT', 'MOVR/USDT', 'QUBIC/USDT', 'AIN/USDT', 'KAIO/USDT'
]

CSV_FILENAME = 'trades_log.csv'

# Initialisation du fichier CSV avec en-têtes s'il n'existe pas encore
if not os.path.exists(CSV_FILENAME):
    df_init = pd.DataFrame(columns=[
        'timestamp', 'bot_name', 'symbol', 'entry_price', 
        'exit_price', 'pnl_usd', 'pnl_pct', 'result', 'capital_after'
    ])
    df_init.to_csv(CSV_FILENAME, index=False)

exchange = ccxt.bitget({'enableRateLimit': True})

# ==========================================
# 2. STRUCTURES DE PAPER TRADING SÉPARÉES
# ==========================================
bot_30m = {
    'name': 'DONCHIAN 30M',
    'window': 30,
    'capital': INITIAL_CAPITAL,
    'positions': [],
    'history': []
}

bot_60m = {
    'name': 'DONCHIAN 60M',
    'window': 60,
    'capital': INITIAL_CAPITAL,
    'positions': [],
    'history': []
}

BOTS = [bot_30m, bot_60m]

# ==========================================
# 3. CALCUL DES INDICATEURS QUANTITATIFS
# ==========================================
def compute_indicators(df, window):
    """Calcule l'EMA 200, l'ATR et le Donchian/Volume selon la fenêtre spécifiée."""
    df = df.copy()
    close = df['close']
    high = df['high']
    low = df['low']
    
    df['ema_200'] = close.ewm(span=EMA_FILTER_PERIOD, adjust=False).mean()
    df['donchian_high'] = high.shift(1).rolling(window=window).max()
    df['donchian_low'] = low.shift(1).rolling(window=window).min()
    
    tr = np.maximum(
        high - low,
        np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1)))
    )
    df['atr'] = tr.rolling(window=ATR_PERIOD).mean()
    df['vol_ma'] = df['volume'].rolling(window=window).mean()
    
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
# 4. MOTEUR DE SUIVI ET D'ENTRÉE PAR BOT
# ==========================================
def manage_positions(bot):
    """Gère le Trailing Stop ATR et la clôture des positions."""
    positions_to_remove = []
    
    for pos in bot['positions']:
        symbol = pos['symbol']
        try:
            ticker = exchange.fetch_ticker(symbol)
            current_price = ticker['last']
            
            df = fetch_ohlcv(symbol, TIMEFRAME, limit=CANDLES_TO_FETCH)
            if df is None or len(df) < ATR_PERIOD:
                continue
            
            df = compute_indicators(df, bot['window'])
            current_atr = df['atr'].iloc[-1]
            
            new_sl = current_price - (current_atr * ATR_MULTIPLIER)
            if new_sl > pos['sl']:
                pos['sl'] = new_sl
            
            if current_price <= pos['sl']:
                pnl_pct = (pos['sl'] - pos['entry']) / pos['entry']
                pnl_usd = pos['size'] * pnl_pct
                bot['capital'] += pnl_usd
                
                res = 'WIN' if pnl_usd >= 0 else 'LOSS'
                bot['history'].append({'symbol': symbol, 'result': res, 'pnl': pnl_usd})
                
                # --- ENREGISTREMENT DU TRADE DANS LE FICHIER CSV ---
                trade_data = {
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'bot_name': bot['name'],
                    'symbol': symbol,
                    'entry_price': pos['entry'],
                    'exit_price': pos['sl'],
                    'pnl_usd': round(pnl_usd, 4),
                    'pnl_pct': round(pnl_pct * 100, 2),
                    'result': res,
                    'capital_after': round(bot['capital'], 2)
                }
                pd.DataFrame([trade_data]).to_csv(CSV_FILENAME, mode='a', header=False, index=False)
                
                print(f"\n🚨 [{bot['name']}] SORTIE {res} | {symbol} | Prix: {current_price:.10f} | PnL: ${pnl_usd:+.2f} (Enregistré dans {CSV_FILENAME})")
                positions_to_remove.append(pos)
                
        except Exception as e:
            print(f"⚠️ Erreur suivi [{bot['name']}] {symbol}: {e}")
            
    for pos in positions_to_remove:
        bot['positions'].remove(pos)

def scan_and_enter(bot):
    """Détecte les opportunités selon la fenêtre Donchian propre au bot avec affichage détaillé."""
    active_symbols = [p['symbol'] for p in bot['positions']]
    
    print(f"\n--- Scan [{bot['name']}] ---")
    for symbol in TOKENS:
        if symbol in active_symbols:
            continue
            
        df = fetch_ohlcv(symbol, TIMEFRAME, CANDLES_TO_FETCH)
        if df is None or len(df) < EMA_FILTER_PERIOD:
            continue
            
        df = compute_indicators(df, bot['window'])
        last_row = df.iloc[-1]
        
        close = last_row['close']
        ema_200 = last_row['ema_200']
        donchian_high = last_row['donchian_high']
        atr = last_row['atr']
        volume = last_row['volume']
        vol_ma = last_row['vol_ma']
        
        # --- DÉCIMALES AJUSTÉES À 10 ---
        print(f"    👉 {symbol:<10} | Prix: {close:<14.10f} | EMA200: {ema_200:<14.10f} | Donchian {bot['window']}m: {donchian_high:<14.10f}")
        
        is_uptrend = close > ema_200
        is_breakout = close > donchian_high
        is_volume_confirmed = volume > vol_ma
        
        if is_uptrend and is_breakout and is_volume_confirmed:
            initial_sl = close - (atr * ATR_MULTIPLIER)
            bot['positions'].append({
                'symbol': symbol,
                'entry': close,
                'sl': initial_sl,
                'size': TRADE_SIZE,
                'entry_time': datetime.now()
            })
            print(f"🚀 [{bot['name']}] ACHAT {symbol} | Entrée: {close:.10f} | SL Initial: {initial_sl:.10f}")

# ==========================================
# 5. BOUCLE D'EXÉCUTION ET BENCHMARK
# ==========================================
print(f"\n🤖 BOT COMPARATIF ACTIF (DONCHIAN 30M vs DONCHIAN 60M)")
print(f"📁 Fichier d'enregistrement des trades : {CSV_FILENAME}")
try:
    while True:
        timestamp_str = datetime.now().strftime('%H:%M:%S')
        print(f"\n🔍 ==================== SCAN 1M ({timestamp_str}) ====================")
        
        for bot in BOTS:
            manage_positions(bot)
            scan_and_enter(bot)
        
        print(f"\n📊 {'='*20} COMPARATIF EN DIRECT {'='*20}")
        print(f"{'BOT':<15} | {'CAPITAL':<10} | {'POS. OUVERTES':<15} | {'TRADES':<8} | {'WIN RATE':<10}")
        print("-" * 68)
        
        for bot in BOTS:
            wins = sum(1 for t in bot['history'] if t['result'] == 'WIN')
            total = len(bot['history'])
            wr = (wins / total * 100) if total > 0 else 0.0
            print(f"{bot['name']:<15} | ${bot['capital']:<9.2f} | {len(bot['positions']):<15} | {total:<8} | {wr:.1f}%")
        print("=" * 68)
        
        time.sleep(15)
        
except KeyboardInterrupt:
    print("\n🛑 Bot comparatif arrêté proprement.")
