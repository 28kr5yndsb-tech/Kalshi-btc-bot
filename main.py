import os
import time
import datetime
import requests
import base64
import threading
from flask import Flask
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ==========================================
# FLASK WEB SERVER (For Render & UptimeRobot)
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "🤖 Kalshi BTC Scalp Bot is live and running!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web_server, daemon=True).start()


# ==========================================
# CONFIGURATION & PARAMETERS
# ==========================================
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

KEY_ID = os.getenv("KALSHI_API_KEY_ID") or os.getenv("KALSHI_KEY_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

RAW_PEM = os.getenv("KALSHI_PRIVATE_KEY") or os.getenv("KALSHI_API_PRIVATE_KEY") or ""
PRIVATE_KEY_PEM = RAW_PEM.replace("\\n", "\n")

# Scalping Parameters
DIP_THRESHOLD_PERCENT = 0.5    # Faster trigger for scalping (0.5% drop)
SURGE_THRESHOLD_PERCENT = 0.5  # Also catch sharp pumps
DIP_WINDOW_SECONDS = 180       # 3-minute rolling window for fast action
CHECK_EVERY_SECONDS = 5        # Lightning-fast poll loop


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass


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
        """Fetches separate 15-minute and hourly BTC markets."""
        all_markets = []
        for series in ["KXBTC15M", "KXBTC"]:
            path = f"/markets?series_ticker={series}&status=open"
            res = self.request("GET", path)
            markets = res.get("markets", [])
            if markets:
                all_markets.extend(markets)
        return all_markets


class BTCMonitor:
    def __init__(self, history_seconds=180):
        self.price_history = []
        self.history_seconds = history_seconds

    def fetch_current_price(self):
        try:
            res = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=3)
            data = res.json()
            return float(data["data"]["amount"])
        except Exception:
            return None

    def check_momentum(self):
        price = self.fetch_current_price()
        if not price:
            return "FLAT", 0.0, price

        now = time.time()
        self.price_history.append((now, price))
        self.price_history = [(t, p) for t, p in self.price_history if now - t <= self.history_seconds]

        base_price = self.price_history[0][1]
        pct_change = ((price - base_price) / base_price) * 100

        if pct_change <= -DIP_THRESHOLD_PERCENT:
            return "DIP", pct_change, price
        elif pct_change >= SURGE_THRESHOLD_PERCENT:
            return "SURGE", pct_change, price
        
        return "NORMAL", pct_change, price


def main():
    print("⚡ Starting Kalshi BTC Scalping Bot...")
    send_telegram("⚡ *Kalshi BTC Scalper Online!*\nScanning 15m and hourly contracts for rapid entries...")

    client = KalshiClient(KEY_ID, PRIVATE_KEY_PEM, KALSHI_BASE_URL)
    monitor = BTCMonitor(history_seconds=DIP_WINDOW_SECONDS)

    while True:
        try:
            status, change_pct, current_price = monitor.check_momentum()
            timestamp_str = datetime.datetime.now().strftime("%H:%M:%S")

            if current_price:
                print(f"[{timestamp_str}] BTC: ${current_price:,.2f} | 3m Change: {change_pct:+.2f}%")

            if status in ["DIP", "SURGE"]:
                markets = client.get_btc_markets()
                
                m15_details = "None active"
                hourly_details = "None active"

                if markets:
                    for m in markets:
                        ticker = m.get("ticker", "")
                        title = m.get("title", "Contract")
                        yes_ask = m.get("yes_ask", 0) / 100.0
                        no_ask = m.get("no_ask", 0) / 100.0

                        contract_summary = (
                            f"\n  • `[ {ticker} ]`\n"
                            f"    Target: {title}\n"
                            f"    Odds -> Yes: `${yes_ask:.2f}` | No: `${no_ask:.2f}`"
                        )

                        if "15M" in ticker:
                            m15_details = contract_summary
                        else:
                            hourly_details = contract_summary

                emoji = "🚨" if status == "DIP" else "🚀"
                action_text = "Buy NO (Short) or Scalp Rebound YES" if status == "DIP" else "Buy YES (Momentum) or Fade Top NO"

                alert_msg = (
                    f"{emoji} *BTC {status} DETECTED!* ({change_pct:+.2f}% in 3m)\n\n"
                    f"• *Spot Price:* `${current_price:,.2f}`\n\n"
                    f"⏱ *15-Min Scalp Market:*`{m15_details}`\n\n"
                    f"⏰ *Hourly Target Market:*`{hourly_details}`\n\n"
                    f"⚡ *Scalp Setup:* {action_text}"
                )
                
                print(alert_msg)
                send_telegram(alert_msg)
                
                # Cooldown to avoid alert spamming during high volatility
                time.sleep(60)

        except Exception as e:
            print(f"❌ Error in main loop: {e}")

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
