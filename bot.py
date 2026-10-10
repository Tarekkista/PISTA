import requests

def get_bitget_pure_spot_strong_buy():
    """
    Récupère les tokens Strong Buy 1D et filtre proprement en local
    pour ne garder que le Spot USDT pur.
    """
    print("⚡ Interrogation instantanée du Screener TradingView (1D)...")
    
    url = "https://scanner.tradingview.com/crypto/scan"
    
    payload = {
        "filter": [
            {"left": "Recommend.All", "operation": "egreater", "right": 0.5},  # STRONG BUY
            {"left": "exchange", "operation": "equal", "right": "BITGET"}
        ],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name", "close", "change", "Recommend.All"],
        "sort": {"sortBy": "Recommend.All", "sortOrder": "desc"},
        "range": [0, 150]
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        
        results = data.get("data", [])
        spot_tokens = []
        
        for item in results:
            symbol_raw = item.get("s", "")  # Ex: "BITGET:STRKUSDT" ou dérivés
            if ":" in symbol_raw:
                ticker = symbol_raw.split(":")[1]
                
                # On ne garde que les paires en /USDT qui sont du Spot pur
                # (on élimine les contrats perpétuels / futures qui contiennent des indices de date ou des suffixes spécifiques)
                if ticker.endswith("USDT") and not any(sub in ticker for sub in ["PERP", "QUARTER", "_", "-"]):
                    formatted_symbol = ticker[:-4] + "/USDT"
                    if formatted_symbol not in spot_tokens:
                        spot_tokens.append(formatted_symbol)
                    
        return spot_tokens

    except Exception as e:
        print(f"⚠️ Erreur lors de la requête API TradingView : {e}")
        return []

if __name__ == "__main__":
    tokens = get_bitget_pure_spot_strong_buy()
    
    print("\n" + "="*60)
    print(f"🚀 TOKENS BITGET SPOT EN STRONG BUY 1D ({len(tokens)}) :")
    print("="*60)
    print(tokens)
    print("="*60)
