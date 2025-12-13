#!/usr/bin/env python3
"""
Analyse approfondie de la stratégie Gabagool sur les marchés BTC 15min
"""

import pandas as pd

df = pd.read_csv('gabagool_time_travel_analysis.csv')

print('=' * 70)
print('ANALYSE COMPLETE DE LA STRATEGIE GABAGOOL')
print('=' * 70)

# === 1. VUE GLOBALE ===
print('\n--- VUE GLOBALE ---')
print(f'Total trades dans le CSV: {len(df)}')
print(f'Marchés uniques: {df["market_slug"].unique().tolist()}')
print(f'Range temps: {df["time_utc"].min()} -> {df["time_utc"].max()}')

# === 2. ETAT FINAL PAR MARCHE ===
print('\n' + '=' * 70)
print('ETAT FINAL PAR MARCHE (uniquement market_offset=0 pour éviter duplications)')
print('=' * 70)

# Prendre uniquement le market_offset=0 pour chaque marché
for market in df['market_slug'].unique():
    mkt_df = df[(df['market_slug'] == market) & (df['market_offset'] == 0)]
    if len(mkt_df) == 0:
        # Fallback si pas de offset=0
        mkt_df = df[df['market_slug'] == market]
    
    # Dernier état du marché
    final = mkt_df.iloc[-1]
    q_yes = final['local_q_yes']
    q_no = final['local_q_no']
    expo = final['local_exposure']
    locked = final['local_locked_pnl']
    
    print(f'\n>>> {market}')
    print(f'    q_YES: {q_yes:,.2f} shares')
    print(f'    q_NO:  {q_no:,.2f} shares')
    print(f'    Exposure (USD dépensé): ${expo:,.2f}')
    print(f'    Locked PnL: ${locked:,.2f}')
    print(f'    Imbalance |YES - NO|: {abs(q_yes - q_no):,.2f}')
    if max(q_yes, q_no) > 0:
        ratio = min(q_yes, q_no) / max(q_yes, q_no)
        print(f'    Balance Ratio (min/max): {ratio:.4f} (1.0 = parfaitement équilibré)')

# === 3. ANALYSE DU HEDGING ===
print('\n' + '=' * 70)
print('ANALYSE DU HEDGING')
print('=' * 70)

# Regarder le marché principal (offset=0)
market_0 = df[df['market_offset'] == 0].copy()

if len(market_0) > 0:
    yes_trades = market_0[market_0['side'] == 'YES']
    no_trades = market_0[market_0['side'] == 'NO']
    
    print(f'\nMARCHE OFFSET=0 (cible):')
    print(f'  Trades YES: {len(yes_trades)}')
    print(f'  Trades NO:  {len(no_trades)}')
    print(f'  Volume YES: ${yes_trades["usd_volume"].sum():,.2f}')
    print(f'  Volume NO:  ${no_trades["usd_volume"].sum():,.2f}')
    print(f'  Prix moyen YES: {yes_trades["price"].mean():.4f}')
    print(f'  Prix moyen NO:  {no_trades["price"].mean():.4f}')
    
    # Edge analysis (quand les deux marchés sont < 1)
    avg_price_sum = yes_trades["price"].mean() + no_trades["price"].mean()
    print(f'\n  EDGE THEORIQUE (prix_yes + prix_no):')
    print(f'    Moyenne: {avg_price_sum:.4f}')
    print(f'    < 1 signifie opportunité d\'arbitrage')

# === 4. PATTERN DE TRADING ===
print('\n' + '=' * 70)
print('PATTERN DE TRADING')
print('=' * 70)

# Regarder l'ordre des trades YES vs NO
market_0_sorted = market_0.sort_values('timestamp')
print('\n  Les 20 premiers trades:')
print('  Timestamp | Side | Size     | Price  | Acc. YES  | Acc. NO   | Locked PnL')
print('  ' + '-' * 80)
for i, row in market_0_sorted.head(20).iterrows():
    print(f'  {row["timestamp"]} | {row["side"]:3s} | {row["size"]:8.2f} | {row["price"]:.4f} | {row["local_q_yes"]:9.2f} | {row["local_q_no"]:9.2f} | {row["local_locked_pnl"]:10.2f}')

# === 5. EVOLUTION DU LOCKED PNL ===
print('\n' + '=' * 70)
print('EVOLUTION DU LOCKED PNL (snapshots)')
print('=' * 70)

# Snapshots à différents % du marché
percentiles = [0, 10, 25, 50, 75, 90, 100]
for pct in percentiles:
    idx = min(int(len(market_0_sorted) * pct / 100), len(market_0_sorted) - 1)
    row = market_0_sorted.iloc[idx]
    print(f'  {pct:3d}% | q_yes={row["local_q_yes"]:10.0f} | q_no={row["local_q_no"]:10.0f} | locked_pnl=${row["local_locked_pnl"]:10.2f}')

# === 6. CONCLUSION ===
print('\n' + '=' * 70)
print('CONCLUSION - CARACTERISTIQUES DE LA STRATEGIE GABAGOOL')
print('=' * 70)

# État final du marché principal
final_0 = market_0.iloc[-1]
q_yes_final = final_0['local_q_yes']
q_no_final = final_0['local_q_no']
locked_final = final_0['local_locked_pnl']

balanced = abs(q_yes_final - q_no_final) < (max(q_yes_final, q_no_final) * 0.01)

print(f'\n  1. HEDGING COMPLET: {"OUI ✅" if balanced else "NON ❌"}')
print(f'     Raison: q_YES={q_yes_final:.0f} vs q_NO={q_no_final:.0f}, diff={abs(q_yes_final - q_no_final):.0f}')

print(f'\n  2. PROFIT LOCKED POSITIF: {"OUI ✅" if locked_final > 0 else "NON ❌"}')
print(f'     Locked PnL final: ${locked_final:,.2f}')

# Est-ce qu'il prend du directionnel?
profit_if_yes = q_yes_final - final_0['local_exposure']
profit_if_no = q_no_final - final_0['local_exposure']
print(f'\n  3. PROFIL DE RISQUE:')
print(f'     Si YES gagne: ${profit_if_yes:,.2f}')
print(f'     Si NO gagne:  ${profit_if_no:,.2f}')
print(f'     Difference:   ${abs(profit_if_yes - profit_if_no):,.2f}')

is_delta_neutral = abs(profit_if_yes - profit_if_no) < 100
print(f'     Strategy: {"DELTA NEUTRE ⚖️" if is_delta_neutral else "DIRECTIONNEL 📈" if profit_if_yes > profit_if_no else "DIRECTIONNEL 📉"}')

# Exposition totale
print(f'\n  4. EXPOSITION GLOBALE:')
print(f'     USD engagé: ${final_0["local_exposure"]:,.2f}')
print(f'     Profit garanti: ${locked_final:,.2f}')
if final_0["local_exposure"] > 0:
    roi = (locked_final / final_0["local_exposure"]) * 100
    print(f'     ROI garanti: {roi:.2f}%')
