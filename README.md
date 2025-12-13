# Polymarket Paper Trading Bot - Multi-Market Sessions

Simulateur de trading pair-cost arbitrage sur les marchés Bitcoin 15 minutes de Polymarket avec système de sessions multi-marchés. **AUCUN ordre réel n'est envoyé** - tout est en paper trading uniquement.

## 🏗️ Architecture

- **Gamma API** : Récupération des métadonnées (token IDs)
- **CLOB API** : Prix temps réel et données de marché
- **MarketSession** : Gestion des sessions de trading de 15 minutes par marché
- **Paper Trading** : Simulation complète sans risque

## ✨ Nouvelles Fonctionnalités (v3.0)

- **🔍 Découverte automatique des marchés** : Détecte tous les marchés BTC 15 min actifs
- **🎯 Identification du marché actif** : Trouve automatiquement le marché en cours
- **⏳ Sessions de 15 minutes** : Durée fixe et contrôlée par marché
- **🔄 Passage automatique** : Du marché actif au suivant dans la séquence
- **📊 Multi-marchés automatique** : Traite tous les marchés sans intervention
- **📝 Logging avancé** : `market_summary.csv` + logs détaillés par marché
- **💰 Calcul P&L terminal** : Projection des gains YES/NO à la résolution

## 🎯 Stratégie de Trading Prudente

Le bot implémente une stratégie de **market-making asymétrique** avec contraintes strictes :

### Logique de Trading
- **Edge minimum** : 1% requis (`1.0 - pair_cost >= 0.01`)
- **Exposition max** : 60 USDC par marché (vs 200 auparavant)
- **Perte max** : -10 USDC par marché (worst case P&L)
- **Arrêt automatique** : Quand profit théorique ≥ 20 USDC
- **Construction systématique** : De vrais pairs d'arbitrage

### Contraintes Avancées
- **Deuxième jambe** : Achète NO même si pas "cheap" pour compléter le pair
- **DCA limité** : Évite d'acheter YES quand `price_yes < 0.20`
- **Équilibrage** : Maintient ratio d'imbalance < 0.4

## 📊 Fichiers de Log

1. **`market_summary.csv`** : Résumé par marché
   ```csv
   market_id,start_timestamp,end_timestamp,final_qty_yes,final_avg_yes,final_qty_no,final_avg_no,pnl_if_yes,pnl_if_no,max_exposure,nb_trades
   ```

2. **`paper_trading_log_[market_id].csv`** : Log détaillé tick-par-tick pour chaque marché

## 🚀 Installation

```bash
pip install -r requirements.txt
```

## 🎮 Utilisation

```bash
python main.py
```

## 🎯 Mode de Fonctionnement Automatique

Le bot fonctionne maintenant en **mode entièrement automatique** :

### 1. **Découverte des Marchés** 🔍
- Interroge l'API Polymarket pour trouver tous les marchés BTC 15 min actifs
- Identifie automatiquement le marché actuellement en cours
- Si aucun marché actif, attend le prochain dans la séquence

### 2. **Session Active** 📊
- Crée une session de 15 minutes pour le marché actif
- Trade automatiquement selon la stratégie pair-cost
- Log tous les ticks dans un fichier dédié au marché

### 3. **Passage Automatique** 🔄
- À la fin de la session (15 minutes écoulées)
- Calcule le P&L terminal (YES/NO gagne)
- Sauvegarde le résumé dans `market_summary.csv`
- Passe automatiquement au marché suivant
- Répète jusqu'à épuisement de tous les marchés actifs

### 4. **Logging Complet** 📝
- **Par marché** : `paper_trading_log_[slug].csv` (ticks détaillés)
- **Global** : `market_summary.csv` (résumé de toutes les sessions)

## ⚙️ Configuration

Modifiez `config.py` selon vos besoins :

```python
# Liste des marchés à trader automatiquement (15 minutes chacun)
MARKETS = [
    "btc-updown-15m-1765324800",  # Premier marché
    "btc-updown-15m-1765328400",  # Deuxième marché (+1h)
    "btc-updown-15m-1765332000",  # Troisième marché (+2h)
    # Ajoutez autant de marchés que souhaité
]

# Paramètres de trading prudents
MAX_EXPOSURE_PER_MARKET = 60.0    # Exposition max par marché
MIN_EDGE = 0.01                   # Edge minimum requis
MAX_LOSS_PER_MARKET = -10.0       # Perte max autorisée
TARGET_THEORETICAL_PROFIT = 20.0  # Arrêt quand profit >= 20

# Paramètres de session
SESSION_DURATION_MINUTES = 15      # Durée par session
TICK_INTERVAL_SECONDS = 2          # Intervalle entre ticks
```

### 🔍 Comment trouver les slugs de marchés actifs

1. **Allez sur Polymarket** : https://polymarket.com
2. **Cherchez "Bitcoin Up or Down"** dans la barre de recherche
3. **Cliquez sur un marché 15 minutes actif**
4. **Copiez le slug depuis l'URL** : `polymarket.com/event/[SLUG]`
5. **Ajoutez-le à la liste MARKETS** dans `config.py`

**Exemple d'URLs** :
- `polymarket.com/event/btc-updown-15m-1765324800`
- Slug à extraire : `btc-updown-15m-1765324800`

## 📈 Exemple de Sortie Automatique

```
=== 🤖 Bot Polymarket Auto-Discovery Multi-Market ===

🔍 Recherche des marchés BTC 15 min actifs...
✅ Trouvé 3 marchés BTC 15 min
   btc-updown-15m-1765324800 - ACTIVE
   btc-updown-15m-1765328400 - INACTIVE
   btc-updown-15m-1765332000 - INACTIVE

🎯 Marché actif trouvé: btc-updown-15m-1765324800
   Début: 10:30:00
   Fin: 10:45:00

======================================================================
🏛️ MARCHÉ 1/3: btc-updown-15m-1765324800
======================================================================
📊 Session créée: 2025-12-10 10:30:00 -> 2025-12-10 10:45:00
Tick 10 | YES:0.5240 NO:0.4760 | Exp:20.0 | Qty Y:20.00 N:20.83 | PairCost:0.980 | Action:HOLD

⏰ Session btc-updown-15m-1765324800 terminée (durée écoulée)
📊 Session btc-updown-15m-1765324800 terminée:
   Trades: 2 | Max exposure: 20.0
   P&L si YES gagne: 0.00 USDC
   P&L si NO gagne: 1.28 USDC

🔄 Recherche du prochain marché après btc-updown-15m-1765324800...
📋 Prochain marché trouvé: btc-updown-15m-1765328400
🚀 Marché btc-updown-15m-1765328400 devient actif !

RÉSUMÉ GLOBAL FINAL
```

## 🎮 Scripts de Démonstration

Testez les fonctionnalités sans attendre de vrais marchés :

```bash
# Démonstration du système de découverte automatique
python demo_auto_discovery.py

# Démonstration du système multi-marchés avec sessions
python demo_multi_markets.py
```
Marchés traités: 1
Fichier résumé: market_summary.csv
```

## 🛡️ Sécurité

- ✅ **Aucune clé API requise**
- ✅ **Aucun ordre réel envoyé**
- ✅ **Simulation complète uniquement**
- ✅ **Gestion d'erreurs réseau robuste**
- ✅ **Contraintes de risque strictes**

## 📁 Structure du Code

- `config.py` : Configuration centralisée
- `paper_trader.py` : Classe `PaperTrader` avec logique de trading
- `market_session.py` : Classe `MarketSession` pour gestion des sessions
- `main.py` : Boucle principale multi-marchés
- `requirements.txt` : Dépendances Python

## 🔌 API Utilisées

- **Gamma API** (`https://gamma-api.polymarket.com`) : Métadonnées des marchés
- **CLOB API** (`https://clob.polymarket.com`) : Prix temps réel
- Endpoints utilisés :
  - `GET /events/slug/{EVENT_SLUG}` : Infos de l'événement
  - Prix CLOB temps réel pour token IDs

## 🎯 TODO Important

1. **Mettez à jour la liste MARKETS** dans `config.py` avec les marchés BTC 15 minutes actifs
2. **Vérifiez les paramètres de risque** selon votre tolérance
3. **Testez avec de courtes sessions** avant déploiement prolongé

## 🆕 Changements v2.0

- ✅ Système de sessions 15 minutes par marché
- ✅ Multi-marchés automatique
- ✅ Logging `market_summary.csv`
- ✅ Paramètres de risque plus prudents (60 USDC max)
- ✅ Logique d'achat de deuxième jambe
- ✅ Limitation DCA sur probabilités basses
- ✅ Arrêt automatique sur profit cible
# polymarket_bot
# polymarket_bot
