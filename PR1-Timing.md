# PR1: Gabagool Timing & Duty Cycle

## 1. Changements effectifs

Cette PR implémente uniquement la signature temporelle de Gabagool ("Rythme cardiaque"), sans toucher à la logique de décision (trading, hedging, risk).

### A. Duty Cycle (2-on-1-off)
**Concept :** Le bot fait une pause forcée si le temps restant (en minutes) correspond au pattern `(m-1)%3 == 0` (ex: 13, 10, 7, 4, 1).
**Fichier :** `live_arbitrage_bot.py`
**Fiabilité :** 95% (Pattern strict observé).

```python
# Avant
while True:
    now = time.time()
    # ... logic continues ...

# Après
while True:
    now = time.time()
    time_remaining = float(market["end_time"]) - now
    minutes_remaining = int(time_remaining / 60)

    # RULE: Pause minutes 13, 10, 7, 4, 1
    if (minutes_remaining - 1) % 3 == 0 and minutes_remaining > 0:
        print(f"[PAUSE] Gabagool Cycle (M{minutes_remaining}) time_remaining={time_remaining:.1f}s")
        time.sleep(1)
        continue
```

### B. Pre-loading (T-60s)
**Concept :** Démarrer la connexion WebSocket au marché suivant 60 secondes avant l'expiration du courant, pour être actif à la seconde 0.
**Fichier :** `live_arbitrage_bot.py`
**Fiabilité :** 90% (Nécessaire pour le trade T=0).

```python
# Avant
if 0 < time_remaining <= 10 and next_market is None ...

# Après
if 0 < time_remaining <= 60 and next_market is None ...
    # + Added Log
    print(f"[MARKET] Preloading next market at t_remaining={time_remaining:.1f}s")
```

## 2. Logs Attendus

### Pendant la Pause (ex: Minute 7)
```text
[PAUSE] Gabagool Cycle (M7) time_remaining=425.3s
[PAUSE] Gabagool Cycle (M7) time_remaining=424.3s
...
```

### Transition & Preload (ex: Fin Minute 1 -> Minute 0)
```text
[PAUSE] Gabagool Cycle (M1) time_remaining=60.5s
...
[MARKET] Preloading next market at t_remaining=59.8s: btc-updown-15m-1765580400
[MARKET] Preloading next: btc-updown-15m-1765580400 (starts in 60.1s)
...
[>>] BUY SIGNAL ... (Trading reprend car M0 est actif)
```

## 3. Plan de Test Manuel

1.  **Lancer le bot** sur un marché actif (ou simuler un timestamp).
2.  **Observer la console** :
    *   Si `time_remaining` est dans [780-840] (Minute 13), le bot doit spammer `[PAUSE]`.
    *   Si `time_remaining` est dans [720-780] (Minute 12), le bot doit afficher les prix/trades.
3.  **Attendre la fin** (ou simuler) :
    *   À T=60s, vérifier l'apparition du log `[MARKET] Preloading`.
