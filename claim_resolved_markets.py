import os
import sys
import time
import json
import requests
import traceback
from datetime import datetime, timezone
from dotenv import load_dotenv

# Try importing py_clob_client
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
except ImportError:
    print("[!] py_clob_client not installed. Please install it.")
    sys.exit(1)

# --- CONFIGURATION ---
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
CLAIM_STATE_FILE = "claimed_markets.json"
LOOKBACK_HOURS = 24  # Look back 24h
SCAN_INTERVAL_SEC = 900 # 15m markets

def setup_client():
    load_dotenv()
    pk = os.getenv("POLYGON_PRIVATE_KEY")
    funder = os.getenv("POLYMARKET_PROXY_ADDRESS")
    
    if not pk:
        print("[!] No POLYGON_PRIVATE_KEY found in .env")
        return None

    try:
        creds = ApiCreds(
            api_key=os.getenv("POLYMARKET_CLOB_API_KEY"),
            api_secret=os.getenv("POLYMARKET_CLOB_API_SECRET"),
            api_passphrase=os.getenv("POLYMARKET_CLOB_API_PASSPHRASE")
        )
        client = ClobClient(
            host=HOST, 
            key=pk, 
            chain_id=CHAIN_ID, 
            signature_type=2, 
            funder=funder, 
            creds=creds
        )
        print("[AUTH] ClobClient Connected")
        return client
    except Exception as e:
        print(f"[!] Auth Error: {e}")
        return None

def load_claimed_state():
    if os.path.exists(CLAIM_STATE_FILE):
        try:
            with open(CLAIM_STATE_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_claimed_state(state):
    try:
        with open(CLAIM_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[!] Failed to save state: {e}")

def get_market_status(slug):
    url = f"{GAMMA_API_BASE}/markets?slug={slug}"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 404: return None
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data[0] if data else None
        return data
    except Exception as e:
        # Don't spam warnings for future markets not yet created
        # print(f"[WARN] API Fetch failed for {slug}: {e}")
        return None

# Helper for Web3 Redemption
try:
    from web3 import Web3
    from web3.middleware import geth_poa_middleware
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False

# Minimal ABI for CTF Exchange (Position Redemption)
# redeemPositions(address collateralToken, bytes32 parentCollectionId, bytes32 conditionId, uint256[] indexSets)
CTF_EXCHANGE_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"}
        ],
        "name": "redeemPositions",
        "outputs": [],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

def attempt_web3_redemption(client, market_data):
    """
    Attempt to redeem positions via Web3 if available.
    """
    if not WEB3_AVAILABLE:
        print("[CLAIM-FAIL] Web3 library not found. Cannot auto-redeem.")
        return 0.0, False

    try:
        slug = market_data.get("slug")
        condition_id = market_data.get("conditionId")
        
        # Get addresses from client
        exchange_addr = client.get_exchange_address()
        collateral_addr = client.get_collateral_address() # USDC
        
        # Setup Web3
        pk = os.getenv("POLYGON_PRIVATE_KEY")
        rpc_url = "https://polygon-rpc.com" # Default or from env
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        
        account = w3.eth.account.from_key(pk)
        print(f"[CLAIM-WEB3] Connected: {account.address}")
        
        contract = w3.eth.contract(address=exchange_addr, abi=CTF_EXCHANGE_ABI)
        
        # Parameters for Binary 
        parent_collection_id = "0x" + "0"*64 # Always bytes32(0) for simpler markets
        index_sets = [1, 2] # Binary markets usually have outcomes [0, 1] -> index sets [1, 2] (bitmask)
        
        # Build Tx
        print(f"[CLAIM-WEB3] Building redeem tx for {slug}...")
        tx = contract.functions.redeemPositions(
            collateral_addr,
            bytes.fromhex(parent_collection_id[2:]),
            bytes.fromhex(condition_id[2:]),
            index_sets
        ).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 200000,
            'gasPrice': w3.eth.gas_price
        })
        
        # Sign & Send
        signed_tx = w3.eth.account.sign_transaction(tx, pk)
        
        # Robust access to raw transaction bytes
        raw_tx = getattr(signed_tx, 'rawTransaction', None)
        if raw_tx is None:
            raw_tx = getattr(signed_tx, 'raw_transaction', None)
            
        if raw_tx is None:
            # Fallback for dict-like objects
            if isinstance(signed_tx, dict):
                 raw_tx = signed_tx.get('rawTransaction') or signed_tx.get('raw_transaction')
        
        if raw_tx is None:
            print(f"[CLAIM-Error] SignedTransaction object has no rawTransaction attribute. Attributes: {dir(signed_tx)}")
            return 0.0, False

        tx_hash = w3.eth.send_raw_transaction(raw_tx)
        print(f"[CLAIM-WEB3] Tx Sent! Hash: {tx_hash.hex()}")
        
        # Wait for receipt
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt.status == 1:
            print("[CLAIM-WEB3] SUCCESS: Redemption Confirmed.")
            return 1.0, True # Assume some success
        else:
            print("[CLAIM-WEB3] FAILED: Tx Reverted.")
            return 0.0, False

    except Exception as e:
        print(f"[CLAIM-Error] Web3 Error: {e}")
        return 0.0, False

def claim_market(client, market_data):
    slug = market_data.get("slug")
    print(f"[CLAIM] Checking {slug}...")
    
    # Try Web3 method
    if WEB3_AVAILABLE:
        return attempt_web3_redemption(client, market_data)
    else:
        print(f"[CLAIM-ACTION] Market {slug} is RESOLVED. Please redeem via Polymarket UI/Profile.")
        return 0.0, True  # Mark handled to avoid spamming log, but value 0.0


def scan_and_claim():
    client = setup_client()
    if not client:
        return

    claimed_state = load_claimed_state()
    
    now = datetime.now(timezone.utc)
    base_ts = int(now.timestamp())
    base_ts = (base_ts // SCAN_INTERVAL_SEC) * SCAN_INTERVAL_SEC
    
    scanned = 0
    claimed_count = 0
    total_recovered = 0.0
    
    print(f"\n[JOB] Starting Claim Scan | {now.isoformat()}")
    print("-" * 60)

    # Scan last N hours
    for i in range(1, (LOOKBACK_HOURS * 4) + 1):
        ts = base_ts - (i * SCAN_INTERVAL_SEC)
        slug = f"btc-updown-15m-{ts}"
        
        # Check if already processed
        if slug in claimed_state:
            # print(f"[SKIP] {slug} -> already claimed")
            continue
            
        scanned += 1
        
        # Rate limit polite
        time.sleep(0.2)
        
        market = get_market_status(slug)
        if not market:
            continue
            
        # Check resolution status
        # 'closed' is often the best indicator for standard resolution
        is_closed = market.get("closed", False)
        
        if not is_closed:
            continue
            
        # It is closed. Attempt claim.
        try:
            amount, success = claim_market(client, market)
            
            if success:
                claimed_state[slug] = {
                    "timestamp": int(time.time()),
                    "payout": amount,
                    "resolved": True
                }
                claimed_count += 1
                total_recovered += amount
                save_claimed_state(claimed_state)
                
        except Exception as e:
            print(f"[ERROR] Failed to claim {slug}: {e}")
            traceback.print_exc()

    print("-" * 60)
    print(f"[SUMMARY] scanned={scanned} claimed={claimed_count} total_usd_recovered={total_recovered}")

if __name__ == "__main__":
    print("[INIT] Claim Job Service Started (Runs every 30 min)")
    while True:
        try:
            scan_and_claim()
        except KeyboardInterrupt:
            print("[STOP] Stopping Claim Job.")
            sys.exit(0)
        except Exception as e:
            print(f"[CRITICAL] Job crashed: {e}")
            traceback.print_exc()
        
        print(f"[SLEEP] Waking up in 30 minutes...")
        time.sleep(1800)  # 30 minutes
