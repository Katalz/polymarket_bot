# Gabagool Adaptation: Code Verification & Logic Diff

Ce document confirme l'adaptation de la stratégie en mappant chaque changement proposé aux lignes exactes du code actuel.

## 1. Timing & Duty Cycle (Le "Rythme")

*   **📍 Localisation :** `live_arbitrage_bot.py`, fonction `run()`, début de la boucle `while True` (Lignes 635-637).
*   **🔁 AS-IS (Code Actuel) :**
    ```python
    while True:
        now = time.time()
        # ... Logique continue sans pause ...
    ```
    *Le bot interroge le WebSocket et le PriceStreamer aussi vite que possible (`SLEEP_BETWEEN_TICKS = 0.1`).*
*   **🔧 TO-BE (Cible) :**
    ```python
    while True:
        now = time.time()
        # Calcul du temps restant (basé sur market["end_time"])
        min_remaining = int(time_remaining / 60)
        
        # Filtre "2-on-1-off" : Pause si (min-1)%3 == 0 (ex: 13, 10, 7, 4, 1)
        if (min_remaining - 1) % 3 == 0 and min_remaining > 0:
             print(f"[PAUSE] Gabagool Cycle (M{min_remaining})")
             time.sleep(1) 
             continue
    ```
*   **📊 Fiabilité :** **95%**. Le pattern d'absence de trades dans les logs Gabagool est trop parfait pour être aléatoire.
*   **🔎 Test à vérifier :** Lancer le bot et vérifier qu'il affiche "[PAUSE]" et ne trade pas durant les minutes cibles.

## 2. Pre-loading (Anticipation)

*   **📍 Localisation :** `live_arbitrage_bot.py`, bloc "PRELOAD NEXT MARKET" (Lignes 640).
*   **🔁 AS-IS (Code Actuel) :**
    ```python
    if 0 < time_remaining <= 10 and next_market is None ...
    ```
    *Le bot commence à chercher le prochain marché seulement 10 secondes avant la fin.*
*   **🔧 TO-BE (Cible) :**
    ```python
    if 0 < time_remaining <= 60 and next_market is None ...
    ```
    *On avance le trigger à 60 secondes pour garantir la connexion WS.*
*   **📊 Fiabilité :** **90%**. Gabagool trade souvent à T=0 ou T-1s, impossible sans connexion préalable.
*   **🔎 Test à vérifier :** Log `[MARKET] Preloading next: ...` doit apparaître quand `time_remaining` est ~59.9s.

## 3. Inventory & Momentum (Le "Cœur")

*   **📍 Localisation :** `paper_trader.py`, méthode `decide()`, Bloc "Si une seule jambe" (Lignes 251-283).
*   **🔁 AS-IS (Code Actuel - HEDGE) :**
    ```python
    # Ligne 251
    if (pos.qty_yes > 0) ^ (pos.qty_no > 0):
        if pos.qty_yes > 0:
             # ... calcul missing_qty ...
             return Decision("BUY_NO", ...) # FORCE LE HEDGE
    ```
    *Le bot refuse d'accumuler du risque directionnel.*
*   **🔧 TO-BE (Cible - MOMENTUM) :**
    ```python
    # Remplacement complet du bloc L251-283
    imbalance = pos.qty_yes - pos.qty_no
    
    # Si Long YES (imbal > MIN) -> Check BUY YES (Add-on)
    if imbalance > self.cfg.EPS_QTY:
         # Si prix intéressant ou edge > seuil -> BUY YES
         # Interdiction BUY NO (Hedge bloqué)
         
    # Si Long NO -> Check BUY NO
    ```
*   **📊 Fiabilité :** **85%**. L'analyse des quantums de Gabagool montre des séries de "Buy YES" consécutifs augmentant l'exposition, pas d'alternance.
*   **🔎 Test à vérifier :** Lancer un scénario paper où on a 10 YES. Le prochain trade doit être "BUY YES" (si edge ok), jamais "BUY NO".

## 4. Risk / Cutoff (La "Sécurité")

*   **📍 Localisation :** `paper_trader.py`, `scale_allowed` (Lignes 183-189) + `config.py`.
*   **🔁 AS-IS (Code Actuel - DELTA NEUTRE) :**
    ```python
    # Ligne 183 paper_trader.py
    if pos.has_two_legs():
         if pos.locked_profit_usd() >= self.cfg.LOCK_KEEP_USD: # Vérifie le Lock PnL
              return True
    ```
    *Bloque tout trade si le "Locked PnL" est trop négatif (ce qui est TOUJOURS le cas en directionnel).*
*   **🔧 TO-BE (Cible - EXPOSURE CAP) :**
    ```python
    # Dans scale_allowed, supprimer la check has_two_legs et locked_profit
    
    # Nouvelle garde : 
    # 1. Exposure Cap
    if pos.exposure_usd() > self.cfg.MAX_EXPOSURE_USD: return False
    
    # 2. (Optionnel) Stop Loss Spirale
    # Si coût moyen diverge trop du prix actuel -> Stop Opening
    ```
*   ** Adaptation Size :** Changer `MAX_EXPOSURE_PER_MARKET` dans `config.py` de 35$ à une valeur confirmée (35$ est ok pour small roll).
*   **📊 Fiabilité :** **90%**. Un trader directionnel ne regarde pas son "Locked Profit" (qui est l'arbitrage pur) mais son PnL latent + Exposure.
*   **🔎 Test à vérifier :** Le bot doit continuer à trader même si `locked_pnl` affiche -20$, tant que `exposure` < 35$.

## 5. Conclusion du Diff

Le plan est solide et correspond exactement aux zones de friction identifiées dans le code.
*   Les lignes 251-283 de `paper_trader.py` sont le **bloquant principal**.
*   Les lignes 635+ de `live_arbitrage_bot.py` sont le **levier de performance** (timing).

Je suis prêt à appliquer ces changements.
