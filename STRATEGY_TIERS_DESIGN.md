# Strategy Tiers Design: From Survival to Dominance

## 1. Résumé Exécutif
Ce document définit l'évolution de la stratégie du bot en fonction de la taille du capital ("Bankroll").
*   **Le problème.** La "Vraie Strategie Gabagool" (Market Making + Hedging) est mathématiquement impossible avec un petit capital à cause du ticket minimum de Polymarket ($5) et de l'impact du spread.
*   **La solution.** Nous adaptons la stratégie en 3 Tiers.
    *   **Tier 1 (Micro) :** Maximise la croissance par la variance (Sniper All-Out).
    *   **Tier 2 (Grey Zone) :** Lisse la courbe de PnL par des sorties partielles (Skimming).
    *   **Tier 3 (Mid-Core) :** Sécurise le capital par du vrai hedging (Arb Lock).

---

## 2. Définition des Tiers

### Seuils de Bankroll
Le passage d'un Tier à l'autre est dicté par la capacité à diviser la position en "parts" significatives respectant le ticket minimum ($5).

| Tier | Nom | Bankroll Range | Position Size (Risk) | Nb Tickets | Objectif |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | **Micro (Survival)** | **$100 - $1,000** | **$15 - $35** (High %) | 3 - 7 | **Croissance Explosive** (Sortir de la pauvreté) |
| **2** | **Grey Zone (Growth)** | **$1,000 - $5,000** | **$50 - $150** (3-5%) | 10 - 30 | **Lissage de PnL** (Éviter les jours à $0) |
| **3** | **Mid-Core (Wealth)** | **$5,000+** | **$200+** (2-4%) | 40+ | **Yield Constant** (Protection du capital) |

### Hypothèses Critiques
*   **Min Ticket :** ~$5.00 (Contrainte Hard Polymarket).
*   **Spread/Slippage :** ~1-3% par transaction (Taker).
*   **Fees Exchange :** 0% (ou négligeable vs Spread).

---

## 3. Règles par Tier

| Règle | Tier 1 (Micro) | Tier 2 (Grey Zone) | Tier 3 (Mid-Core) |
| :--- | :--- | :--- | :--- |
| **Entry Logic** | **Gabagool (Momentum)** | **Gabagool (Momentum)** | **Gabagool (Momentum + Maker)** |
| **Exit Logic** | **SELL ALL (+25%)** | **Partial Skimming** | **Hedging Lock (+Buy Opposite)** |
| **Time Exit** | **T-30s (SELL ALL)** | **T-30s (SELL REMAINDER)** | **T-30s (Close Arb)** |
| **Sizing Base** | `$5.00` | `$5.00` | `$10.00+` |
| **Max Exposure** | `$16.50` (3 bullets) | `$75.00` (15 bullets) | `$250.00+` |
| **Hedge Allowed?** | ❌ **NON** (Trop cher) | ❌ **NON** (Still expensive) | ✅ **OUI** (Rentable) |

---

## 4. Logique d'Exit Détaillée

### Tier 1: Micro (SELL ALL - PR4A)
*   **Logique :** "Hit & Run". On n'a pas assez de volume pour diviser.
*   **Trigger :** Si PnL > +25% **OU** Time < 30s.
*   **Action :** `SELL_YES` (100% Qty).

### Tier 2: Grey Zone (Partial Skimming)
*   **Logique :** "Prendre des miettes sur la montée".
*   **Trigger 1 :** Si PnL > +15%.
    *   **Action :** Vendre **33%** de la position. (Secure Costs).
*   **Trigger 2 :** Si PnL > +30%.
    *   **Action :** Vendre **33%** de la position. (Secure Profit).
*   **Trigger 3 :** Si Time < 30s.
    *   **Action :** Vendre les **34% restants** (Moonbag).

### Tier 3: Mid-Core (Defensive Hedging)
*   **Logique :** "Vraie stratégie Gabagool". On lock l'arb.
*   **Trigger :** Si `(Prix YES Actuel + Prix NO Actuel) > 1.02` (Occasion de short synthétique).
*   **Action :** Acheter **NO** pour équilibrer le Delta à 0.
*   **Résultat :** Profit bloqué mathématiquement, peu importe l'issue.

---

## 5. Gestion de la Granularité

Le défi est de calculer des tailles partielles valides (>$5).

*   **Formule Partial Sell :**
    ```python
    target_sell_usd = total_position_usd * 0.33
    if target_sell_usd < MIN_TICKET_USD ($5):
        # Fallback pour Tier 1 / Limite Tier 2
        return SELL_ALL
    else:
        return target_sell_usd
    ```
*   **Arrondis :** Toujours arrondir à 2 décimales inférieures pour éviter les erreurs API.

---

## 6. State Machine (Tier 2 Example)

Pour gérer les sorties partielles, le bot doit avoir une mémoire (ou déduire l'état).

*   **STATE 0: `SEARCHING`** (Exposure = 0)
*   **STATE 1: `ACCUMULATING`** (Exposure > 0, PnL < 15%)
*   **STATE 2: `SKIM_1_DONE`** (Exposure réduite d'1/3, PnL a touché +15%)
    *   *Condition:* `total_qty < initial_max_qty * 0.70`
    *   *Empêche de re-vendre le même seuil en boucle.*
*   **STATE 3: `SKIM_2_DONE`** (Exposure réduite de 2/3, PnL a touché +30%)
*   **STATE 4: `EXITED`** (Exposure = 0, Time < 30s ou Manual Close)

---

## 7. Logs & Observabilité

Les logs doivent permettre de valider quel Tier est actif et quelle règle s'applique.

*   `[TIER]` : Affiche le Tier au démarrage (ex: `TIER: MICRO ($140)`).
*   `[TP1]` : Partial Exit 1 déclenché.
*   `[TP2]` : Partial Exit 2 déclenché.
*   `[SELL_ALL]` : Sortie complète (Tier 1 ou Time Exit).
*   `[HEDGE_LOCK]` : Achat jambe opposée (Tier 3).

---

## 8. Cas Limites & Risques

### Cas Limites
1.  **Partial Fill :** On demande de vendre 33%, on est fill à 10%.
    *   *Mitigation :* Le bot doit retenter au prochain tick si la condition est encore vraie.
2.  **Absence de Bid :** On veut sortir mais personne n'achète.
    *   *Mitigation :* Panic Sell à -10% du prix (Market Dump) ou HOLD jusqu'à expiry.
3.  **Tier Transition :** Bankroll passe de $990 à $1010.
    *   *Règle :* Le Tier est configuré au démarrage du bot. Pas de changement dynamique en pleine session.

### Risques & Fiabilité
| Règle | Fiabilité (vs Gabagool Origine) | Risque Principal |
| :--- | :--- | :--- |
| **Entry** | 95% | Faux positif (Momentum qui se retourne). |
| **Exit Micro** | 100% (Adapté) | Vendre trop tôt (rater le x10). |
| **Exit Grey** | 80% (Approx) | Complexité d'exécution (État partiel). |
| **Hedge** | 0% (Désactivé) | Risque directionnel total (Perte sèche possible). |

---

## Conclusion
Pour l'instant, avec **$140**, nous sommes strictement dans le **Tier 1 (Micro)**.
La seule règle valide est **SELL ALL**. Toute tentative de "Partial" ou "Hedge" détruirait l'espérance de gain mathématique.
