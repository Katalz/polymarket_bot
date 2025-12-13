#!/usr/bin/env python3
"""
Bot Polymarket Paper Trading - Pair Cost Arbitrage Strategy

Ce script simule une stratégie de pair-cost arbitrage sur les marchés Bitcoin 15 minutes.
AUCUN ordre réel n'est envoyé - tout est en simulation uniquement.

NOUVELLE ARCHITECTURE:
- Gamma API uniquement pour métadonnées (token IDs)
- CLOB API pour prix temps réel
"""

import time
import csv
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

from config import (
    EVENT_SLUG,
    TICK_INTERVAL_SECONDS,
    MAX_RUNTIME_MINUTES,
    LOG_FILE,
    LOG_HEADERS,
    MARKETS,
    SESSION_DURATION_MINUTES,
    MARKET_SUMMARY_FILE,
    MARKET_SUMMARY_HEADERS
)
import requests
import json
from paper_trader import PaperTrader
from gamma_client import gamma_client
from clob_client import clob_client
from market_session import MarketSession
from market_discovery import (
    get_active_btc_15min_markets,
    get_current_active_market,
    get_next_market_in_sequence,
    wait_for_next_market
)


def initialize_csv_log(log_file: str, headers: list) -> None:
    """Initialise le fichier CSV de log avec les headers."""
    try:
        with open(log_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
        print(f"Fichier de log initialisé: {log_file}")
    except Exception as e:
        print(f"Erreur initialisation log CSV: {e}")
        raise


def log_to_csv(log_file: str, data: Dict[str, Any]) -> None:
    """Ajoute une entrée au fichier CSV de log."""
    try:
        with open(log_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(data.keys()))
            writer.writerow(data)
    except Exception as e:
        print(f"Erreur écriture log CSV: {e}")


def format_timestamp(timestamp: float) -> str:
    """Formate un timestamp en string lisible."""
    return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')


def initialize_market_summary_csv() -> None:
    """Initialise le fichier CSV de résumé des marchés."""
    try:
        # Vérifier si le fichier existe déjà
        import os
        if not os.path.exists(MARKET_SUMMARY_FILE):
            with open(MARKET_SUMMARY_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=MARKET_SUMMARY_HEADERS)
                writer.writeheader()
            print(f"Fichier de résumé marchés initialisé: {MARKET_SUMMARY_FILE}")
    except Exception as e:
        print(f"Erreur initialisation market_summary.csv: {e}")
        raise


def log_market_summary(session) -> None:
    """
    Ajoute une entrée dans le fichier market_summary.csv.

    Args:
        session: Instance de MarketSession terminée
    """
    try:
        # Récupérer les données de résumé
        summary_data = session.get_market_summary_row()

        # Écrire dans le CSV
        with open(MARKET_SUMMARY_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=MARKET_SUMMARY_HEADERS)
            writer.writerow(summary_data)

        # Afficher un résumé console
        pnl_yes, pnl_no = session.compute_terminal_pnl()
        print(f"📊 Session {session.market_id} terminée:")
        print(f"   Trades: {len(session.trades)} | Max exposure: {session.max_exposure:.1f}")
        print(".2f")
        print(".2f")

    except Exception as e:
        print(f"Erreur écriture market_summary.csv: {e}")


def stream_ticks(market_id: str, yes_token_id: str, no_token_id: str, logger):
    """
    Générateur qui stream les ticks pour un marché donné.
    Utilise la même logique que l'ancien système.

    Args:
        market_id: Identifiant du marché (pour compatibilité future)
        yes_token_id: Token ID pour YES
        no_token_id: Token ID pour NO
        logger: Logger pour les erreurs

    Yields:
        Dict avec les données du tick (price_yes, price_no, timestamp)
    """
    tick_count = 0

    try:
        while True:
            tick_start = time.time()
            tick_count += 1

            # Récupération des prix frais depuis CLOB
            try:
                price_yes, price_no = clob_client.get_yes_no_prices_from_clob(yes_token_id, no_token_id)
            except Exception as e:
                logger.error(f"Échec récupération prix CLOB tick {tick_count}: {e}")
                time.sleep(TICK_INTERVAL_SECONDS)
                continue

            # Créer le tick
            tick = {
                "price_yes": price_yes,
                "price_no": price_no,
                "timestamp": time.time()
            }

            yield tick

            # Pause jusqu'au prochain tick
            elapsed = time.time() - tick_start
            sleep_time = max(0, TICK_INTERVAL_SECONDS - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"\nStream interrompu après {tick_count} ticks")
        return


def main():
    """Fonction principale avec découverte automatique des marchés BTC 15 min."""
    print("=== BOT Polymarket Auto-Discovery Multi-Market ===")
    print("Detection automatique des marches BTC 15 min actifs")
    print(f"Duree par session: {SESSION_DURATION_MINUTES} minutes")
    print(f"Resume marches: {MARKET_SUMMARY_FILE}")
    print()

    # Configuration du logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)

    # Initialisation du fichier de résumé des marchés
    initialize_market_summary_csv()

    try:
        # 1. Découverte automatique des marchés BTC 15 min
        print("Recherche des marches BTC 15 min actifs...")
        all_markets = get_active_btc_15min_markets()

        if not all_markets:
            print("Erreur Aucun marché BTC 15 min trouvé. Arrêt du bot.")
            return

        # 2. Identifier le marché actuellement actif
        current_market = get_current_active_market(all_markets)

        if not current_market:
            print("⚠️ Aucun marché actif trouvé. Attente du prochain marché...")

            # Trier les marchés par timestamp et attendre le premier disponible
            sorted_markets = sorted(all_markets, key=lambda x: x["start_time"])
            for market in sorted_markets:
                if time.time() < market["start_time"]:
                    print(f"🎯 Prochain marché disponible: {market['slug']}")
                    print(f"   Démarrage: {time.strftime('%H:%M:%S', time.localtime(market['start_time']))}")

                    # Attendre que ce marché devienne actif
                    while time.time() < market["start_time"]:
                        remaining = int(market["start_time"] - time.time())
                        if remaining % 60 == 0 and remaining > 0:  # Afficher chaque minute
                            print(f"   Session {remaining // 60} minutes restantes...")
                        time.sleep(10)

                    current_market = market
                    print(f"🚀 Marché {current_market['slug']} devient actif !")
                    break

            if not current_market:
                print("Erreur Aucun marché disponible. Arrêt du bot.")
                return

        # 3. Boucle principale : traiter le marché actif puis passer au suivant
        markets_processed = 0

        while current_market:
            markets_processed += 1
            market_slug = current_market["slug"]

            print(f"\n{'='*70}")
            print(f"MARCHE {markets_processed}: {market_slug}")
            print(f"{'='*70}")
            print(f"Titre: {current_market.get('title', 'N/A')}")
            print(f"Periode: {time.strftime('%H:%M:%S', time.localtime(current_market['start_time']))} - {time.strftime('%H:%M:%S', time.localtime(current_market['end_time']))}")

            try:
                # Récupération des token IDs pour ce marché
                print("🔑 Récupération des token IDs...")
                tokens_info = gamma_client.get_market_tokens_from_event_slug(market_slug)

                if not tokens_info:
                    print(f"Erreur ÉCHEC: Impossible de récupérer les token IDs pour {market_slug}")
                    # Passer au marché suivant
                    current_market = get_next_market_in_sequence(all_markets, market_slug)
                    if current_market:
                        current_market = wait_for_next_market(all_markets, market_slug)
                    continue

                yes_token_id = tokens_info["yes_token_id"]
                no_token_id = tokens_info["no_token_id"]
                print(f"Token IDs recuperes Token IDs récupérés: YES={yes_token_id}, NO={no_token_id}")

                # Création de la session de marché
                session = MarketSession(market_slug)
                print(f"📊 Session créée: {format_timestamp(session.start_timestamp)} -> {format_timestamp(session.end_timestamp)}")

                # Initialisation du log détaillé pour cette session
                session_log_file = f"{LOG_FILE.rsplit('.', 1)[0]}_{market_slug}.csv"
                initialize_csv_log(session_log_file, LOG_HEADERS)
                print(f"📝 Log détaillé: {session_log_file}")

                # Boucle de streaming des ticks pour cette session
                print(f"🎯 Démarrage du trading pour {market_slug}...")
                tick_count = 0

                for tick in stream_ticks(market_slug, yes_token_id, no_token_id, logger):
                    tick_count += 1

                    # Traiter le tick dans la session
                    action, decision_reason = session.on_tick(tick)

                    # Log du tick détaillé
                    log_data = session.trader.get_state_dict(
                        price_yes=tick["price_yes"],
                        price_no=tick["price_no"],
                        current_timestamp=tick["timestamp"],
                        market_start_timestamp=session.start_timestamp
                    )
                    log_data["action"] = action
                    log_data["decision_reason"] = decision_reason
                    log_to_csv(session_log_file, log_data)

                    # Affichage du statut (toutes les 10 ticks)
                    if tick_count % 10 == 0:
                        pair_cost = session.trader.state.pair_cost
                        exposure = session.trader.state.total_exposure

                        status = f"Tick {tick_count} | YES:{tick['price_yes']:.4f} NO:{tick['price_no']:.4f} | "
                        status += f"Exp:{exposure:.1f} | Qty Y:{session.trader.state.qty_yes:.2f} N:{session.trader.state.qty_no:.2f} | "
                        if pair_cost:
                            status += f"PairCost:{pair_cost:.4f}"
                        status += f" | Action:{action}"

                        print(status)

                    # Vérifier si la session est terminée
                    if session.is_finished(tick["timestamp"]):
                        print(f"\nSession Session {market_slug} terminée (durée écoulée)")
                        break

                # Calcul du P&L terminal et logging
                pnl_yes, pnl_no = session.compute_terminal_pnl()
                log_market_summary(session)

                # Résumé de session
                print(f"📈 Session {market_slug} - Résumé:")
                print(f"   Ticks: {tick_count}")
                print(f"   Trades: {len(session.trades)}")
                print(f"   Max exposure: {session.max_exposure:.1f}")
                print(".2f")
                print(".2f")

            except KeyboardInterrupt:
                print(f"\n🛑 Interruption utilisateur détectée pendant {market_slug}")
                print("🔄 Passage automatique au marché suivant...")
                # Ne pas casser la boucle, continuer au marché suivant
            except Exception as e:
                print(f"Erreur Erreur pendant {market_slug}: {e}")
                logger.error(f"Erreur marché {market_slug}: {e}")

            # Passer au marché suivant dans la séquence
            print(f"\n🔄 Recherche du prochain marché après {market_slug}...")
            next_market = get_next_market_in_sequence(all_markets, market_slug)

            if next_market:
                print(f"📋 Prochain marché trouvé: {next_market['slug']}")
                current_market = wait_for_next_market(all_markets, market_slug)
            else:
                print("🏁 Aucun marché suivant dans la séquence")
                current_market = None

    except KeyboardInterrupt:
        print("\n🛑 Arrêt forcé du bot par l'utilisateur")

    # Résumé global final
    print(f"\n{'='*70}")
    print("📊 RÉSUMÉ GLOBAL FINAL")
    print(f"{'='*70}")
    print(f"Marchés traités: {markets_processed if 'markets_processed' in locals() else 0}")
    print(f"Fichier résumé: {MARKET_SUMMARY_FILE}")
    print(f"Logs détaillés: {LOG_FILE.rsplit('.', 1)[0]}_[market_slug].csv")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
