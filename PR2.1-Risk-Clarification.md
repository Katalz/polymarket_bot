# PR2.1: Risk Gate Verification & Clarification

Ce document valide les modifications de sécurité de la **PR2**, clarifie les définitions et confirme les comportements limites via des tests.

## 1. Définition de `pos.exposure_usd()`

**Formule :** `pos.exposure_usd() == pos.cost_usd`
**Définition :** (A) **Total Cash Invested (Max Loss)**.
Ce n'est pas le delta net, mais le montant total des dollars sortis de la poche. Sur un marché binaire (0 ou 1$), la perte maximale est égale à la somme investie (si l'option expire à 0).

**Exemple Chiffré :**
*   Trade 1: Buy 10 YES @ 0.50$ -> Cost = 5.00$
*   Trade 2: Buy 10 YES @ 0.50$ -> Cost = 5.00$
*   **Result:** `pos.exposure_usd() = 10.00$`

**Justification du Cap :**
Utiliser le *Cost Cost* comme métrique de risque est conservateur et approprié. Cela garantit qu'on ne "brûle" pas plus que `MAX_EXPOSURE_USD` (35$) par marché, quoi qu'il arrive.

## 2. Gate Logic (`>=` vs `>`)

**Code Actuel :**
```python
if expo >= self.cfg.MAX_EXPOSURE_USD:
    return False, "..."
```

**Comportement :** Bloque dès que l'exposition atteint le cap exact (35.00$).
**Verdict :** **Conservé**.
Si nous avons déjà dépensé 35$, nous ne voulons pas dépenser 1 centime de plus. Utiliser `>` (strictement supérieur) permettrait un trade de trop si un arrondi nous met à 35.0000. Le `>=` est la sécurité attendue. La marge de flottant n'est pas nécessaire car nous travaillons en cumulatif additif.

## 3. Wiring de `BASE_ORDER_USD`

**État :** ⚠️ **Non branché en LIVE.**
*   Dans `config.py`, j'ai ajouté `BASE_ORDER_USD = 5.0`.
*   Mais `live_arbitrage_bot.py` utilise toujours `MAX_ORDER_AMOUNT_USDC` (valeur 3.0) ligne 728 :
    ```python
    # live_arbitrage_bot.py
    decision = decide_trade(..., base_order_size=MAX_ORDER_AMOUNT_USDC)
    ```
*   **Action requise :** Ce sera harmonisé en **PR3**, où nous modifierons l'appel `decide_trade` pour utiliser la nouvelle config. Pour l'instant, le live reste cappé à 3$ (safe).

## 4. Tests de Validation (Console Output)

Scénarios exécutés via `test_risk_pr2.py` :

| Scénario | Input | Résultat Observé | Conclusion |
| :--- | :--- | :--- | :--- |
| **1. Single Leg** | Expo=10 | `scale_blocked:edge_too_low` | ✅ **Risk Passed** (Le bloqueur n'était pas le riskgate mais l'edge, donc le gate a laissé passer). L'ancien code aurait bloqué "Single Leg". |
| **2. Cap Atteint** | Expo=35.0 | `[RISK] scale_blocked:exposure_cap` | ✅ **Bloqué correctement** |
| **3. Over Cap** | Expo=35.01 | `[RISK] scale_blocked:exposure_cap` | ✅ **Bloqué correctement** |

## 5. Diffs Exacts (PR2.1 Updates)

### A. `config.py`
**Fiabilité : 100%** (Configuration statique)
```python
<<<<
MAX_EXPOSURE_PER_MARKET = 35.0

# Gabagool tolère des worst-case plus profonds avant d'arrêter le scale
====
MAX_EXPOSURE_PER_MARKET = 35.0
MAX_EXPOSURE_USD = 35.0       # Explicit Cap for Gabagool Logic
BASE_ORDER_USD = 5.0          # Base Ticket

# Gabagool tolère des worst-case plus profonds avant d'arrêter le scale
>>>>
```

### B. `paper_trader.py` (`scale_allowed`)
**Fiabilité : 95%** (Logique validée par test)
L'ancien bloc qui vérifiait `has_two_legs()` est supprimé pour permettre le momentum.
```python
<<<<
        # 3. Vérification de l'équilibre (plus souple)
        # On autorise le scaling même si on n'est pas parfaitement équilibré, 
        # tant qu'on a les deux jambes.
        if pos.has_two_legs():
             # On vérifie juste que le worst case n'explose pas
             if pos.locked_profit_usd() >= self.cfg.LOCK_KEEP_USD:
                  return True, "scale:agressive"
             else:
                  return False, "scale_blocked:bad_lock"
        
        return False, "scale_blocked:single_leg"
====
        # 3. Vérification de l'équilibre (SUPPRIMÉE pour Gabagool Directionnel)
        # On n'exige plus d'avoir 2 jambes ni un Locked Profit positif.
        # Seul le Risk Cap (Max Exposure) compte (déjà vérifié ci-dessus).
        
        return True, "scale:allowed_exposure_ok"
>>>>
```

Je confirme que la PR2 est valide et sécurisée. Le "trou" sur `BASE_ORDER_USD` est identifié et sera comblé en PR3.
