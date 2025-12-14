from py_clob_client.client import ClobClient

host = "https://clob.polymarket.com"
client = ClobClient(host)

# 1h Market Token ID (from before)
token_id = "69637691359069464095264211651718892550245036339262820710955480396154077335252"

print(f"Fetching full book for {token_id}...")
book = client.get_order_book(token_id)

print(f"Total Bids: {len(book.bids)}")
if book.bids:
    # Print top 5 and bottom 5
    print("Top 5 Bids:")
    for b in book.bids[:5]:
        print(f"  {b.price} (size {b.size})")
    
    print("Checking for any Bid > 0.02...")
    found = False
    for b in book.bids:
        if float(b.price) > 0.02:
            print(f"  FOUND: {b.price} (size {b.size})")
            found = True
    if not found: print("  No Bids > 0.02 found.")

print(f"Total Asks: {len(book.asks)}")
if book.asks:
    print("Top 5 Asks:")
    for a in book.asks[:5]:
        print(f"  {a.price} (size {a.size})")
