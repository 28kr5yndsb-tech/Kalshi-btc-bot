import os
import time
import requests

# --- Configuration (Update or use environment variables) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
CHECK_INTERVAL_SECONDS = 300  # Check every 5 minutes

def scan_low_prob_hourly_btc(max_price_cents=15):
    """Scans Kalshi hourly Bitcoin markets for low-probability/high-payout contracts."""
    url = "https://api.elections.kalshi.com/trade-api/v2/markets"
    params = {
        "limit": 100,
        "status": "open",
        "series_ticker": "KXBTCH"  # Kalshi's hourly Bitcoin series
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        markets = data.get("markets", [])
        
        matches = []
        for mkt in markets:
            # yes_ask represents the cost in cents (e.g., 8 = 8 cents / 8% implied chance)
            yes_ask = mkt.get("yes_ask", 100)
            
            # Filter for contracts priced between 1¢ and max_price_cents (e.g., 1% to 15% probability)
            if 0 < yes_ask <= max_price_cents:
                multiplier = round(100 / yes_ask, 1)
                matches.append({
                    "title": mkt.get("title"),
                    "subtitle": mkt.get("subtitle"),
                    "price_cents": yes_ask,
                    "multiplier": multiplier,
                    "ticker": mkt.get("ticker")
                })
        return matches
    except Exception as e:
        print(f"Error fetching Kalshi markets: {e}")
        return []

def send_telegram_alert(text):
    """Sends the formatted alert message to your Telegram chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, 
        "text": text, 
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Error sending Telegram alert: {e}")

def run_bot_loop():
    print("Starting Kalshi Hourly BTC Bot Scanner...")
    while True:
        print("Checking Kalshi hourly markets...")
        opportunities = scan_low_prob_hourly_btc(max_price_cents=15)
        
        if opportunities:
            msg = "🚨 *Low-Prob Hourly BTC Targets Found:*\n\n"
            for op in opportunities:
                msg += (
                    f"• *{op['title']}*\n"
                    f"  Sub: {op['subtitle']}\n"
                    f"  Cost: `{op['price_cents']}¢` ({op['multiplier']}x payout)\n"
                    f"  Ticker: `{op['ticker']}`\n\n"
                )
            send_telegram_alert(msg)
            print("Alert sent to Telegram!")
        else:
            print("No low-prob targets found right now.")
            
        # Wait before checking again (e.g., 5 minutes)
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    run_bot_loop()
