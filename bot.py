# ==========================================
# 5. BOUCLE D'EXÉCUTION ET BENCHMARK
# ==========================================
print("\n🤖 BOT COMPARATIF ACTIF (DONCHIAN 30M vs DONCHIAN 60M)")
try:
    while True:
        timestamp_str = datetime.now().strftime('%H:%M:%S')
        print(f"\n🔍 ==================== SCAN ({timestamp_str}) ====================")
        
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
        
        # Pause paramétrable entre chaque scan
        time.sleep(SCAN_INTERVAL)
        
except KeyboardInterrupt:
    print("\n🛑 Bot comparatif arrêté proprement.")
