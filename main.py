"""
Kalshi BTC alert bot (Telegram)

1) Pings you when BTC dips or spikes inside the current 15-minute window.
2) Sends suggested price call-outs for the hour at :00 and :30.

Price data: Coinbase public API (no key needed).
Secrets needed in Replit (lock icon -> Secrets):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""

import math
import os
import statistics
import time
from collections import deque
from datetime import datetime, timezone

import requests

# ------------------------- SETTINGS (change these) -------------------------
POLL_SECONDS = 5            # how often to check price
WINDOW_MOVE_PCT = 0.12      # alert when price is this % away from the 15-min open
                            # (alerts again for each extra multiple: 0.24%, 0.36%...)
FAST_MOVE_PCT = 0.08        # alert on a sharp move this big inside FAST_WINDOW_SEC
FAST_WINDOW_SEC = 60
FAST_COOLDOWN_SEC = 120     # min seconds between fast-move alerts
STRIKE_STEP = 100           # $ spacing for the hourly strike table
SEND_WINDOW_OPEN = True     # ping at the start of each 15-min window
# ---------------------------------------------------------------------------

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
API = "https://api.exchange.coinbase.com"
HEADERS = {"User-Agent": "kalshi-btc-alerts/1.0"}


def send(msg: str) -> None:
    """Send a Telegram message. Never crashes the bot."""
    print(msg.replace("\n", " | "))
    if not TOKEN or not CHAT_ID:
        print("!! Telegram secrets missing - message not sent")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg},
            timeout=10,
        )
        if not r.ok:
            print("Telegram error:", r.text)
    except Exception as e:
        print("Telegram send failed:", e)


def get_price() -> float:
    r = requests.get(f"{API}/products/BTC-USD/ticker", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])


def get_candles(granularity: int, count: int):
    """Returns candles oldest->newest as [time, low, high, open, close, volume]."""
    end = int(time.time())
    start = end - granularity * count
    r = requests.get(
        f"{API}/products/BTC-USD/candles",
        params={"granularity": granularity, "start": start, "end": end},
        headers=HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    return sorted(r.json(), key=lambda c: c[0])


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def fmt(p: float) -> str:
    return f"${p:,.0f}"


def hourly_callouts(price: float, now: datetime) -> str:
    candles = get_candles(60, 240)  # last 4 hours of 1-minute candles
    closes = [c[4] for c in candles if c[4] > 0]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    sigma_1m = statistics.pstdev(rets)

    hour_start_ts = int(now.replace(minute=0, second=0, microsecond=0).timestamp())
    hour_open = price
    for c in candles:
        if c[0] == hour_start_ts:
            hour_open = c[3]
            break

    mins_left = max(1, 60 - now.minute)
    sigma_left = sigma_1m * math.sqrt(mins_left)   # expected move to end of hour
    move = price * sigma_left

    hi4 = max(c[2] for c in candles)
    lo4 = min(c[1] for c in candles)
    last60 = candles[-60:]
    hi1 = max(c[2] for c in last60)
    lo1 = min(c[1] for c in last60)

    lines = [
        f"HOURLY CALL-OUTS  ({now.strftime('%H:%M')} UTC, {mins_left} min left)",
        f"Price: {fmt(price)}   Hour open: {fmt(hour_open)}",
        "",
        f"Likely range to hour end (~68%): {fmt(price - move)} - {fmt(price + move)}",
        f"Stretch range (~95%): {fmt(price - 2 * move)} - {fmt(price + 2 * move)}",
        "",
        f"Resistance: {fmt(hi1)} (last 1h high), {fmt(hi4)} (4h high)",
        f"Support:    {fmt(lo1)} (last 1h low), {fmt(lo4)} (4h low)",
        "",
        "Model chance BTC finishes the hour ABOVE:",
    ]

    center = round(price / STRIKE_STEP) * STRIKE_STEP
    for k in range(center + 2 * STRIKE_STEP, center - 3 * STRIKE_STEP, -STRIKE_STEP):
        z = math.log(k / price) / sigma_left if sigma_left > 0 else 0
        p_above = 1 - norm_cdf(z)
        lines.append(f"  {fmt(k)}: {p_above * 100:.0f}%")

    lines += [
        "",
        "Simple volatility model, no trend or fees. A guide, not a prediction.",
    ]
    return "\n".join(lines)


def main():
    send("BTC alert bot started.")
    history = deque()          # (timestamp, price) for the fast-move check
    window_id = None
    window_open = None
    last_level = {"up": 0, "down": 0}
    last_fast_alert = 0.0
    last_callout_key = None

    while True:
        try:
            now = datetime.now(timezone.utc)
            ts = time.time()
            price = get_price()

            # ---- new 15-minute window (Kalshi 15-min markets: :00 :15 :30 :45) ----
            wid = int(ts // 900)
            if wid != window_id:
                window_id = wid
                window_open = price
                last_level = {"up": 0, "down": 0}
                if SEND_WINDOW_OPEN:
                    send(f"NEW 15-MIN WINDOW\nOpen: {fmt(price)}")

            mins_left = 15 - (now.minute % 15) - (1 if now.second else 0)
            mins_left = max(mins_left, 0)

            # ---- move away from the 15-min open ----
            move_pct = (price / window_open - 1) * 100
            direction = "up" if move_pct > 0 else "down"
            level = int(abs(move_pct) // WINDOW_MOVE_PCT)
            if level > last_level[direction]:
                last_level[direction] = level
                arrow = "UP" if direction == "up" else "DOWN"
                send(
                    f"15-MIN {arrow} {abs(move_pct):.2f}%\n"
                    f"Now {fmt(price)} vs open {fmt(window_open)}\n"
                    f"~{mins_left} min left in window"
                )

            # ---- fast spike / dip ----
            history.append((ts, price))
            while history and ts - history[0][0] > FAST_WINDOW_SEC:
                history.popleft()
            old_price = history[0][1]
            fast_pct = (price / old_price - 1) * 100
            if abs(fast_pct) >= FAST_MOVE_PCT and ts - last_fast_alert > FAST_COOLDOWN_SEC:
                last_fast_alert = ts
                word = "SPIKE" if fast_pct > 0 else "DIP"
                send(
                    f"FAST {word} {abs(fast_pct):.2f}% in ~{FAST_WINDOW_SEC}s\n"
                    f"{fmt(old_price)} -> {fmt(price)}\n"
                    f"~{mins_left} min left in 15-min window"
                )

            # ---- hourly call-outs at :00 and :30 (and once at startup) ----
            key = (now.date(), now.hour, now.minute // 30)
            if key != last_callout_key and (last_callout_key is None or now.minute % 30 == 0):
                last_callout_key = key
                send(hourly_callouts(price, now))

        except Exception as e:
            print("Loop error:", e)
            time.sleep(10)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
