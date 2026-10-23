import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
import time
import requests

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

# Check interval (in seconds)
POLL_INTERVAL = 60

# Tracking price history for drop calculation
price_history = []
MAX_HISTORY = 5  # Trailing 5 samples (5 minutes)
DROP_THRESHOLD = 0.01  # Example: 1% drop threshold

def send_telegram_alert(message):
    """Sends a notification message to your Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables.")
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
        print(f"[ERROR] Exception occurred while sending Telegram alert: {e}")

def main():
    print("Kalshi BTC bot started successfully.")
    send_telegram_alert("🟢 *Kalshi BTC Bot is online and monitoring markets!*")
    
    while True:
        try:
            # Add your Kalshi polling and monitoring logic here
            print("Checking Kalshi markets...")
            
            # Example loop delay
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"[ERROR] Error in main loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
