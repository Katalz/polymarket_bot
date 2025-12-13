# Gabagool Strategy: Reality Check & Implementation Plan

Ce document définit les modifications strictes à apporter au code actuel (`live_arbitrage_bot.py`, `paper_trader.py`) pour aligner le comportement sur la stratégie Gabagool reverse-engineerée, tout en respectant une contrainte de sizing faible.

## 1. Résumé Exécutif

Le bot actuel est structurellement un **Arbitragiste Neutre** (cherchant à lisser son exposition à 0) qui arrive parfois trop tard (Pre-load 10s).
Gabagool est un **Trader de Momentum Directionnel** (accumulant l'exposition gagnante) fonctionnant sur un rythme strict (2-on-1-off).

**Leviers majeurs manquants :**
1.  **Suppression du Hedging Forcé :** Remplacer le "rebalance" par un "add-on" directionnel.
2.  **Timing Stricte :** Implémenter le Duty Cycle (Pause aux minutes 13, 10, 7, 4, 1).
3.  **Avancer l'Horloge :** Déclencher le pre-load à T-60s pour être actif à la seconde 0.

**Niveau de Risque du Changement :** ÉLEVÉ (Passage de Delta-Neutre à Directionnel). Mitigé par des caps de sizing divisés par 100 par rapport à Gabagool.

---

## 2. Tableau AS-IS vs TO-BE

| Dimension | Code Actuel (`live_arbitrage_bot.py`, `paper_trade.py`) | Cible Gabagool-Lite | Écart Réel |
| :--- | :--- | :--- | :---: |
| **Logique d'Inventaire** | `if single_leg -> HEDGE` (Ligne 251 `paper_trader`) | **Momentum:** Si Imbal > 0, Buy MORE YES. | 🟥 Critique |
| **Cycle de Trading** | Continu (`while True`) | **2-on-1-off:** Pause min 13, 10, 7... | 🟥 Critique |
| **LifeCycle** | Switch market à T-10s | Switch market à **T-60s** | 🟧 Moyen |
| **Safety Gate** | `locked_profit >= -3$` | `exposure <= 35$` (Locked PnL ignoré) | 🟥 Critique |
| **Scaling** | Interdit si une seule jambe (`has_two_legs`) | Autorisé si conviction forte | 🟧 Moyen |

---

## 3. Changements Proposés (Par Bloc Stratégique)

### A. Timing & Duty Cycle (Rythme Cardiaque)

#### 📍 Où : `live_arbitrage_bot.py` -> Boucle `while True` (v. ligne 635)

*   **Actuel :** Aucune gestion de pause. Le bot bombarde l'API tant qu'il a du prix.
*   **Modification :**
    Insérer la logique de pause "Modulo 3" au début de la boucle.
    ```python
    minutes_remaining = int(time_remaining / 60)
    # Cycle 2-on-1-off: Pause aux minutes 13, 10, 7, 4, 1
    # Formule mathématique Gabagool : (min - 1) % 3 == 0
    if (minutes_remaining - 1) % 3 == 0 and minutes_remaining > 0:
        # LOG: "Gabagool Pause (M{minutes_remaining})"
        time.sleep(1)
        continue
    ```
*   **Pourquoi :** Évite le bruit, laisse l'oracle se caler, imite la signature "burst" de Gabagool.
*   **Fiabilité :** 90% (Data très claire).

### B. Pre-loading & Market Switching

#### 📍 Où : `live_arbitrage_bot.py` -> Preload Block (v. ligne 640)

*   **Actuel :** `if 0 < time_remaining <= 10 ...`
*   **Modification :**
    Passer le seuil à **60 secondes**.
    `if 0 < time_remaining <= 60 ...`
*   **Pourquoi :** Gabagool trade souvent dès la seconde 0 ou -1. 10s est trop court pour établir une connexion WS stable.
*   **Risque :** Plus de consommation CPU/Bande passante pendant la minute de chevauchement.

### C. Le Cœur : Shift Inventory Neutral -> Momentum

#### 📍 Où : `paper_trader.py` -> `decide()` (v. ligne 250)

*   **Actuel :**
    ```python
    # 2) Si une seule jambe -> HEDGE
    if (pos.qty_yes > 0): ... return "BUY_NO" (hedge)
    ```
*   **Modification :** **Remplacer totalement ce bloc.**
    Nouvelle logique "Directional Conviction" :
    1. Calculer `imbalance = qty_yes - qty_no`.
    2. Si `imbalance > 0` (Long YES):
       - Si `price_yes < CHEAP_YES` ou `edge > MIN`: **BUY YES** (Aggressive Add).
       - Interdire BUY NO (sauf si `worst_case_pnl < HARD_STOP_LOSS`).
    3. Si `imbalance < 0` (Long NO):
       - Miroir (Buy NO).
*   **Pourquoi :** C'est l'essence de la stratégie. On parie que le déséquilibre initial avait raison.
*   **Fiabilité :** 80% (Inférence forte du scaling unidirectionnel).

### D. Risk & Cutoff

#### 📍 Où : `paper_trader.py` -> `scale_allowed` et `config.py`

*   **Actuel :** Vérifie `pos.locked_profit_usd() >= -3.0`.
*   **Modification :**
    En mode directionnel, `locked_profit` sera toujours égal à `-exposure` (donc très négatif genre -30$).
    Il faut changer la garde en :
    `if pos.exposure_usd() > cfg.MAX_EXPOSURE_USD: return False`
    Et ajouter un **Hard Stop Loss** (ex: si le prix va contre nous de >20%, exit).
*   **Size Adaptation (Important):**
    *   `MAX_EXPOSURE_PER_MARKET`: **35.0$** (Contrainte small bankroll).
    *   Base Order : **3.0$** -> **5.0$** (Min ticket Polygon souvent 5$).
    *   Scale Factor : Max **1.5x** (vs 3x Gabagool) pour limiter la variance.

---

## 4. Schéma de Sizing "Small Bankroll"

Pour respecter la contrainte "Size Faible" mais "Logique Gabagool" :

1.  **Base Unit (`U`)** = 5$ (Minimum viable pour éviter les rejets "Min Shares").
2.  **Max Inventory (`I_max`)** = 35$ (~7 Unités).
3.  **Conviction Multiplier :**
    *   Entrée neutre : 1x `U`.
    *   Suivi de tendance (Add-on) : 1x `U` (Pas de martingale).
    *   Opportunité <0.10 ou >0.90 : 1.5x `U` (7.5$).
4.  **Protection Spirale :**
    *   Si `P_entry_avg` s'éloigne de >15% du prix actuel (trade perdant), **INTERDICTION de renchérir**. On ne "moyenne pas à la baisse" agressivement. On attend l'expiry ou le stop loss.

---

## 5. Validation Finale

Je suis prêt à appliquer ces changements dans l'ordre :
1.  `config.py` (Paramètres Risk & Sizing).
2.  `paper_trader.py` (Supression Hedge -> Injection Momentum Logic).
3.  `live_arbitrage_bot.py` (Duty Cycle "2-on-1-off" + Preload T-60s).

Confirmer le lancement.
