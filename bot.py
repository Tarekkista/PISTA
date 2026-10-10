import subprocess
import sys
import time

# Installations automatiques des modules si manquants
for module in ["tradingview_ta", "ccxt"]:
    try:
        __import__(module)
    except ImportError:
        print(f"📦 Installation automatique de {module}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", module])

import ccxt
from tradingview_ta import TA_Handler, Interval

# Initialisation de Bitget via CCXT
exchange = ccxt.bitget({'enableRateLimit': True})

def get_all_bitget_usdt_pairs():
    """Récupère la totalité des paires Spot USDT actives sur Bitget."""
    try:
        print("🔄 Récupération de la liste complète des tokens sur Bitget...")
        markets = exchange.load_markets()
        pairs = [
            symbol for symbol, m in markets.items()
            if m['spot'] and m['quote'] == 'USDT' and m['active']
        ]
        print(f"📌 {len(pairs)} paires USDT trouvées sur Bitget Spot.")
        return pairs
    except Exception as e:
        print(f"⚠️ Erreur lors de la récupération des marchés Bitget: {e}")
        return []

def check_strong_buy(symbol):
    """Vérifie auprès de TradingView 1D si le token est en STRONG_BUY sur Bitget."""
    ticker_tv = symbol.replace('/', '')
    try:
        handler = TA_Handler(
            symbol=ticker_tv,
            screener="crypto",
            exchange="BITGET",
            interval=Interval.INTERVAL_1_DAY
        )
        analysis = handler.get_analysis()
        rec = analysis.summary.get('RECOMMENDATION')
        return rec == 'STRONG_BUY'
    except Exception:
        # Tente sans forcer l'exchange si la paire a un nom légèrement différent sur TV
        try:
            handler_auto = TA_Handler(
                symbol=ticker_tv,
                screener="crypto",
                exchange="",
                interval=Interval.INTERVAL_1_DAY
            )
            analysis_auto = handler_auto.get_analysis()
            return analysis_auto.summary.get('RECOMMENDATION') == 'STRONG_BUY'
        except Exception:
            return False

def scan_entire_bitget_market():
    all_pairs = get_all_bitget_usdt_pairs()
    if not all_pairs:
        return

    print("\n" + "="*60)
    print("🔍 SCAN GLOBAL DE BITGET EN COURS (TECHNICAL RATING 1D)...")
    print("="*60)

    strong_buy_tokens = []
    
    # On parcourt TOUT le marché Bitget
    for i, symbol in enumerate(all_pairs, 1):
        print(f"⏳ [{i}/{len(all_pairs)}] Analyse de {symbol}...", end="\r")
        if check_strong_buy(symbol):
            strong_buy_tokens.append(symbol)
            print(f"\n🚀 DÉTECTÉ STRONG BUY 1D : {symbol}")

    print("\n\n" + "="*60)
    print(f"✅ RÉSULTAT DU SCAN GLOBAL BITGET ({len(strong_buy_tokens)} tokens trouvés) :")
    print(strong_buy_tokens)
    print("="*60 + "\n")

if __name__ == "__main__":
    scan_entire_bitget_market()
