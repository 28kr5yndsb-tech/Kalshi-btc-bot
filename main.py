import os
import time
import threading
import requests
from http.server import HTTPServer, SimpleHTTPRequestHandler

# Dummy HTTP server to satisfy Render's web service port check
def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

# Start the dummy server in a background thread
threading.Thread(target=run_dummy_server, daemon=True).start()

# Load secrets from Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Configuration
POLL_INTERVAL = 60          # Check price every 60 seconds
MAX_HISTORY = 5             # Keep trailing 5 minutes of prices
DROP_THRESHOLD = 0.01       # Trigger alert on 1% drop (0.01 = 1%)

price_history = []

def send_telegram_alert(message):
    """Sends a notification message to your Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"[ERROR] Failed to send Telegram message: {response.text}")
    except Exception as e:
        print(f"[ERROR] Exception sending alert: {e}")

def get_btc_price():
    """Fetches real-time BTC spot price (USD)."""
    try:
        response = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=10)
        if response.status_code == 200:
            data = response.json()
            return float(data['data']['amount'])
    except Exception as e:
        print(f"[ERROR] Fetching BTC price failed: {e}")
    return None

def main():
    print("Kalshi BTC Dip Monitoring Bot starting...")
    send_telegram_alert("🟢 *Kalshi BTC Dip Bot is live!* Monitoring 5-min price drops.")
    
    while True:
        try:
            current_price = get_btc_price()
            
            if current_price:
                price_history.append(current_price)
                if len(price_history) > MAX_HISTORY:
                    price_history.pop(0)
                
                # Compare current price against the highest price in our trailing window
                max_price = max(price_history)
                drop_pct = (max_price - current_price) / max_price
                
                print(f"[LOG] Current BTC: ${current_price:,.2f} | 5-min Max: ${max_price:,.2f} | Drop: {drop_pct*100:.2f}%")
                
                if drop_pct >= DROP_THRESHOLD:
                    alert_msg = (
                        f"🚨 *BTC DIP ALERT!*\n\n"
                        f"• *Current Price:* ${current_price:,.2f}\n"
                        f"• *5-Min Peak:* ${max_price:,.2f}\n"
                        f"• *Drop Amount:* {drop_pct*100:.2f}%\n\n"
                        f"⚡ *Time to check Kalshi markets!*"
                    )
                    send_telegram_alert(alert_msg)
                    # Clear history after alert to prevent spamming duplicate alerts
                    price_history.clear()
            
            time.sleep(POLL_INTERVAL)
            
        except Exception as e:
            print(f"[ERROR] Main loop exception: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
