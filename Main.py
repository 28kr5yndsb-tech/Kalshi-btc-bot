import os
import time
import requests

# Load secrets from Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Check interval (in seconds)
POLL_INTERVAL = 60

# Tracking price history for drop calculation
price_history = []
MAX_HISTORY = 5  # Trailing 5 samples (5 minutes)
DROP_THRESHOLD = 0.01  # Example: 1% drop threshold (adjust as needed)


def send_telegram_alert(message):
    """Sends a notification message to your Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[INFO] Telegram notification sent successfully!")
        else:
            print(f"[ERROR] Failed to send Telegram alert: {response.text}")
    except Exception as e:
        print(f"[ERROR] Exception sending Telegram alert: {e}")


def fetch_btc_price():
    """Fetches current BTC price from public API."""
    try:
        url = "https://api.coindesk.com/v1/bpi/currentprice.json"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return float(data["bpi"]["USD"]["rate_float"])
    except Exception as e:
        print(f"[ERROR] Failed to fetch BTC price: {e}")
    return None


def main():
    print("🚀 Kalshi BTC Dip Alert Bot Started...")
    send_telegram_alert(
        "🤖 Kalshi BTC Dip Alert Bot is now ONLINE and monitoring for dips!"
    )

    while True:
        try:
            current_price = fetch_btc_price()

            if current_price:
                price_history.append(current_price)
                if len(price_history) > MAX_HISTORY:
                    price_history.pop(0)

                peak_price = max(price_history)
                drop_percent = (peak_price - current_price) / peak_price

                print(
                    f"[LOG] Current BTC: ${current_price:,.2f} | 5-min High: ${peak_price:,.2f} | Drop: {drop_percent*100:.2f}%"
                )

                # If dip threshold triggered
                if drop_percent >= DROP_THRESHOLD:
                    alert_msg = (
                        f"📉 **BTC DIP ALERT!**\n\n"
                        f"Current Price: ${current_price:,.2f}\n"
                        f"Recent High: ${peak_price:,.2f}\n"
                        f"Drop: {drop_percent*100:.2f}%\n\n"
                        f"Check Kalshi markets for entry!"
                    )
                    send_telegram_alert(alert_msg)

        except Exception as e:
            print(f"[ERROR] Main loop error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
