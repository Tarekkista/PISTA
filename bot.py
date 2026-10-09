import ccxt
import numpy as np
import pandas as pd
import time
from datetime import datetime

# ==========================================
# 1. CONFIGURATION (1M)
# ==========================================
TIMEFRAME = '1m'
EMA_FILTER_PERIOD = 100   # Filtre de tendance (EMA 100)
ATR_PERIOD = 14
ATR_MULTIPLIER = 1.8      # Trailing stop
CANDLES_TO_FETCH = 250
SCAN_INTERVAL = 15        # secondes

INITIAL_CAPITAL = 1000.0
TRADE_SIZE = 150.0

TOKENS = [
    'RLC/USDT', 'DMC/USDT', 'MOVR/USDT', 'QUBIC/USDT', 'AIN/USDT', 'KAIO/USDT'
]

exchange = ccxt.bitget({'enableRateLimit': True})


def make_bot(name, window):
    return {
        'name': name,
        'window': window,
        'capital': INITIAL_CAPITAL,   # capital réalisé
        'positions': [],
        'history': [],
        'last_signal': {},            # symbol -> timestamp de la bougie fermée déjà tradée
    }


BOTS = [make_bot('DONCHIAN 30M', 30), make_bot('DONCHIAN 60M', 60)]


# ==========================================
# 2. DONNÉES ET INDICATEURS
# ==========================================
def compute_indicators(df, window):
    df = df.copy()
    close, high, low = df['close'], df['high'], df['low']

    df['ema_filter'] = close.ewm(span=EMA_FILTER_PERIOD, adjust=False).mean()
    df['donchian_high'] = high.shift(1).rolling(window=window).max()

    tr = np.maximum(
        high - low,
        np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1)))
    )
    df['atr'] = tr.rolling(window=ATR_PERIOD).mean()
    df['vol_ma'] = df['volume'].rolling(window=window).mean()
    return df


def fetch_all():
    """Un seul téléchargement par token et par cycle, partagé entre les bots."""
    data = {}
    for symbol in TOKENS:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLES_TO_FETCH)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            if len(df) > EMA_FILTER_PERIOD + 2:
                data[symbol] = df
        except Exception as e:
            print(f"⚠️ Erreur de téléchargement pour {symbol} : {e}")
    return data


# ==========================================
# 3. SORTIE ET SUIVI DES POSITIONS
# ==========================================
def close_position(bot, pos, exit_price):
    pnl_usd = pos['size'] * (exit_price - pos['entry']) / pos['entry']
    bot['capital'] += pnl_usd
    res = 'WIN' if pnl_usd > 0 else 'LOSS'
    bot['history'].append({'symbol': pos['symbol'], 'result': res, 'pnl': pnl_usd})
    print(f"\n🚨 [{bot['name']}] SORTIE {res} | {pos['symbol']} | Sortie: {exit_price:.10f} | PnL: ${pnl_usd:+.2f}")


def manage_positions(bot, data):
    """
    Le SL ne monte qu'à la clôture des bougies (close - ATR*mult de la bougie).
    Détection du stop : low des bougies fermées depuis le dernier passage,
    puis prix live + low de la bougie en cours.
    Prix de sortie : gap -> open, mèche -> SL, stop franchi en live -> prix courant.
    """
    to_remove = []

    for pos in bot['positions']:
        df = data.get(pos['symbol'])
        if df is None:
            continue

        df = compute_indicators(df, bot['window'])
        live = df.iloc[-1]
        closed = df.iloc[:-1]
        exit_price = None

        # 1. Bougies fermées non traitées
        for _, c in closed[closed['timestamp'] > pos['last_ts']].iterrows():
            # la bougie d'entrée n'est pas testée sur son low (mélange pré/post entrée)
            if c['timestamp'] != pos['entry_ts'] and c['low'] <= pos['sl']:
                exit_price = min(c['open'], pos['sl'])
                break
            if not np.isnan(c['atr']):
                pos['sl'] = max(pos['sl'], c['close'] - ATR_MULTIPLIER * c['atr'])
            pos['last_ts'] = c['timestamp']

        # 2. Bougie en cours (le SL est constant pendant la bougie)
        price = live['close']
        pos['last_price'] = price
        if exit_price is None:
            if price <= pos['sl']:
                exit_price = price
            elif live['timestamp'] != pos['entry_ts'] and live['low'] <= pos['sl']:
                exit_price = min(live['open'], pos['sl'])

        if exit_price is not None:
            close_position(bot, pos, exit_price)
            to_remove.append(pos)

    for pos in to_remove:
        bot['positions'].remove(pos)


# ==========================================
# 4. ENTRÉES (SIGNAUX SUR BOUGIE FERMÉE)
# ==========================================
def scan_and_enter(bot, data):
    active = {p['symbol'] for p in bot['positions']}

    print(f"\n--- Scan [{bot['name']}] ---")
    for symbol in TOKENS:
        if symbol in active or symbol not in data:
            continue

        df = compute_indicators(data[symbol], bot['window'])
        live = df.iloc[-1]   # bougie en cours (prix d'entrée)
        sig = df.iloc[-2]    # dernière bougie fermée (signal)

        needed = [sig['ema_filter'], sig['donchian_high'], sig['atr'], sig['vol_ma']]
        if any(pd.isna(v) for v in needed):
            continue

        print(f"    👉 {symbol:<10} | Prix: {live['close']:<14.10f} | EMA{EMA_FILTER_PERIOD}: {sig['ema_filter']:<14.10f} | Donchian {bot['window']}m: {sig['donchian_high']:<14.10f}")

        # un seul trade par bougie de signal
        if bot['last_signal'].get(symbol) == sig['timestamp']:
            continue

        is_uptrend = sig['close'] > sig['ema_filter']
        is_breakout = sig['close'] > sig['donchian_high']
        is_volume_confirmed = sig['volume'] > sig['vol_ma']

        if is_uptrend and is_breakout and is_volume_confirmed:
            entry = live['close']
            initial_sl = entry - sig['atr'] * ATR_MULTIPLIER
            bot['last_signal'][symbol] = sig['timestamp']
            bot['positions'].append({
                'symbol': symbol,
                'entry': entry,
                'sl': initial_sl,
                'size': TRADE_SIZE,
                'entry_time': datetime.now(),
                'entry_ts': live['timestamp'],
                'last_ts': live['timestamp'] - 1,
                'last_price': entry,
            })
            print(f"🚀 [{bot['name']}] ACHAT {symbol} | Entrée: {entry:.10f} | SL Initial: {initial_sl:.10f}")


# ==========================================
# 5. BOUCLE PRINCIPALE
# ==========================================
def unrealized(bot):
    return sum(p['size'] * (p['last_price'] - p['entry']) / p['entry'] for p in bot['positions'])


print("\n🤖 BOT COMPARATIF ACTIF (DONCHIAN 30M vs DONCHIAN 60M)")
try:
    while True:
        print(f"\n🔍 ==================== SCAN 1M ({datetime.now().strftime('%H:%M:%S')}) ====================")
        data = fetch_all()

        for bot in BOTS:
            manage_positions(bot, data)
            scan_and_enter(bot, data)

        print(f"\n📊 {'='*20} COMPARATIF EN DIRECT {'='*20}")
        print(f"{'BOT':<15} | {'CAPITAL':<10} | {'EQUITY':<10} | {'POS.':<5} | {'TRADES':<7} | {'WIN RATE':<8}")
        print("-" * 68)
        for bot in BOTS:
            total = len(bot['history'])
            wins = sum(1 for t in bot['history'] if t['result'] == 'WIN')
            wr = (wins / total * 100) if total else 0.0
            equity = bot['capital'] + unrealized(bot)
            print(f"{bot['name']:<15} | ${bot['capital']:<9.2f} | ${equity:<9.2f} | {len(bot['positions']):<5} | {total:<7} | {wr:.1f}%")
        print("=" * 68)

        time.sleep(SCAN_INTERVAL)

except KeyboardInterrupt:
    print("\n🛑 Bot comparatif arrêté proprement.")
