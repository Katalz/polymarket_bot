# PR2: Risk & Exposure Gates

## 1. Contexte : L'invalidité du Locked Profit

Dans une stratégie d'arbitrage neutre (Market Making), on assure que `min(qty_yes, qty_no) - cost > -3$`. C'est le "Locked Profit".
Dans une stratégie **Directionnelle (Gabagool)**, on a souvent : `Qty_Yes = 100, Qty_No = 0`.
*   Locked Profit = `0 - Cost` = `-Cost` (ex: -50$).
*   Le gate actuel (`locked >= -3`) bloquerait immédiatement toute position directionnelle.

**Solution :** Remplacer ce gate par un **Exposure Cap** strict (`Cost <= MAX`).

## 2. Changements effectifs

### A. Paramètres (`config.py`)
Ajout explicite de la limite d'exposition en USD absolue.
```python
MAX_EXPOSURE_USD = 35.0  # Cap strict pour small bankroll
BASE_ORDER_USD = 5.0     # Ticket de base
```

### B. Suppression Gates Neutres (`paper_trader.py`)
**Fonction :** `scale_allowed`
**Diff :**
```python
# Avant
if pos.has_two_legs():
    if pos.locked_profit_usd() >= self.cfg.LOCK_KEEP_USD:
         return True, "scale:agressive"
    else:
         return False, "scale_blocked:bad_lock"
return False, "scale_blocked:single_leg"

# Après
# 1. Exposure Check (Primordial)
if pos.exposure_usd() >= self.cfg.MAX_EXPOSURE_USD:
    return False, f"[RISK] scale_blocked:exposure_cap exposure={pos.exposure_usd():.2f} cap={self.cfg.MAX_EXPOSURE_USD}"

# 2. Relaxed Logic (Autorise Single Leg + Negative Lock)
# On suppose que l'Edge check a déjà validé l'opportunité avant
return True, "scale:allowed_exposure_ok"
```

## 3. Plan de Test

### Scénario 1 : Directionnel Autorisé
*   **État :** `Qty_Yes=20, Qty_No=0, Cost=10$` (Single Leg). `Lock = -10$`.
*   **Gate Actuel :** Refus (`single_leg` ou `bad_lock`).
*   **Gate Nouveau :** `10$ < 35$ (Cap)` -> **AUTORISÉ**.

### Scénario 2 : Cap Atteint
*   **État :** `Cost = 36$`.
*   **Gate Nouveau :** `36$ > 35$` -> **REFUS** + Log `[RISK] scale_blocked:exposure_cap`.

## 4. Fiabilité
*   **100%** : Nécessaire pour que le bot puisse fonctionner en mode non-hedgé. Sans ce changement, le code de momentum (PR3) serait mort-né (dead code unreachable due to risk block).
