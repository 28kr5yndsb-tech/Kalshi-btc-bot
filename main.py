import os
import time
import threading
import requests
from http.server import HTTPServer, SimpleHTTPRequestHandler

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

POLL_INTERVAL = 15
MAX_HISTORY = 12
TRIGGER_PERCENT = 0.45  # Stricter threshold: requires a strong 0.45% move to avoid noise

price_history = []
last_alert_time = 0
ALERT_COOLDOWN = 180    # 3-minute cooldown so it only alerts on major moves

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=5)

def get_btc_spot_price():
    try:
        res = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
        if res.status_code == 200:
            return float(res.json()['data']['amount'])
    except:
        pass
    return None

def get_kalshi_market():
    """Fetches active Kalshi 15-min market and parses exact dollar pricing fields."""
    try:
        url = "https://api.elections.kalshi.com/trade-api/v2/events"
        params = {"series_ticker": "KXBTC15M", "status": "open", "with_nested_markets": True}
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200:
            events = res.json().get("events", [])
            if events and "markets" in events[0] and events[0]["markets"]:
                mkt = events[0]["markets"][0]
                
                # Parse exact dollar pricing fields provided by Kalshi API
                yes_cost = float(mkt.get("yes_ask_dollars", mkt.get("yes_bid_dollars", 0.50)))
                no_cost = float(mkt.get("no_ask_dollars", mkt.get("no_bid_dollars", 0.50)))

                return {
                    "ticker": mkt.get("ticker", "KXBTC15M"),
                    "strike": float(mkt.get("floor_strike", mkt.get("cap_strike", 0))),
                    "yes_price": yes_cost if yes_cost > 0 else 0.50,
                    "no_price": no_cost if no_cost > 0 else 0.50,
                }
    except Exception as e:
        print(f"Kalshi error: {e}")
    return None

def main():
    global last_alert_time
    send_telegram("🟢 *Kalshi Dip & Reversal Scanner Active (Strict Mode)*")

    while True:
        try:
            current_price = get_btc_spot_price()
            kalshi_mkt = get_kalshi_market()

            if current_price:
                price_history.append(current_price)
                if len(price_history) > MAX_HISTORY:
                    price_history.pop(0)

                base_price = price_history[0]
                price_change_pct = ((current_price - base_price) / base_price) * 100

                ticker = kalshi_mkt["ticker"] if kalshi_mkt else "KXBTC15M"
                strike = kalshi_mkt["strike"] if kalshi_mkt else current_price
                yes_p = kalshi_mkt["yes_price"] if kalshi_mkt else 0.50
                no_p = kalshi_mkt["no_price"] if kalshi_mkt else 0.50

                now = time.time()
                # Only trigger on significant dips/surges past the strict threshold
                if abs(price_change_pct) >= TRIGGER_PERCENT and (now - last_alert_time) > ALERT_COOLDOWN:
                    last_alert_time = now

                    if price_change_pct < 0:
                        header = "🔻 *STRONG DIP REVERSAL ALERT*"
                        if current_price < strike:
                            advice = f"• **Spot is BELOW Strike** 📉\n• Heavy dump below target. Consider **NO** for continuation or look for deep-value **YES** bounce plays."
                        else:
                            advice = f"• **Spot holding ABOVE Strike** 📈\n• Strong dip holding the line. Excellent candidate to buy cheap **YES** for a snap-back."
                    else:
                        header = "🚀 *STRONG SURGE BREAKOUT ALERT*"
                        advice = f"• **Momentum Breakout** 🚀\n• Strong pump. Consider buying **YES** to ride continuation."

                    msg = (
                        f"{header}\n\n"
                        f"• *Spot Price:* ${current_price:,.2f} ({price_change_pct:+.2f}%)\n"
                        f"• *Kalshi Ticker:* `{ticker}`\n"
                        f"• *Strike Target:* ${strike:,.2f}\n\n"
                        f"💰 *Live Contract Costs:*\n"
                        f"  - **YES Cost:** ${yes_p:.2f}\n"
                        f"  - **NO Cost:** ${no_p:.2f}\n\n"
                        f"{advice}"
                    )
                    send_telegram(msg)

            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
