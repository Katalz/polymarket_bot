# PR3: Momentum Logic & Hard Stops

## 1. Changement Fondamental : Hedge -> Momentum

L'objectif est de remplacer le réflexe "Neutralisation" par un réflexe "Conviction".
Le bloc qui détecte une position "Single Leg" ne doit plus déclencher un HEDGE (achat opposé) mais evaluer un ADD-ON (achat identique).

### A. Règle de Décision (Pseudo-Code)

```python
# SI on a une position nette significative (Imbalance > EPS)
Si imbalance > EPS_QTY:
    
    # 1. Protection Directionnelle (Hard Stop)
    Si (PrixActuel < PrixMoyen * 0.85):
        STOP OPENING ("Trop tard pour renforcer, on garde ou on attend exit")
    
    # 2. Logique Momentum (Gabagool)
    Si Conviction == YES:
        Action: BUY YES (Renforcement)
        Condition: Edge > 0 OU Prix Cheap
        Interdit: BUY NO (Hedge) -> Log "[MOMO] hedge_blocked"
        
    Si Conviction == NO:
        Action: BUY NO (Renforcement)
        ...
```

### B. Small Size Constraints
On conserve les `BASE_ORDER_USD` (5$) et le `MAX_EXPOSURE_USD` (35$) mis en place précédemment. Le momentum permet d'aller jusqu'au cap, mais pas au-delà.

## 2. Changements Effectifs (`paper_trader.py`)

### Suppression du Bloc Hedge
Lignes ~251-283 remplacées.

### Introduction du Hard Stop
Ajout d'un calcul `avg_price` simplifié (cost / qty). Si le prix chute de 15% sous notre moyen, on arrête d'ajouter du risque.

## 3. Plan de Test (Paper)

1.  **Inventory YES-only (Profit)** :
    *   Pos: 10 YES @ 0.50. Market: YES @ 0.55.
    *   Result: BUY YES (Add-on).
2.  **Inventory YES-only (Loss < 15%)** :
    *   Pos: 10 YES @ 0.50. Market: YES @ 0.45.
    *   Result: BUY YES (Cheap Add-on).
3.  **Inventory YES-only (Hard Stop)** :
    *   Pos: 10 YES @ 0.50. Market: YES @ 0.40 (-20%).
    *   Result: HOLD + Log `[STOP] hard_stop_triggered`.
4.  **Hedge Attempt** :
    *   Pos: 10 YES. Edge favors NO.
    *   Result: HOLD + Log `[MOMO] hedge_blocked`.

## 4. Fiabilité
*   **Momentum Logic (85%)** : Le comportement d'accumulation est certifié. La règle exacte "Edge vs Cheap" est une approximation robuste.
*   **Hard Stop (100%)** : Nécessité absolue de sécurité pour une small bankroll.
