# PR3: Momentum Logic & Hard Stops (Design Final)

Ce document fige les règles d'implémentation de la PR3 après arbitrage des questions de design.

## 1. Arbitrages Design & Réponses

### 1️⃣ Seuil de Momentum (Edge)
*   **Décision :** Utilisation précise de `cfg.SCALE_EDGE_MIN` (0.005 / 0.5%).
*   **Justification :** Gabagool est un bot à haute fréquence qui accumule agressivement. Un seuil de 1.5% serait trop restrictif et tuerait le volume sur un "small account". Le filtre "Cheap" (`< 0.55`) agit comme un second filet.
*   **Fiabilité :** 90%.

### 2️⃣ Définition EPS_QTY (Imbalance Threshold)
*   **Décision :** `EPS_QTY = 3.0` (shares).
*   **Justification :** Avec une taille de ticket de base ~5$ (env. 10 shares à 0.50$), un déséquilibre de 3 shares représente 30% d'un ticket. C'est suffisant pour filtrer le bruit ("dust") sans retarder le momentum mode.
*   **Fiabilité :** 95%.

### 3️⃣ Calcul Prix Moyen (`cost / qty`)
*   **Décision :** Validé comme **Invariant Temporaire** (Buy-Only Model).
*   **Justification :** Tant que le bot ne vend pas pour sortir (pas de module "Active Exit" dans cette PR), `cost_usd` est monotone croissant. L'approximation est mathématiquement exacte.
*   **Fiabilité :** 100%.

### 4️⃣ Interaction Hard Stop vs Duty Cycle
*   **Décision :** **Le Duty Cycle (Pause) l'emporte.**
*   **Justification :** 
    *   Le "Hard Stop" implémenté ici est un **Stop Opening** (refus de rajouter du risque), pas un Panic Sell.
    *   La "Pause" (Duty Cycle) est un refus de trader.
    *   Les deux états sont cohérents : "Ne rien faire". Il n'est pas nécessaire de checker le Stop pendant la Pause car l'action résultante est identique (HOLD).
*   **Fiabilité :** 100% (Cohérence logique).

### 5️⃣ Hedge Blocking
*   **Décision :** **Interdiction Stricte (HOLD).**
*   **Justification :** C'est l'essence de la stratégie Gabagool. Si on hedge, on redevient un simple market maker qui perd sur les fees. On accepte la variance directionnelle.
*   **Fiabilité :** 95%.

---

## 2. Spécification d'Implémentation (`paper_trader.py`)

### A. Règle "Hard Stop" (Safety)
**Emplacement :** Dans `decide()`, juste après le calcul imbalance.
```python
# Hard Stop Opening
# Si prix actuel < 85% du prix moyen payé => HOLD (Stop Bleeding)
avg = pos.cost_usd / pos.qty
if current_price < avg * 0.85:
    return HOLD, "[STOP] hard_stop_triggered"
```

### B. Règle "Momentum YES" (Imbalance > 3.0)
**Logique :** 
1.  **Block BUY NO** : Strict.
2.  **Check BUY YES** :
    *   Condition 1 : `Price <= CHEAP_YES_MAX` (0.55) -> Zone d'accumulation "No Brainer".
    *   Condition 2 : `Edge >= SCALE_EDGE_MIN` (0.005) -> Zone de valeur.
3.  **Action** : Si Cond 1 ou 2 est vraie -> `BUY_YES` (via `_get_valid_usd` pour min ticket).

### C. Règle "Momentum NO" (Imbalance < -3.0)
*   Miroir du Momentum YES.

## 3. Plan de Test (Mises à jour)

Le script `test_momentum_pr3.py` sera ajusté pour refléter l'agressivité validée :
*   **Test Hedge Blocked** : Si l'edge est massif ($0.60 vs $0.30 -> Edge 0.10) et Imbalance YES, le bot **DOIT** acheter YES (Add-on). Le test doit valider `BUY_YES` et non `HOLD` dans ce cas extrême, OU on doit tester un cas où l'edge est faible/négatif pour vérifier le HOLD pure.
*   *Correction Test Scenario 2* : On utilisera un prix qui donne un edge négligeable pour vérifier le blocage du hedge "reflexe".

## 4. Statut
**PR3 READY FOR IMPLEMENTATION**
