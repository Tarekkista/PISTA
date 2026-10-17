import ccxt
import numpy as np
import pandas as pd
import time
import requests
import os
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
SL_PERCENT = 5.0           # Stop Loss fixe en % pour le bot sans trailing
CANDLES_TO_FETCH = 250     # Historique suffisant pour les MAs

INITIAL_CAPITAL = 1000.0
TRADE_SIZE = 100.0

# --- SIMULATION RÉALISTE VIA CARNET D'ORDRES (API publique Bitget, sans clé) ---
FEE_RATE = 0.001           # Frais par côté (0,1 %) : À VÉRIFIER sur ton compte Bitget
ORDERBOOK_DEPTH = 50       # Nombre de niveaux lus dans le carnet
MAX_SLIPPAGE_PCT = 0.5     # Entrée rejetée si le prix moyen obtenu dépasse le prix du signal de plus de X %
MAX_SPREAD_PCT = 1.0       # Entrée rejetée si le spread (ask - bid) dépasse X %

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
# 4b. SIMULATION RÉALISTE DES FILLS (CARNET D'ORDRES)
# ==========================================
def fetch_orderbook(symbol):
    """Lit le carnet d'ordres public (sans clé API). Retourne None en cas d'erreur."""
    try:
        return exchange.fetch_order_book(symbol, limit=ORDERBOOK_DEPTH)
    except Exception as e:
        print(f"⚠️ Erreur carnet d'ordres pour {symbol} : {e}")
        return None

def simulate_buy(book, usd_amount):
    """Achat au marché de usd_amount en parcourant les asks.
    Retourne (prix_moyen, quantité, usd_dépensé, rempli_complètement)."""
    remaining = usd_amount
    qty = 0.0
    spent = 0.0
    for level in book['asks']:
        price, amount = level[0], level[1]
        take_usd = min(remaining, price * amount)
        qty += take_usd / price
        spent += take_usd
        remaining -= take_usd
        if remaining <= 1e-9:
            break
    complete = remaining <= 1e-9
    avg = (spent / qty) if qty > 0 else None
    return avg, qty, spent, complete

def simulate_sell(book, qty):
    """Vente au marché de qty en parcourant les bids.
    Retourne (prix_moyen, produit_usd, rempli_complètement).
    Si le carnet est trop mince, le reste est valorisé au dernier niveau lu."""
    remaining = qty
    proceeds = 0.0
    last_price = None
    for level in book['bids']:
        price, amount = level[0], level[1]
        take = min(remaining, amount)
        proceeds += take * price
        remaining -= take
        last_price = price
        if remaining <= 1e-12:
            break
    complete = remaining <= 1e-12
    if not complete and last_price is not None:
        proceeds += remaining * last_price
    avg = (proceeds / qty) if qty > 0 else None
    return avg, proceeds, complete

def realistic_entry(symbol, signal_price):
    """Simule l'achat réaliste au moment du signal.
    Retourne un dict de champs à ajouter à la position."""
    book = fetch_orderbook(symbol)
    if not book or not book.get('asks') or not book.get('bids'):
        print(f"   ↳ [CARNET] {symbol} | ❌ REJETÉ (carnet indisponible)")
        return {'rejected': True, 'note': 'carnet indisponible'}

    best_bid = book['bids'][0][0]
    best_ask = book['asks'][0][0]
    mid = (best_bid + best_ask) / 2.0
    spread_pct = (best_ask - best_bid) / mid * 100.0

    avg, qty, spent, complete = simulate_buy(book, TRADE_SIZE)
    if avg is None:
        print(f"   ↳ [CARNET] {symbol} | ❌ REJETÉ (aucun fill possible)")
        return {'rejected': True, 'note': 'aucun fill possible', 'entry_spread_pct': round(spread_pct, 3)}

    # Positif = pire que le prix du signal
    slippage_pct = (avg - signal_price) / signal_price * 100.0

    fields = {
        'rejected': False,
        'note': '',
        'entry_real': avg,
        'qty': qty,
        'entry_fee': spent * FEE_RATE,
        'entry_cost': spent + spent * FEE_RATE,
        'entry_spread_pct': round(spread_pct, 3),
        'entry_slippage_pct': round(slippage_pct, 3)
    }

    reasons = []
    if not complete:
        reasons.append('carnet trop mince')
    if spread_pct > MAX_SPREAD_PCT:
        reasons.append(f'spread {spread_pct:.2f}%')
    if slippage_pct > MAX_SLIPPAGE_PCT:
        reasons.append(f'slippage {slippage_pct:.2f}%')

    if reasons:
        fields['rejected'] = True
        fields['note'] = ' / '.join(reasons)
        print(f"   ↳ [CARNET] {symbol} | ❌ REJETÉ ({fields['note']})")
    else:
        print(f"   ↳ [CARNET] {symbol} | spread: {spread_pct:.3f}% | slippage: {slippage_pct:+.3f}% | prix réel: {avg:.10f}")

    return fields

def realistic_exit(pos, theoretical_exit_price):
    """Simule la vente réaliste au moment de la sortie.
    Retourne un dict de colonnes à ajouter à l'enregistrement du trade."""
    result = {
        'pnl_usd_real': None,
        'entry_price_real': pos.get('entry_real'),
        'exit_price_real': None,
        'entry_spread_pct': pos.get('entry_spread_pct'),
        'entry_slippage_pct': pos.get('entry_slippage_pct'),
        'exit_slippage_pct': None,
        'fees_usd': None,
        'rejected': pos.get('rejected', False),
        'note': pos.get('note', '')
    }

    if pos.get('rejected'):
        return result

    book = fetch_orderbook(pos['symbol'])
    if not book or not book.get('bids'):
        result['note'] = 'carnet indisponible à la sortie'
        return result

    avg, proceeds, complete = simulate_sell(book, pos['qty'])
    if avg is None:
        result['note'] = 'aucun fill possible à la sortie'
        return result

    exit_fee = proceeds * FEE_RATE
    pnl_real = (proceeds - exit_fee) - pos['entry_cost']

    result['pnl_usd_real'] = round(pnl_real, 2)
    result['exit_price_real'] = avg
    # Positif = pire que le prix théorique de sortie
    result['exit_slippage_pct'] = round((theoretical_exit_price - avg) / theoretical_exit_price * 100.0, 3)
    result['fees_usd'] = round(pos['entry_fee'] + exit_fee, 4)
    if not complete:
        result['note'] = 'sortie partielle (carnet trop mince)'

    return result

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
                # CAS 2 : SANS TRAILING (Stop Loss fixe en % + Signal Inverse)
                if current_price <= pos['sl']:
                    exit_triggered = True
                    exit_reason = "Stop Loss (%)"
                    exit_price = pos['sl']
                elif crossunder:
                    exit_triggered = True
                    exit_reason = "Signal Inverse"
                    exit_price = current_price

            if exit_triggered:
                pnl_pct = (exit_price - pos['entry']) / pos['entry']
                pnl_usd = pos['size'] * pnl_pct
                bot['capital'] += pnl_usd

                res = 'WIN' if pnl_usd >= 0 else 'LOSS'
                
                exit_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                entry_time_str = pos['entry_time'].strftime('%Y-%m-%d %H:%M:%S')

                trade_record = {
                    'symbol': symbol,
                    'entry_time': entry_time_str,
                    'exit_time': exit_time_str,
                    'entry_price': pos['entry'],
                    'exit_price': exit_price,
                    'result': res,
                    'pnl_usd': round(pnl_usd, 2),
                    'exit_reason': exit_reason
                }

                # Colonnes réalistes ajoutées après les colonnes d'origine
                real = realistic_exit(pos, exit_price)
                trade_record.update(real)
                
                bot['history'].append(trade_record)

                # --- SAUVEGARDE DANS LE FICHIER CSV CORRESPONDANT ---
                filename = "trades_avec_trailing_realiste.csv" if use_trailing else "trades_sans_trailing_realiste.csv"
                df_trade = pd.DataFrame([trade_record])
                file_exists = os.path.exists(filename)
                df_trade.to_csv(filename, mode='a', header=not file_exists, index=False)

                print(f"\n🚨 [{bot['name']}] SORTIE {res} ({exit_reason}) | {symbol} | Prix: {exit_price:.10f} | PnL: ${pnl_usd:+.2f}")
                if real['pnl_usd_real'] is not None:
                    print(f"   ↳ [RÉALISTE] PnL: ${real['pnl_usd_real']:+.2f} | slippage sortie: {real['exit_slippage_pct']:+.3f}% | frais: ${real['fees_usd']:.2f}")
                else:
                    print(f"   ↳ [RÉALISTE] non calculé ({real['note']})")
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
            initial_sl_trailing = close - (atr * ATR_MULTIPLIER)
            initial_sl_percent = close * (1.0 - SL_PERCENT / 100.0)
            
            active_symbols_trailing = [p['symbol'] for p in bot_with_trailing['positions']]
            active_symbols_without = [p['symbol'] for p in bot_without_trailing['positions']]

            # Fill réaliste calculé une seule fois (même moment, même carnet pour les deux bots)
            real_fields = {}
            if symbol not in active_symbols_trailing or symbol not in active_symbols_without:
                real_fields = realistic_entry(symbol, close)

            # Bot avec trailing
            if symbol not in active_symbols_trailing:
                bot_with_trailing['positions'].append({
                    'symbol': symbol,
                    'entry': close,
                    'sl': initial_sl_trailing,
                    'size': TRADE_SIZE,
                    'entry_time': datetime.now(),
                    **real_fields
                })
                print(f"🚀 [{bot_with_trailing['name']}] ACHAT {symbol} | Entrée: {close:.10f} | SL Initial: {initial_sl_trailing:.10f}")

            # Bot sans trailing (avec Stop Loss fixe en %)
            if symbol not in active_symbols_without:
                bot_without_trailing['positions'].append({
                    'symbol': symbol,
                    'entry': close,
                    'sl': initial_sl_percent,
                    'size': TRADE_SIZE,
                    'entry_time': datetime.now(),
                    **real_fields
                })
                print(f"🚀 [{bot_without_trailing['name']}] ACHAT {symbol} | Entrée: {close:.10f} | SL Fixe ({SL_PERCENT}%): {initial_sl_percent:.10f}")

# ==========================================
# 6. BOUCLE D'EXÉCUTION ET TABLEAU COMPARATIF
# ==========================================
print("\n🤖 BOT COMPARATIF ACTIF : AVEC vs SANS TRAILING (AVEC SL %)")
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
