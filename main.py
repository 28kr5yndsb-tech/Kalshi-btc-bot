import os
import time
import datetime
import requests
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ==========================================
# CONFIGURATION & PARAMETERS
# ==========================================
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Retrieve Secrets from Replit Environment
KEY_ID = os.getenv("KALSHI_API_KEY_ID") or os.getenv("KALSHI_KEY_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Get Private Key content directly from Secrets (handles pasted newlines)
RAW_PEM = os.getenv("KALSHI_PRIVATE_KEY") or os.getenv("KALSHI_API_PRIVATE_KEY") or ""
PRIVATE_KEY_PEM = RAW_PEM.replace("\\n", "\n")

# Strategy / Threshold Settings
DIP_THRESHOLD_PERCENT = 0.8    # Alert if BTC drops > 0.8% in 5 minutes
DIP_WINDOW_SECONDS = 300       # 5 minutes lookback
CHECK_EVERY_SECONDS = 10       # Loop interval in seconds


# ==========================================
# TELEGRAM NOTIFIER
# ==========================================
def send_telegram(message):
    """Sends real-time notification pings directly to your Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram BOT_TOKEN or CHAT_ID missing from Secrets.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"⚠️ Failed to send Telegram alert: {e}")


# ==========================================
# KALSHI API CLIENT
# ==========================================
class KalshiClient:
    def __init__(self, key_id, pem_content, base_url):
        self.key_id = key_id
        self.base_url = base_url
        self.private_key = self._load_private_key(pem_content)

    def _load_private_key(self, pem_content):
        if not pem_content or "BEGIN PRIVATE KEY" not in pem_content:
            return None
        try:
            return serialization.load_pem_private_key(
                pem_content.strip().encode('utf-8'),
                password=None
            )
        except Exception:
            return None

    def _sign_request(self, timestamp, method, path):
        if not self.private_key:
            return ""
        message = f"{timestamp}{method}{path}".encode('utf-8')
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode('utf-8')

    def request(self, method, path, body=None):
        timestamp = str(int(time.time() * 1000))
        full_path = f"/trade-api/v2{path}"
        signature = self._sign_request(timestamp, method, full_path)

        headers = {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self.key_id or "",
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp
        }

        url = f"{self.base_url}{path}"
        response = requests.request(method, url, headers=headers, json=body)
        return response.json()

    def get_btc_markets(self):
        """Fetches active BTC prediction markets on Kalshi."""
        path = "/markets?series_ticker=KXBTC&status=open"
        res = self.request("GET", path)
        return res.get("markets", [])


# ==========================================
# REAL-TIME SPOT BTC MONITOR
# ==========================================
class BTCMonitor:
    def __init__(self, history_seconds=300):
        self.price_history = []
        self.history_seconds = history_seconds

    def fetch_current_price(self):
        """Fetches live BTC/USD spot price from Coinbase."""
        try:
            res = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
            data = res.json()
            return float(data["data"]["amount"])
        except Exception as e:
            print(f"⚠️ Error fetching BTC price: {e}")
            return None

    def update_and_check_dip(self):
        price = self.fetch_current_price()
        if not price:
            return False, 0.0, price

        now = time.time()
        self.price_history.append((now, price))

        # Prune prices older than 5 minutes
        self.price_history = [(t, p) for t, p in self.price_history if now - t <= self.history_seconds]

        peak_price = max(p for _, p in self.price_history)
        drop_pct = ((peak_price - price) / peak_price) *  100

        return drop_pct >= DIP_THRESHOLD_PERCENT, drop_pct, price


# ==========================================
# MAIN ALERT LOOP
# ==========================================
def main():
    print("🤖 Starting Kalshi BTC Dip Alert Bot...")
    send_telegram("🤖 *Kalshi BTC Alert Bot Online!*\nMonitoring real-time BTC dips & market targets...")

    client = KalshiClient(KEY_ID, PRIVATE_KEY_PEM, KALSHI_BASE_URL)
    monitor = BTCMonitor(history_seconds=DIP_WINDOW_SECONDS)

    while True:
        try:
            is_dip, drop_pct, current_price = monitor.update_and_check_dip()
            timestamp_str = datetime.datetime.now().strftime("%H:%M:%S")

            if current_price:
                print(f"[{timestamp_str}] BTC Price: ${current_price:,.2f} | 5m Drop: {drop_pct:.2f}%")

            if is_dip:
                # Fetch active markets to map target prices
                markets = client.get_btc_markets()
                market_snippet = ""
                
                if markets:
                    m = markets[0] # Grab the nearest active market contract
                    ticker = m.get("ticker", "KXBTC")
                    yes_ask = m.get("yes_ask", 0) / 100.0  # convert cents to dollar scale format if needed or keep raw cents
                    no_ask = m.get("no_ask", 0) / 100.0
                    
                    # Try extracting strike/target price from title or subtitle if available
                    title = m.get("title", "")
                    
                    market_snippet = (
                        f"• Active Ticker: `{ticker}`\n"
                        f"• Market Odds: Yes: `${yes_ask:.2f}` | No: `${no_ask:.2f}`\n"
                    )

                alert_msg = (
                    f"🚨 *BTC DIP DETECTED!*\n\n"
                    f"• BTC Price: `${current_price:,.2f}` (-{drop_pct:.2f}% in 3 min)\n"
                    f"{market_snippet}\n"
                    f"⚡ *Suggested Trade:* Buy YES (Rebound) or Buy NO (Short) on Kalshi."
                )
                
                print(alert_msg)
                send_telegram(alert_msg)

                # Cooldown so it doesn't spam every 10 seconds during a prolonged drop
                time.sleep(120)

        except Exception as e:
            print(f"❌ Error in main loop: {e}")

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
