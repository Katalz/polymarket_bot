# Configuration Actuelle : Bot Gabagool "Sniper" ($140)

> **Statut**: Pilot ($140 Bankroll)
> **Mode**: Multi-Market Strict (1 Active Position)
> **Fichier**: `config.py`

## 1. Gestion du Capital & Risque
| Paramètre | Valeur | Description |
| :--- | :--- | :--- |
| `MAX_EXPOSURE_USD` | **$16.50** | Exposition max par marché (autorise ~3 tirs de $5). |
| `BASE_ORDER_USD` | **$5.00** | Taille du ticket d'entrée (Limit Order). |
| `DAILY_LOSS_LIMIT` | **$35.00** | Arrêt du bot si perte journalière > $35 (25% bankroll). |
| `MAX_CONCURRENT` | **1** | Strictement 1 seul marché actif à la fois. |

## 2. Scanner & Sélection (No-Fly)
| Paramètre | Valeur | Description |
| :--- | :--- | :--- |
| `MARKET_UNIVERSE` | `["BTC", "ETH", "SOL", "XRP"]` | Actifs surveillés (15 minutes). |
| `NO_FLY_MAX_SPREAD` | **0.04** | Ignorer marché si Spread > 4 cts (ex: 50-54). |
| `NO_FLY_MIN_LIQ` | **$50** | Ignorer si profondeur top 5 ticks < $50. |
| `TIME_WINDOW` | **T-13m à T-3m** | Entrées autorisées seulement entre 180s et 850s restants. |
| `NO_ENTRY_TIMEOUT` | **45s** | Si Lock sans position pendant 45s -> Unlock & Rescan. |

## 3. Logique de Trading (Entrée/Sortie)
| Paramètre | Valeur | Description |
| :--- | :--- | :--- |
| `CHEAP_YES_MAX` | **0.49** | N'achète YES que si prix < 0.49 (Discount). |
| `SCALE_EDGE_MIN` | **1.5%** | Seuil de rentabilité théorique pour entrer. |
| `TAKE_PROFIT_PCT` | **+25%** | Sortie complète si ROI position > 25%. |
| `FORCE_EXIT_SEC` | **T-30s** | Vente forcée (Market) à 30s de l'expiration. |
| `TAKER_SLIPPAGE` | **0.03** | Tolérance de glissement pour ordres Market/Limit agressifs. |

## 4. Modes Spéciaux
- **DRY_RUN_SCAN_ONLY**: `True` (Actuel)
  - Le bot scanne, score et log, mais ne **verrouille jamais** de marché et n'envoie **aucun ordre**.
  - *Action requise:* Passer à `False` pour activer le trading réel.
- **DUTY CYCLE**: Actif
  - Pause de trading aux minutes 1, 4, 7, 10, 13 (Pattern Gabagool).
