#!/usr/bin/env python3
"""
analyze_pnl.py
Analyse les fichiers CSV de trades générés par gabagool_tracker.py pour afficher le bilan par marché.
"""

import os
import glob
import csv
import sys

# Dossiers à scanner (priorité à celui utilisé par le tracker actuel)
TARGET_DIRS = ["gabagool_trades", "distinct_trades"]

def analyze_market_file(filepath):
    """Lit un fichier CSV et extrait les métriques finales"""
    filename = os.path.basename(filepath)
    
    q_yes = 0.0
    q_no = 0.0
    exposure = 0.0
    locked_pnl = 0.0
    last_row = None
    row_count = 0
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                last_row = row
                row_count += 1
    except Exception as e:
        print(f"[ERROR] Reading {filename}: {e}")
        return None

    if not last_row:
        return None  # Fichier vide ou juste header

    # Extraire les dernières valeurs accumulées
    try:
        q_yes = float(last_row.get("q_yes", 0))
        q_no = float(last_row.get("q_no", 0))
        exposure = float(last_row.get("exposure", 0))
        locked_pnl = float(last_row.get("locked_pnl", 0))
    except ValueError:
        pass

    # Calcul des scénarios de sortie
    # Si le marché finit YES : on gagne q_yes ($1/part), on a dépensé exposure
    # PnL_YES = q_yes - exposure
    # Si le marché finit NO : on gagne q_no ($1/part), on a dépensé exposure
    # PnL_NO = q_no - exposure
    
    pnl_if_yes = q_yes - exposure
    pnl_if_no = q_no - exposure
    
    # Locked PnL est censé être le min(pnl_if_yes, pnl_if_no) -> le profit garanti
    # Vérifions si le tracker le calcule comme ça.
    # Dans le tracker: locked = min(q_yes, q_no) - expo
    # C'est cohérent.
    
    is_hedged = abs(q_yes - q_no) < 5.0 # Tolérance de 5 parts pour dire "équilibré" si gros volumes
    
    return {
        "filename": filename,
        "count": row_count,
        "q_yes": q_yes,
        "q_no": q_no,
        "exposure": exposure,
        "locked_pnl": locked_pnl,
        "pnl_if_yes": pnl_if_yes,
        "pnl_if_no": pnl_if_no,
        "is_hedged": is_hedged
    }

def main():
    print("="*60)
    print("GABAGOOL PERFORMANCE ANALYSIS")
    print("="*60)
    
    found_files = []
    for d in TARGET_DIRS:
        if os.path.exists(d):
            files = glob.glob(os.path.join(d, "*.csv"))
            found_files.extend(files)
    
    if not found_files:
        print("Aucun fichier de trades trouvé.")
        return

    # Trier par nom (donc par date approximative si le timestamp est dans le nom)
    found_files.sort()
    
    total_locked = 0.0
    
    for f in found_files:
        stats = analyze_market_file(f)
        if not stats:
            continue
            
        print(f"\nMARKET: {stats['filename']}")
        print(f"  Trades: {stats['count']}")
        print(f"  Pos YES: {stats['q_yes']:>10.2f}")
        print(f"  Pos NO : {stats['q_no']:>10.2f}")
        print(f"  Invested: ${stats['exposure']:.2f}")
        print("-" * 30)
        
        # Affichage Gain/Perte Potentiels
        print(f"  Result if YES: ${stats['pnl_if_yes']:+.2f}")
        print(f"  Result if NO : ${stats['pnl_if_no']:+.2f}")
        
        # Bilan Sécurisé
        print(f"  LOCKED PnL   : ${stats['locked_pnl']:+.2f} (Risk-Free)")
        
        # Analyse Equilibre
        if stats['is_hedged']:
             print(f"  STATUS: [HEDGED] Positions are balanced.")
        else:
             diff = stats['q_yes'] - stats['q_no']
             direction = "YES" if diff > 0 else "NO"
             print(f"  STATUS: [EXPOSED] Net exposure to {direction} ({abs(diff):.2f} shares)")
             
        total_locked += stats['locked_pnl']

    print("\n" + "="*60)
    print(f"TOTAL LOCKED PnL (All Markets): ${total_locked:+.2f}")
    print("="*60)

if __name__ == "__main__":
    main()
