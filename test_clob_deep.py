from py_clob_client.client import ClobClient

host = "https://clob.polymarket.com"
print(f"Connecting to {host}...")
client = ClobClient(host)

# 1h Market Token ID
token_id = "69637691359069464095264211651718892550245036339262820710955480396154077335252"

print(f"Checking Token: {token_id}")

try:
    # 1. Last Trade
    last_trade = client.get_last_trade_price(token_id)
    print(f"Last Trade Price: {last_trade}")

    # 2. Book
    book = client.get_order_book(token_id)
    if book.bids: print(f"Best Bid: {book.bids[0].price}")
    if book.asks: print(f"Best Ask: {book.asks[0].price}")
    
except Exception as e:
    print(f"Error: {e}")
