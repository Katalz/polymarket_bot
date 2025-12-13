# PR3: Design Validation & Final Rules

Voici les réponses et arbitrages finaux avant implémentation du code.

## 1. Calcul du Prix Moyen (`avg_price`)
**Formule :** `avg_price = pos.cost_usd / (pos.qty_yes + pos.qty_no)`
**Justification :** 
*   `pos.cost_usd` est un agrégat global (Gross Spend).
*   Dans une stratégie **Directionnelle/Momentum**, l'inventaire est quasi-exclusivement unilatéral (ex: 100 YES, 0 NO). Dans ce cas, la formule est exacte.
*   En cas de poussière résiduelle (ex: 100 YES, 1 NO), la dilution est négligeable et va dans le sens de la sécurité (légère hausse du prix moyen perçu).

## 2. Prix Hard Stop
**Comparaison :**
*   Si Conviction YES : `current_price_yes < avg_price * 0.85`
*   Si Conviction NO : `current_price_no < avg_price * 0.85`
**Confirmation :** On compare bien le prix de marché de l'outcome détenu.

## 3. Zone Cheap (`0.55`) & Symétrie
**Définition :** Zone d'**Accumulation**. Ce n'est pas un trade "sûr", mais une zone où le Risk/Reward est statistiquement favorable pour Gabagool.
**Symétrie :**
*   `CHEAP_YES_MAX = 0.55` (On achète YES si <= 0.55)
*   `CHEAP_NO_MAX = 0.55` (On achète NO si <= 0.55)
*   C'est symétrique en termes de "Prix Facial".

## 4. Définition de l'Edge
**Formule :** `e = 1.0 - (p_yes + p_no)` (Global Arb Edge).
**Rôle :**
*   C'est un proxy de "Inefficacité de marché".
*   Si `e > 0.5%`, le marché est "subventionné". On utilise cette subvention pour payer le spread de notre accumulation directionnelle.
*   L'edge est **non-signé** (global), mais on l'utilise pour financer la direction de notre conviction.

## 5. Wiring `BASE_ORDER_USD`
**Action Confirmée :**
*   `paper_trader.py` utilise déjà `self.cfg.BASE_ORDER_USD`.
*   **PR3 Modification :** `live_arbitrage_bot.py` sera modifié pour importer et utiliser `BASE_ORDER_USD` à la place de `MAX_ORDER_AMOUNT_USDC` dans l'appel `decide_trade`.
*   **Objectif :** Sizing unifié (5.0$) entre simulation et live.

---

## RÉSUMÉ FINAL DES RÈGLES PR3
1.  **Stop Opening** : Si `current_price < avg_cost * 0.85`, HOLD (Stop adding risk).
2.  **Momentum YES** (`Imbalance > 3 shares`):
    *   **NEVER** Buy NO (Strict).
    *   **BUY YES** si `Price(YES) <= 0.55` OU `Edge >= 0.5%`.
3.  **Momentum NO** (`Imbalance < -3 shares`):
    *   **NEVER** Buy YES (Strict).
    *   **BUY NO** si `Price(NO) <= 0.55` OU `Edge >= 0.5%`.
4.  **Wiring** : Le Live Bot utilise `BASE_ORDER_USD` (5$).

**PR3 READY FOR IMPLEMENTATION.**
