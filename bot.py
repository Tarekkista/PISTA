import subprocess
import sys
import requests

def get_bitget_strong_buy_tokens_direct():
    """
    Interroge directement l'API JSON native du Screener TradingView.
    Récupère en 1 seconde TOUS les tokens Spot Bitget avec un Technical Rating 1D >= 0.5 (Strong Buy).
    """
    print("⚡ Interrogation instantanée de l'API Screener TradingView (1D)...")
    
    url = "https://scanner.tradingview.com/crypto/scan"
    
    # Requête native formatée comme celle du site web TradingView
    payload = {
        "filter": [
            {"left": "Recommend.All", "operation": "egreater", "right": 0.5}, # 0.5 à 1.0 = STRONG BUY
            {"left": "exchange", "operation": "equal", "right": "BITGET"}
        ],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name", "close", "change", "Recommend.All"],
        "sort": {"sortBy": "Recommend.All", "sortOrder": "desc"},
        "range": [0, 150]  # Récupère jusqu'à 150 résultats
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        
        results = data.get("data", [])
        strong_buy_tokens = []
        
        for item in results:
            # Le nom du ticker est retourné sous la forme "BITGET:STRKUSDT"
            symbol_raw = item.get("s", "")
            if ":" in symbol_raw:
                ticker = symbol_raw.split(":")[1]
                
                # Filtrer uniquement les paires Spot terminant par USDT
                if ticker.endswith("USDT"):
                    # Formater en format standard 'STRK/USDT' pour Bitget / CCXT
                    formatted_symbol = ticker[:-4] + "/USDT"
                    strong_buy_tokens.append(formatted_symbol)
                    
        return strong_buy_tokens

    except Exception as e:
        print(f"⚠️ Erreur lors de la requête API TradingView : {e}")
        return []

if __name__ == "__main__":
    tokens = get_bitget_strong_buy_tokens_direct()
    
    print("\n" + "="*60)
    print(f"🚀 TOKENS BITGET EN STRONG BUY 1D DÉTECTÉS ({len(tokens)}) :")
    print("="*60)
    print(tokens)
    print("="*60)
