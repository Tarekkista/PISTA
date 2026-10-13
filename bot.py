import ccxt
import numpy as np
import pandas as pd
import time
import requests
from datetime import datetime

# ==========================================
# 1. CONFIGURATION STRATÉGIQUE (3M)
# ==========================================
TIMEFRAME = '1m'
FAST_LEN = 500             # MA Rapide (500)
SLOW_LEN = 3               # MA Lente (3)
MA_TYPE = 'EMA'            # Type de moyenne mobile : 'EMA' ou 'SMA'
ATR_PERIOD = 14            # Période ATR
ATR_MULTIPLIER = 1.8       # Trailing Stop ATR multiplier
CANDLES_TO_FETCH = 250     # Historique suffisant pour les MAs

INITIAL_CAPITAL = 1000.0
TRADE_SIZE = 100.0

# Fréquence de mise à jour du Screener TradingView (toutes les 5 minutes = 300 secondes)
SCREENER_INTERVAL_SEC = 300
last_screener_time = 0

# Liste dynamique initialisée à vide, mise à jour au démarrage
TOKENS = []

exchange = ccxt.bitget({'enableRateLimit': True})

# ==========================================
# 2. STRUCTURES DE DOUBLE SUIVI (AVEC vs SANS TRAILING)
# ==========================================
bot_with_trailing = {
    'name': 'MA 500/3 [AVEC TRAILING]',
    'capital': INITIAL_CAPITAL,
    'positions': [],
    'history': []
}

bot_without_trailing = {
    'name': 'MA 500/3 [SANS TRAILING]',
    'capital': INITIAL_CAPITAL,
    'positions': [],
    'history': []
}

BOTS = [bot_with_trailing, bot_without_trailing]

# ==========================================
# 3. SCREENER TRADINGVIEW (VOLATILITÉ W UNIQUEMENT)
# ==========================================
def update_dynamic_tokens():
    """Interroge l'API TradingView : Volatilité W > 25 uniquement."""
    global TOKENS
    print(f"\n🔍 [SCREENER TV] Interrogation (Vol W > 25 uniquement)...")

    url = "https://scanner.tradingview.com/crypto/scan"
    payload = {
        "filter": [
            {"left": "exchange", "operation": "equal", "right": "BITGET"},
            {"left": "Volatility.W", "operation": "egreater", "right": 25}
        ],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name", "close", "change", "Volatility.W"],
        "sort": {"sortBy": "Volatility.W", "sortOrder": "desc"},
        "range": [0, 500]
    }

    fetched_tokens = []
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"⚠️ Screener TV HTTP {response.status_code} : {response.text[:200]}")
        data = response.json()
        results = data.get("data", [])

        for item in results:
            symbol_raw = item.get("s", "")  # Ex: "BITGET:STRKUSDT"
            if ":" in symbol_raw:
                ticker = symbol_raw.split(":")[1]
                if ticker.endswith("USDT") and not any(sub in ticker for sub in ["PERP", "QUARTER", "_", "-"]):
                    formatted_symbol = ticker[:-4] + "/USDT"
                    if formatted_symbol not in fetched_tokens:
                        fetched_tokens.append(formatted_symbol)

    except Exception as e:
        print(f"⚠️ Erreur lors de la requête du screener TradingView : {e}")

    if fetched_tokens:
        TOKENS = fetched_tokens
    elif not TOKENS:
        TOKENS = ['STRK/USDT', 'KAIO/USDT']

    print(f"✅ [SCREENER TV] Tokens validés ({len(TOKENS)}) : {TOKENS}")

# ==========================================
# 4. CALCUL DES INDICATEURS (MA & ATR)
# ==========================================
def calculate_ma(series, length, ma_type):
    if ma_type == 'EMA':
        return series.ewm(span=length, adjust=False).mean()
    else:
        return series.rolling(window=length).mean()

def compute_indicators(df):
    """Calcule les MAs et l'ATR."""
    df = df.copy()
    close = df['close']
    high = df['high']
    low = df['low']

    df['ma_fast'] = calculate_ma(close, FAST_LEN, MA_TYPE)
    df['ma_slow'] = calculate_ma(close, SLOW_LEN, MA_TYPE)

    tr = np.maximum(
        high - low,
        np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1)))
    )
    df['atr'] = tr.rolling(window=ATR_PERIOD).mean()

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
# 5. MOTEUR DE SUIVI ET D'ENTRÉE (AVEC ET SANS TRAILING)
# ==========================================
def manage_positions(bot, use_trailing):
    """Gère le suivi des positions selon si le Trailing est actif ou non."""
    positions_to_remove = []

    for pos in bot['positions']:
        symbol = pos['symbol']
        try:
            ticker = exchange.fetch_ticker(symbol)
            current_price = ticker['last']

            df = fetch_ohlcv(symbol, TIMEFRAME, limit=CANDLES_TO_FETCH)
            if df is None or len(df) < ATR_PERIOD:
                continue

            df = compute_indicators(df)
            current_atr = df['atr'].iloc[-1]
            last_row = df.iloc[-1]
            prev_row = df.iloc[-2]

            ma_fast = last_row['ma_fast']
            ma_slow = last_row['ma_slow']
            crossunder = (prev_row['ma_fast'] >= prev_row['ma_slow']) and (ma_fast < ma_slow)

            exit_triggered = False
            exit_reason = ""
            exit_price = 0.0

            if use_trailing:
                # CAS 1 : AVEC TRAILING STOP ATR
                new_sl = current_price - (current_atr * ATR_MULTIPLIER)
                if new_sl > pos['sl']:
                    pos['sl'] = new_sl

                if current_price <= pos['sl']:
                    exit_triggered = True
                    exit_reason = "Trailing SL"
                    exit_price = pos['sl']
                elif crossunder:
                    exit_triggered = True
                    exit_reason = "Signal Inverse"
                    exit_price = current_price
            else:
                # CAS 2 : SANS TRAILING (Uniquement sortie sur Signal Inverse par exemple)
                if crossunder:
                    exit_triggered = True
                    exit_reason = "Signal Inverse"
                    exit_price = current_price

            if exit_triggered:
                pnl_pct = (exit_price - pos['entry']) / pos['entry']
                pnl_usd = pos['size'] * pnl_pct
                bot['capital'] += pnl_usd

                res = 'WIN' if pnl_usd >= 0 else 'LOSS'
                bot['history'].append({'symbol': symbol, 'result': res, 'pnl': pnl_usd})

                print(f"\n🚨 [{bot['name']}] SORTIE {res} ({exit_reason}) | {symbol} | Prix: {exit_price:.10f} | PnL: ${pnl_usd:+.2f}")
                positions_to_remove.append(pos)

        except Exception as e:
            print(f"⚠️ Erreur suivi position {symbol} pour {bot['name']}: {e}")

    for pos in positions_to_remove:
        bot['positions'].remove(pos)

def scan_and_enter():
    """Détecte les opportunités d'achat et les applique aux deux bots simultanément."""
    for symbol in TOKENS:
        df = fetch_ohlcv(symbol, TIMEFRAME, CANDLES_TO_FETCH)
        if df is None or len(df) < SLOW_LEN:
            continue

        df = compute_indicators(df)
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]

        close = last_row['close']
        ma_fast = last_row['ma_fast']
        ma_slow = last_row['ma_slow']
        atr = last_row['atr']

        crossover = (prev_row['ma_fast'] <= prev_row['ma_slow']) and (ma_fast > ma_slow)

        if crossover:
            initial_sl = close - (atr * ATR_MULTIPLIER)
            
            # On vérifie pour chaque bot s'il n'a pas déjà une position sur ce symbole
            for bot in BOTS:
                active_symbols = [p['symbol'] for p in bot['positions']]
                if symbol not in active_symbols:
                    bot['positions'].append({
                        'symbol': symbol,
                        'entry': close,
                        'sl': initial_sl,
                        'size': TRADE_SIZE,
                        'entry_time': datetime.now()
                    })
                    print(f"🚀 [{bot['name']}] ACHAT {symbol} | Entrée: {close:.10f} | SL Initial: {initial_sl:.10f}")

# ==========================================
# 6. BOUCLE D'EXÉCUTION ET TABLEAU COMPARATIF
# ==========================================
print("\n🤖 BOT COMPARATIF ACTIF : AVEC vs SANS TRAILING ATR")
try:
    while True:
        now_ts = time.time()

        if last_screener_time == 0 or (now_ts - last_screener_time >= SCREENER_INTERVAL_SEC):
            update_dynamic_tokens()
            last_screener_time = now_ts

        timestamp_str = datetime.now().strftime('%H:%M:%S')
        print(f"\n🔍 ==================== SCAN 3M ({timestamp_str}) ====================")

        # Exécution du scan d'entrée commun
        scan_and_enter()

        # Gestion séparée des positions pour chaque cas
        manage_positions(bot_with_trailing, use_trailing=True)
        manage_positions(bot_without_trailing, use_trailing=False)

        # Affichage du tableau comparatif dans le terminal
        print(f"\n📊 {'='*15} TABLEAU COMPARATIF EN DIRECT {'='*15}")
        print(f"{'STRATÉGIE':<28} | {'CAPITAL':<10} | {'POS. OUVERTES':<13} | {'TRADES':<8} | {'WIN RATE':<10}")
        print("-" * 79)

        for bot in BOTS:
            wins = sum(1 for t in bot['history'] if t['result'] == 'WIN')
            total = len(bot['history'])
            wr = (wins / total * 100) if total > 0 else 0.0
            print(f"{bot['name']:<28} | ${bot['capital']:<9.2f} | {len(bot['positions']):<13} | {total:<8} | {wr:.1f}%")
        print("=" * 79)

        time.sleep(3)

except KeyboardInterrupt:
    print("\n🛑 Bot comparatif arrêté proprement.")
