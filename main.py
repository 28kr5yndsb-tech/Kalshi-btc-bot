"""
Kalshi BTC buy-signal bot (Telegram)

Compares live Bitcoin price (Coinbase) with the real Kalshi prices and tells you:
  1) 15-MIN market: BUY UP (YES) or BUY DOWN (NO) when Kalshi's price looks off.
  2) HOURLY market: which strike to buy and the max price to pay, at :00 and :30.

Secrets needed (Render -> Environment, or Replit -> Secrets):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
No Kalshi login needed: it only reads public prices. It never places trades.
"""

import math
import os
import statistics
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import requests

# ------------------------- SETTINGS (change these) -------------------------
POLL_SECONDS = 5
MIN_EDGE_15 = 0.10            # 15-min: only alert when model beats the price by 10c+ (after fees)
MIN_MINS_LEFT_15 = 4.0        # 15-min: no new signals in the last 4 minutes
HOURLY_MIN_EDGE = 0.06        # hourly: min edge (after fees) to list a pick
HOURLY_CALLOUT_MINUTES = (0, 30)   # send the hourly list at these minutes
PRICE_RANGE = (0.10, 0.90)    # ignore contracts priced outside 10c-90c
SIGMA_MULT = 1.2              # >1 = more cautious (BTC has fat tails)
FEE_RATE = 0.07               # Kalshi taker fee approx 7% * p * (1-p)
REALERT_SECONDS = 120         # same side again only after this long...
REALERT_EXTRA_EDGE = 0.08     # ...AND if the edge grew by this much
# ---------------------------------------------------------------------------

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
COINBASE = "https://api.exchange.coinbase.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
HEADERS = {"User-Agent": "kalshi-btc-signals/2.0"}
ET = ZoneInfo("America/New_York")


# ----------------------------- helpers -----------------------------
def send(msg: str) -> None:
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


def usd(p: float) -> str:
    return f"${p:,.0f}"


def cents(x: float) -> str:
    return f"${x:.2f}"


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def fee(p: float) -> float:
    return FEE_RATE * p * (1 - p)


def get_price() -> float:
    r = requests.get(f"{COINBASE}/products/BTC-USD/ticker", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])


def get_candles(granularity: int, count: int):
    end = int(time.time())
    r = requests.get(
        f"{COINBASE}/products/BTC-USD/candles",
        params={"granularity": granularity, "start": end - granularity * count, "end": end},
        headers=HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    return sorted(r.json(), key=lambda c: c[0])


_sigma_cache = {"t": 0.0, "v": None}


def get_sigma() -> float:
    """Per-minute volatility from the last ~2 hours. Cached for 60 seconds."""
    if _sigma_cache["v"] is None or time.time() - _sigma_cache["t"] > 60:
        closes = [c[4] for c in get_candles(60, 120) if c[4] > 0]
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        _sigma_cache.update(t=time.time(), v=max(statistics.pstdev(rets), 1e-5))
    return _sigma_cache["v"]


def prob_above(spot: float, strike: float, sigma_1m: float, mins_left: float) -> float:
    s = sigma_1m * SIGMA_MULT * math.sqrt(max(mins_left, 0.25))
    p = 1 - norm_cdf(math.log(strike / spot) / s)
    return min(0.98, max(0.02, p))


# ----------------------------- Kalshi -----------------------------
def kalshi_markets(series: str, **params):
    q = {"series_ticker": series, "status": "open", "limit": 200}
    q.update(params)
    r = requests.get(f"{KALSHI}/markets", params=q, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json().get("markets", [])


def price_of(m: dict, key: str):
    """Kalshi price in dollars (0-1). Handles both the dollars and cents formats."""
    v = m.get(key + "_dollars")
    if v not in (None, ""):
        v = float(v)
    else:
        v = m.get(key)
        if v is None:
            return None
        v = float(v) / 100.0
    return v if v > 0 else None


def close_dt(m: dict) -> datetime:
    return datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))


def best_side(p_yes: float, yes_ask, no_ask, min_edge: float):
    """Returns (side, ask, fair, net_edge) for the better side, or None."""
    best = None
    for side, fair, ask in (("YES", p_yes, yes_ask), ("NO", 1 - p_yes, no_ask)):
        if ask is None or not (PRICE_RANGE[0] <= ask <= PRICE_RANGE[1]):
            continue
        net = fair - ask - fee(ask)
        if net >= min_edge and (best is None or net > best[3]):
            best = (side, ask, fair, net)
    return best


# ----------------------------- 15-minute signals -----------------------------
def check_15m(spot, sigma, now_ts, fallback_open, move_60s):
    """Returns (key, side, edge, message) or None."""
    mk = [m for m in kalshi_markets("KXBTC15M", limit=20) if close_dt(m).timestamp() > now_ts]
    if not mk:
        return None
    m = min(mk, key=lambda x: close_dt(x).timestamp())
    mins_left = (close_dt(m).timestamp() - now_ts) / 60
    if mins_left < MIN_MINS_LEFT_15 or mins_left > 15.3:
        return None

    strike = m.get("floor_strike")
    strike = float(strike) if strike else fallback_open
    p_yes = prob_above(spot, strike, sigma, mins_left)
    pick = best_side(p_yes, price_of(m, "yes_ask"), price_of(m, "no_ask"), MIN_EDGE_15)
    if not pick:
        return None

    side, ask, fair, net = pick
    label = "UP (YES)" if side == "YES" else "DOWN (NO)"
    max_pay = math.floor((fair - fee(ask) - 0.03) * 100) / 100
    gap = spot - strike
    msg = (
        f"BUY {label} - 15 MIN\n"
        f"Ask now: {cents(ask)}   Max price: {cents(max_pay)}\n"
        f"Model {fair * 100:.0f}% vs market {ask * 100:.0f}%  (edge +{net * 100:.0f}c after fees)\n"
        f"BTC {usd(spot)} | price to beat {usd(strike)} ({'+' if gap >= 0 else '-'}${abs(gap):,.0f})\n"
        f"Last 60s: {move_60s:+.2f}% | {mins_left:.0f} min left\n"
        f"Sell target: ~{cents(min(0.97, fair))} or when edge is gone.\n"
        f"Bail if BTC crosses back {'below' if side == 'YES' else 'above'} {usd(strike)}."
    )
    return (m["ticker"], side, net, msg)


# ----------------------------- hourly picks -----------------------------
def hourly_picks(spot, sigma, now: datetime) -> str:
    ts = now.timestamp()
    mk = kalshi_markets("KXBTCD", min_close_ts=int(ts) + 30, max_close_ts=int(ts) + 3700)
    if not mk:   # fallback: fetch more and filter ourselves
        mk = [m for m in kalshi_markets("KXBTCD", limit=1000) if close_dt(m).timestamp() > ts]
    if not mk:
        return "HOURLY: couldn't find the Kalshi hourly BTC market right now."

    next_close = min(close_dt(m) for m in mk)
    mk = [m for m in mk if close_dt(m) == next_close and m.get("floor_strike")]
    mins_left = max((next_close.timestamp() - ts) / 60, 0.5)

    move = spot * sigma * SIGMA_MULT * math.sqrt(mins_left)
    header = (
        f"HOURLY BUY LIST - closes {next_close.astimezone(ET).strftime('%-I:%M %p')} ET "
        f"({mins_left:.0f} min)\n"
        f"BTC {usd(spot)} | likely range {usd(spot - move)} - {usd(spot + move)}\n"
    )

    rows = []
    for m in mk:
        k = float(m["floor_strike"])
        p_yes = prob_above(spot, k, sigma, mins_left)
        ya, na = price_of(m, "yes_ask"), price_of(m, "no_ask")
        pick = best_side(p_yes, ya, na, HOURLY_MIN_EDGE)
        if pick:
            rows.append((pick[3], k, pick))
    rows.sort(reverse=True)

    if not rows:
        return header + "\nNo strike has a clear edge right now. Skip the hourly market until the next update."

    lines = [header]
    for i, (_, k, (side, ask, fair, net)) in enumerate(rows[:3], 1):
        word = "above" if side == "YES" else "below"
        max_pay = math.floor((fair - fee(ask) - 0.02) * 100) / 100
        lines.append(
            f"{i}) BUY {side}: BTC {word} {usd(k)}\n"
            f"   Ask {cents(ask)} | max price {cents(max_pay)} | model {fair * 100:.0f}% (edge +{net * 100:.0f}c)"
        )
    lines.append("\nModel = volatility estimate, no trend. Prices move fast, so check Kalshi before buying.")
    return "\n".join(lines)


# ----------------------------- keep-alive web server -----------------------------
# Render "Web Service" needs an open port or it stops the bot. This opens one.
class Ping(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"bot running")

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


def start_keep_alive():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Ping)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Keep-alive server on port", port)


# ----------------------------- main loop -----------------------------
def main():
    start_keep_alive()
    send("BTC signal bot started (Kalshi prices + Telegram).")
    history = deque()
    window_id, window_open = None, None
    alerted = {}                     # (ticker, side) -> (time, edge)
    last_callout = None

    while True:
        try:
            now = datetime.now(timezone.utc)
            ts = time.time()
            spot = get_price()
            sigma = get_sigma()

            wid = int(ts // 900)
            if wid != window_id:
                window_id, window_open = wid, spot

            history.append((ts, spot))
            while history and ts - history[0][0] > 60:
                history.popleft()
            move_60s = (spot / history[0][1] - 1) * 100

            # ---- 15-minute buy signals ----
            try:
                res = check_15m(spot, sigma, ts, window_open, move_60s)
            except Exception as e:
                print("15m check failed:", e)
                res = None
            if res:
                key, side, edge, msg = res
                last = alerted.get((key, side))
                if last is None or (ts - last[0] > REALERT_SECONDS and edge >= last[1] + REALERT_EXTRA_EDGE):
                    alerted[(key, side)] = (ts, edge)
                    send(msg)
            if len(alerted) > 200:
                alerted.clear()

            # ---- hourly buy list ----
            ck = (now.date(), now.hour, now.minute)
            if ck != last_callout and (last_callout is None or now.minute in HOURLY_CALLOUT_MINUTES):
                last_callout = ck
                try:
                    send(hourly_picks(spot, sigma, now))
                except Exception as e:
                    print("Hourly failed:", e)

        except Exception as e:
            print("Loop error:", e)
            time.sleep(10)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
