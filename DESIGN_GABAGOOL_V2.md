# Audit & Adaptation Plan: Gabagool Strategy

## 1. Audit du Code Existant

### A. Architecture Actuelle
*   **Market Selection (`market_discovery.py`, `live_arbitrage_bot.py`)**: Utilise `discover_market()` et `discover_next_market()`. Déjà capable de trouver les marchés 15m BTC.
    *   *Point fort:* `discover_next_market` existe déjà et cherche à `current_ts + 900`.
    *   *Point faible:* Le trigger de pre-load est hardcodé à **T-10s** (`time_remaining <= 10`) dans `live_arbitrage_bot.py`. Gabagool commence à **T-60s**.
*   **Pricing (`live_arbitrage_bot.py`)**: `PriceStreamer` via WebSocket. Simple et efficace.
*   **Logic / Decision (`paper_trader.py`)**:
    *   Classe `GabagoolLikeStrategy`.
    *   *Conflit Majeur:* La logique actuelle **force le hedging** dès qu'une jambe existe (`if (pos.qty_yes > 0) ^ (pos.qty_no > 0): ... return "hedge:balance_to_..."`). C'est l'inverse du momentum Gabagool.
    *   *Conflit:* Le scaling (`scale_allowed`) vérifie `pos.has_two_legs()`. Gabagool scale souvent une seule jambe directionnelle.
*   **Risk (`config.py`, `paper_trader.py`)**:
    *   `MAX_EXPOSURE_PER_MARKET`: 35$.
    *   `LOCK_KEEP_USD`: -3$.
    *   `HARD_SAFETY_CAP`: 15$ par ordre.
    *   *Note:* C'est bien configuré pour une "small size", mais la logique de "Locked Profit" est omniprésente et bloquera le momentum directionnel qui a souvent un locked PnL négatif (risk one side).

### B. Ce qui est déjà compatible
*   **Architecture "Live Session":** `LiveTraderSession` isole bien l'état par marché.
*   **Pre-load basics:** Le mécanisme de `next_streamer` est déjà là pour switch instantané.
*   **Sizing dynamique:** La fonction `_get_valid_usd` gère déjà les contraintes de min ticket.

### C. Ce qui contredit Gabagool
*   **Hedge Systématique:** Le bot refuse d'avoir une position directionnelle > 1 trade. Il essaie immédiatement de "bilan_to_yes" ou "bilan_to_no".
*   **Absence de Duty Cycle:** Le bot trade en continu (`while True`). Pas de pause aux minutes 13, 10, 7...
*   **Timing Pre-load:** 10s vs 60s.

---

## 2. Interrogations Critiques (Risque d'erreur)

| Question | Risque | Vérification à faire / Interprétation |
| :--- | :--- | :--- |
| **Le Duty Cycle (2-on-1-off) est-il réel ou un artefact ?** | Moyen | Si c'est un artefact de rate-limit, l'implémenter nous désavantage. *Avis:* La régularité (13, 10, 7, 4) est trop parfaite pour être un bug. C'est probablement pour laisser l'oracle ou le carnet se stabiliser/recharger. |
| **La directionnalité est-elle "Momentum" ou "Market Making" ?** | Fort | Si Gabagool est en fait un MM qui quote les 2 côtés et se fait *prendre* un seul côté, on verra des trades directionnels dans les logs alors que l'intention est neutre. *Avis:* Le scaling agressif (rajouter à la jambe gagnante) suggère du Momentum actif, pas passif. |
| **Risque de ruine sans hedge ?** | Fort | Gabagool a $11k de bankroll par marché pour absorber la variance. Avec notre petite size, une série de 5 défaites directionnelles peut faire mal. *Solution:* Garder un **Hard Stop Loss** par marché plus strict que Gabagool. |

---

## 3. Plan de Modifications (Diff-Based Design)

### Résumé
Nous allons transformer le cœur de `paper_trader.py` pour abandonner le "Hedge First" au profit d'un "Momentum First". Nous allons modifier `live_arbitrage_bot.py` pour intégrer le **Duty Cycle** (pause forcée) et avancer le **Pre-load à 60s**. Le sizing restera conservateur mais suivra la conviction.

### Table As-Is vs To-Be

| Feature | As-Is (Code Actuel) | To-Be (Gabagool Adaptation) |
| :--- | :---: | :---: |
| **Logique Principale** | `if single_leg -> HEDGE` | `if single_leg -> CHECK MOMENTUM` |
| **Pre-load** | T - 10s | **T - 60s** |
| **Market Cycle** | Continu | **Filtre T%3 != 1** (13, 10, 7...) |
| **Risk Control** | `locked_profits >= -3$` | `exposure <= MAX` (Lock ignoré si momentum) |
| **Direction** | Rebalance vers 50/50 | **Add to Winner** (si Imb > 0, Buy YES) |

### Détail des Changements

#### A. Timing & Duty Cycle (`live_arbitrage_bot.py`)
*   **Actuel:** `time_remaining` sert uniquement au lock de fin.
*   **Modif:** Ajouter un filtre au début de la boucle `while True`:
    ```python
    minutes_left = int(time_remaining / 60)
    # Cycle 2-on-1-off: Pause aux minutes 13, 10, 7, 4, 1
    # Formule: (min - 1) % 3 == 0  =>  13-1=12(ok), 12-1=11(no)... wait.
    # Gap minutes: 13, 10, 7, 4, 1.
    gabagool_pause = (minutes_left in [13, 10, 7, 4, 1])
    if gabagool_pause: continue
    ```
*   *Fiabilité:* 90% (Pattern très clair dans les logs).

#### B. Pre-loading (`live_arbitrage_bot.py`)
*   **Actuel:** `if 0 < time_remaining <= 10 ...`
*   **Modif:** `if 0 < time_remaining <= 60 ...`
*   *Impact:* Permet d'être connecté et de recevoir les premiers ticks dès l'ouverture officielle.

#### C. Inventory & Momentum (`paper_trader.py`)
*   **Actuel:** Bloc `# 2) Si une seule jambe -> HEDGE`.
*   **Modif:** Remplacer ce bloc complet.
    *   Calculer `imbalance = qty_yes - qty_no`.
    *   Si `imbalance > 0` (Long YES):
        *   Interdire BUY NO (sauf si Stop Loss atteint).
        *   Autoriser BUY YES si `edge > min` ET `price_yes` favorable.
    *   Si `imbalance < 0` (Long NO):
        *   Interdire BUY YES.
        *   Autoriser BUY NO.
*   *Size adaptation:* Utiliser `base_order_size` small (3-5$). Ne pas scaler x10 comme Gabagool, mais peut-être x1.5 si l'inventory confirme le trend.

#### D. Risk Gates (`config.py` / `paper_trader.py`)
*   **Actuel:** `locked_profit_usd` est le garde-fou principal.
*   **Modif:** Le "Locked Profit" n'a plus de sens en directionnel pur (il sera toujours négatif = -exposure).
*   **Nouveau Garde-fou:** `MAX_LOSS_PER_MARKET`.
    *   Si `cost_usd > MAX_EXPOSURE`, stop opening.
    *   Si `estimated_pnl < -MAX_LOSS`, trigger **Emergency Close** (vendre ou хеdger pour sortir).
    *   *Note:* Pour l'instant, on se contente de "Stop Opening" pour ne pas complexifier avec de la vente active.

### Check-list Validation

1.  [ ] **Accepter** le retrait de la logique de hedging systématique dans `paper_trader.py`.
2.  [ ] **Accepter** l'extension du pre-load à 60s (plus de bande passante/CPU utilisé plus tôt).
3.  [ ] **Accepter** le "mutisme" du bot pendant les minutes de pause (13, 10, etc.).
4.  [ ] **Confirmer** les paramètres de risque réduits : Max Exposure 35$ (vs 11k$), Max Loss 18$.

Si tu valides ce plan, je procède à l'implémentation fichier par fichier.
