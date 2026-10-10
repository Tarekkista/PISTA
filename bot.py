import requests

def get_bitget_spot_strong_buy_tokens():
    """
    Interroge l'API Screener de TradingView en 1 seconde.
    Filtre : Bitget + SPOT uniquement + USDT + Rating 1D >= 0.5 (STRONG BUY).
    """
    print("⚡ Interrogation du Screener TradingView (Bitget SPOT 1D)...")
    
    url = "https://scanner.tradingview.com/crypto/scan"
    
    # Requête native avec filtre explicite sur le marché SPOT
    payload = {
        "filter": [
            {"left": "Recommend.All", "operation": "egreater", "right": 0.5},  # STRONG BUY
            {"left": "exchange", "operation": "equal", "right": "BITGET"},
            {"left": "typespec", "operation": "in_range", "right": ["spot"]}   # --- FILTRE EXCLUSIF SPOT ---
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
        spot_strong_buy_tokens = []
        
        for item in results:
            symbol_raw = item.get("s", "")  # Ex: "BITGET:STRKUSDT"
            if ":" in symbol_raw:
                ticker = symbol_raw.split(":")[1]
                
                # Conserver uniquement les paires Spot terminant par USDT
                if ticker.endswith("USDT"):
                    formatted_symbol = ticker[:-4] + "/USDT"
                    spot_strong_buy_tokens.append(formatted_symbol)
                    
        return spot_strong_buy_tokens

    except Exception as e:
        print(f"⚠️ Erreur lors de la requête API TradingView : {e}")
        return []

if __name__ == "__main__":
    tokens = get_bitget_spot_strong_buy_tokens()
    
    print("\n" + "="*60)
    print(f"🚀 TOKENS BITGET SPOT EN STRONG BUY 1D ({len(tokens)}) :")
    print("="*60)
    print(tokens)
    print("="*60)
