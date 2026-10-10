import subprocess
import sys

# Installation automatique de tradingview-screener si absent
try:
    from tradingview_screener import Query, Column
except ModuleNotFoundError:
    print("📦 Installation de tradingview-screener...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tradingview-screener"])
    from tradingview_screener import Query, Column

def get_bitget_strong_buy_tokens_instant():
    """
    Interroge le Screener TradingView en 1 seule requête globale.
    Filtre : Exchange BITGET + Timeframe 1D + Technical Rating = STRONG_BUY
    """
    print("⚡ Interrogation instantanée du Screener TradingView (1D)...")
    
    try:
        # Requête unique directement envoyée au moteur du screener TradingView
        query = (
            Query()
            .set_markets('crypto')
            .select('name', 'change', 'close', 'Recommend.All')
            .where(
                Column('exchange') == 'BITGET',
                Column('Recommend.All') >= 0.5  # 0.5 à 1.0 correspond à STRONG_BUY sur TradingView
            )
            .get_scanner_data()
        )
        
        raw_data = query[1]  # Récupère le DataFrame des résultats
        
        # Ne garder que les paires Spot USDT (ex: STRKUSDT -> STRK/USDT)
        strong_buy_list = []
        for ticker in raw_data['name']:
            if ticker.endswith('USDT'):
                # Formate le symbole pour CCXT / Bitget
                symbol_ccxt = ticker[:-4] + '/USDT'
                strong_buy_list.append(symbol_ccxt)
                
        return strong_buy_list

    except Exception as e:
        print(f"⚠️ Erreur lors de la requête du screener : {e}")
        return []

if __name__ == "__main__":
    tokens = get_bitget_strong_buy_tokens_instant()
    
    print("\n" + "="*60)
    print(f"🚀 TOKENS BITGET EN STRONG BUY 1D DÉTECTÉS ({len(tokens)}) :")
    print("="*60)
    print(tokens)
    print("="*60)
