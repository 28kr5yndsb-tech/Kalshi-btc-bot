import os
import time
import threading
import requests
from http.server import HTTPServer, SimpleHTTPRequestHandler

# Dummy HTTP server to keep Render web service awake
def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Parameters
POLL_INTERVAL = 15          # Check every 15 seconds
MAX_HISTORY = 12            # Trailing 3 minutes (12 samples * 15s)
TRIGGER_PERCENT = 0.35      # Trigger alert on 0.35% move in 3 mins

price_history = []
last_alert_time = 0
ALERT_COOLDOWN = 120        # Avoid spamming pings (2 mins between alerts)

def send_telegram(message):
    """Sends formatted Telegram message."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")

def get_btc_spot_price():
    """Fetches real-time BTC spot price."""
    try:
        res = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
        if res.status_code == 200:
            return float(res.json()['data']['amount'])
    except Exception as e:
        print(f"[ERROR] Spot price fetch error: {e}")
    return None

def get_kalshi_15m_market():
    """Fetches currently active Kalshi 15-min BTC market details."""
    try:
        url = "https://api.elections.kalshi.com/trade-api/v2/events"
        params = {"series_ticker": "KXBTC15M", "status": "open", "with_nested_markets": True}
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200:
            events = res.json().get("events", [])
            if events and "markets" in events[0] and events[0]["markets"]:
                market = events[0]["markets"][0]
                return {
                    "ticker": market.get("ticker", "N/A"),
                    "title": events[0].get("title", "BTC 15 min"),
                    "target_price": float(market.get("floor_strike", market.get("cap_strike", 0))),
                    "yes_ask": market.get("yes_ask_dollars", market.get("yes_ask", "N/A")),
                    "no_ask": market.get("no_ask_dollars", market.get("no_ask", "N/A")),
                    "close_time": market.get("close_time", "")
                }
    except Exception as e:
        print(f"[ERROR] Kalshi market fetch error: {e}")
    return None

def main():
    global last_alert_time
    print("Kalshi 15m BTC Dip & Surge Scanner starting...")
    send_telegram("🟢 *Kalshi 15m BTC Scanner Active!*\nMonitoring real-time BTC price moves vs. active Kalshi contracts.")

    while True:
        try:
            current_price = get_btc_spot_price()
            kalshi_mkt = get_kalshi_15m_market()

            if current_price:
                price_history.append(current_price)
                if len(price_history) > MAX_HISTORY:
                    price_history.pop(0)

                base_price = price_history[0]
                price_change_pct = ((current_price - base_price) / base_price) * 100

                # Formulate Kalshi stats if available
                if kalshi_mkt and kalshi_mkt["target_price"] > 0:
                    target = kalshi_mkt["target_price"]
                    dist = current_price - target
                    dist_pct = (dist / target) * 100
                    dist_str = f"+${dist:,.2f} (+{dist_pct:.2f}%)" if dist >= 0 else f"-${abs(dist):,.2f} ({dist_pct:.2f}%)"
                    mkt_ticker = kalshi_mkt['ticker']
                    yes_price = f"${kalshi_mkt['yes_ask']}" if kalshi_mkt['yes_ask'] != "N/A" else "N/A"
                    no_price = f"${kalshi_mkt['no_ask']}" if kalshi_mkt['no_ask'] != "N/A" else "N/A"
                else:
                    target = "N/A"
                    dist_str = "N/A"
                    mkt_ticker = "KXBTC15M"
                    yes_price = "N/A"
                    no_price = "N/A"

                print(f"[LOG] BTC: ${current_price:,.2f} | 3-Min Move: {price_change_pct:+.2f}% | Target: {target} ({dist_str})")

                # Trigger logic check
                now = time.time()
                if abs(price_change_pct) >= TRIGGER_PERCENT and (now - last_alert_time) > ALERT_COOLDOWN:
                    last_alert_time = now

                    if price_change_pct < 0:
                        direction = "🔻 *BTC DIP DETECTED*"
                        action = "⚡ *Suggested Trade:* Buy **NO (Short)** on Kalshi or look for a low-cost **YES (Rebound)**."
                    else:
                        direction = "🚀 *BTC SURGE DETECTED*"
                        action = "⚡ *Suggested Trade:* Buy **YES (Long)** on Kalshi."

                    msg = (
                        f"{direction}\n\n"
                        f"• *BTC Price:* ${current_price:,.2f} ({price_change_pct:+.2f}% in 3 min)\n"
                        f"• *Active Ticker:* `{mkt_ticker}`\n"
                        f"• *Target Price:* ${target:,.2f} if isinstance(target, float) else target\n"
                        f"• *Distance to Target:* {dist_str}\n"
                        f"• *Market Odds:* Yes: {yes_price} | No: {no_price}\n\n"
                        f"{action}"
                    )
                    send_telegram(msg)

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            print(f"[ERROR] Main loop exception: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
