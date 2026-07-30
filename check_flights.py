"""
DXB -> China Flight Deal Scanner

Forked from rizabalci/worldwide-flight-bot (MIT).
https://github.com/rizabalci/worldwide-flight-bot

Daily round-trip deal scanner from Dubai (DXB) to 22 Chinese cities.
Each city has its own target price. An alert fires per route if either:
  1. Below target  -- the fare is at or under the target (with a margin)
  2. Big drop      -- the fare is >= ROLLING_DROP_PCT below the route's
                      rolling average across its recent history
  3. All-time low  -- cheaper than any fare previously recorded

Stack: Travelpayouts (Aviasales v3) + Telegram + GitHub Actions. Free.

Silent on dealless days (unless HEARTBEAT=true).

Environment variables:
    TRAVELPAYOUTS_TOKEN   (secret)
    TELEGRAM_BOT_TOKEN    (secret)
    TELEGRAM_CHAT_ID      (secret)
    CURRENCY              (var)   default "cny"
    ORIGINS               (var)   default "DXB"
    ROLLING_WINDOW_DAYS   (var)   default 14
    ROLLING_DROP_PCT      (var)   default 0.20
    TARGET_MARGIN_PCT     (var)   default 0.10
    MONTHS_AHEAD          (var)   default 6
    MAX_NIGHTS            (var)   default 21
    WATCHLIST             (var)   default "PEK,PVG,HKG"
    HEARTBEAT             (var)   default "true"
    MUTE                  (var)   default "false"
    BROAD_FALLBACK        (var)   default "true"
"""

import json
import os
import sys
import time
from datetime import date, datetime, timezone
from statistics import mean

import requests

# -------------------- Secrets / tuning --------------------

TRAVELPAYOUTS_TOKEN = os.environ["TRAVELPAYOUTS_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def env_str(name: str, default: str) -> str:
    """Read a string env var. Empty string or unset -> default."""
    v = os.environ.get(name, "").strip()
    return v if v else default


def env_int(name: str, default: int) -> int:
    """Read an int env var. Empty string or unset -> default.
    (GitHub Actions passes unset repo variables as '', which int() rejects.)"""
    v = os.environ.get(name, "").strip()
    try:
        return int(v) if v else default
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    """Read a float env var. Empty string or unset -> default."""
    v = os.environ.get(name, "").strip()
    try:
        return float(v) if v else default
    except ValueError:
        return default


CURRENCY = env_str("CURRENCY", "cny").lower()
ORIGINS = [o.strip().upper() for o in os.environ.get("ORIGINS", "").split(",") if o.strip()] or ["DXB"]

ROLLING_WINDOW_DAYS = env_int("ROLLING_WINDOW_DAYS", 14)
ROLLING_DROP_PCT = env_float("ROLLING_DROP_PCT", 0.20)
# A fare must be at least this far UNDER target to count as a "below target" hit.
# Kills barely-under noise. 0.10 = must be >=10% under target. Set 0.0 to alert
# on anything at or below target.
TARGET_MARGIN_PCT = env_float("TARGET_MARGIN_PCT", 0.10)

# Watchlist: routes shown every day, even when not a deal.
WATCHLIST = [c.strip().upper() for c in os.environ.get("WATCHLIST", "").split(",") if c.strip()] or ["PEK", "PVG", "HKG"]

HEARTBEAT = env_str("HEARTBEAT", "true").lower() != "false"
MUTE = env_str("MUTE", "false").lower() == "true"

# When a route returns nothing for the queried months, retry once with no month
# constraint. Catches routes whose cached fares sit outside the scan window.
BROAD_FALLBACK = env_str("BROAD_FALLBACK", "true").lower() != "false"

# API pacing. Travelpayouts Data API allows 60 req/min.
PACING_SECONDS = env_float("PACING_SECONDS", 0.6)

# -------------------- Currency symbol lookup --------------------

CURRENCY_SYMBOL = {
    "cny": "¥", "aed": "د.إ", "usd": "$", "eur": "€", "gbp": "£",
    "rub": "₽", "hkd": "HK$", "twd": "NT$", "krw": "₩", "jpy": "¥",
    "sgd": "S$", "myr": "RM", "thb": "฿", "inr": "₹", "aud": "A$",
    "cad": "C$", "chf": "CHF", "try": "₺",
}.get(CURRENCY, CURRENCY.upper() + " ")


# -------------------- Tier rules --------------------
# Only one tier: DXB -> China is always long-haul (6-10 hours).

CN = {
    "name": "china",
    "label": "China",
    "flag": "🇨🇳",
    "arrow": "→",
    "trip_word": "round-trip",
    "trip_type": "rt",
    "one_way": "false",
    # Most DXB -> CN routes have at least 1 stop (via HKG, DOH, IST, etc.).
    # Setting direct=false surfaces connecting flights, which are usually
    # 20-40% cheaper than direct on this corridor.
    "direct": "false",
    "months_ahead": env_int("MONTHS_AHEAD", 6),
    # Long-haul justifies a longer stay (the flight eats a day each way).
    # Cap at 21 nights (3 weeks) -- beyond that, prices inflate for "open jaw"
    # or extended stays and stops being comparable to a normal trip.
    "max_nights": env_int("MAX_NIGHTS", 21),
}


# -------------------- China destinations --------------------
#
# Targets are round-trip economy, CNY, from DXB, per adult.
#
# Calibrated against typical booking-site pricing as of 2025-2026:
#   - HKG/MFM/CAN/SZX/PEK/PVG: lots of capacity, lots of competition
#   - HGH/CTU/CKG/XIY/NKG: solid mid-tier, mostly via HKG or DOH
#   - Smaller cities (KMG/WUH/HAK/SYX): often via HKG or back through CAN
#
# Bump a target up if you only want to hear about truly exceptional prices;
# bump down if you want more frequent alerts.

CHINA_DESTINATIONS = {
    # --- Tier 1: most capacity, lowest typical fares ---
    "HKG": ("Hong Kong · 香港",        1800),
    "MFM": ("Macau · 澳门",           2200),
    "CAN": ("Guangzhou · 广州",       2400),
    "SZX": ("Shenzhen · 深圳",        2500),
    "PEK": ("Beijing Capital · 北京首都", 2700),
    "PVG": ("Shanghai Pudong · 上海浦东", 2700),

    # --- Tier 2: major hubs, mostly 1-stop ---
    "HGH": ("Hangzhou · 杭州",         3000),
    "NKG": ("Nanjing · 南京",          3000),
    "XIY": ("Xi'an · 西安",            3100),
    "CTU": ("Chengdu · 成都",          3200),
    "CKG": ("Chongqing · 重庆",        3200),

    # --- Tier 3: regional cities ---
    "XMN": ("Xiamen · 厦门",           3000),
    "WUH": ("Wuhan · 武汉",            3100),
    "TAO": ("Qingdao · 青岛",          3100),
    "TSN": ("Tianjin · 天津",          3100),
    "DLC": ("Dalian · 大连",           3300),
    "KMG": ("Kunming · 昆明",          3500),

    # --- Hainan / beach ---
    "HAK": ("Haikou · 海口",           3500),
    "SYX": ("Sanya · 三亚",            3800),

    # --- Taiwan ---
    "TPE": ("Taipei Taoyuan · 台北桃園",  2500),
    "TSA": ("Taipei Songshan · 台北松山",  2700),
    "KHH": ("Kaohsiung · 高雄",         3000),
}


HISTORY_FILE = "price_history.json"
API_BASE = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
AVIASALES = "https://www.aviasales.com"


# -------------------- Date helpers --------------------

def upcoming_months(n: int) -> list:
    today = date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(n):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def fmt_date(d):
    """Convert an API date 'YYYY-MM-DD' to display format 'DD MMM'.
    Leaves anything unparseable (e.g. 'flexible') untouched."""
    if not d:
        return d
    MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    try:
        dt = date.fromisoformat(d)
        return f"{dt.day:02d} {MONTHS[dt.month]}"
    except ValueError:
        return d


def trip_nights(dep, ret):
    """Number of nights between departure and return (YYYY-MM-DD strings)."""
    if not dep or not ret:
        return None
    try:
        d = date.fromisoformat(dep)
        r = date.fromisoformat(ret)
        return (r - d).days
    except ValueError:
        return None


def passes_filter(dep, nights, cfg):
    """Decide whether a fare qualifies. Rejects trips longer than the
    tier's max_nights."""
    cap = cfg.get("max_nights")
    if cap is not None and nights is not None and nights > cap:
        return False
    return True


# -------------------- API fetch --------------------

def get_cheapest(origin, destination, cfg):
    """Cheapest fare origin->destination under the tier's rules.

    API quirks of Travelpayouts at this scale:
      * 429 Too Many Requests   -> back off and retry the SAME call (up to 5x).
      * 400 Bad Request         -> route+month not indexed, skip silently.
    """
    best = None
    fetch_limit = env_int("FETCH_LIMIT", 8)
    for ym in upcoming_months(cfg["months_ahead"]):
        params = {
            "origin": origin,
            "destination": destination,
            "departure_at": ym,
            "currency": CURRENCY,
            "one_way": cfg["one_way"],
            "direct": cfg["direct"],
            "sorting": "price",
            "limit": fetch_limit,
            "token": TRAVELPAYOUTS_TOKEN,
        }
        data = None
        attempts = 0
        while True:
            attempts += 1
            try:
                r = requests.get(
                    API_BASE,
                    params=params,
                    headers={"X-Access-Token": TRAVELPAYOUTS_TOKEN},
                    timeout=30,
                )
                if r.status_code == 429:
                    wait = int(r.headers.get("Retry-After", "0")) or min(15, 3 * attempts)
                    print(
                        f"  ~ {origin}->{destination} {ym}: 429, waiting {wait}s "
                        f"(attempt {attempts})",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
                    if attempts < 5:
                        continue
                    break
                if r.status_code == 400:
                    break
                r.raise_for_status()
                data = r.json().get("data", [])
                break
            except Exception as e:  # noqa: BLE001
                print(f"  ! {origin}->{destination} {ym}: API error {e}", file=sys.stderr)
                break

        if data:
            for item in data:
                price = item.get("price")
                dep = (item.get("departure_at") or "")[:10]
                ret = (item.get("return_at") or "")[:10]
                nights = trip_nights(dep, ret)
                if price is None or not passes_filter(dep, nights, cfg):
                    continue
                if best is None or price < best["price"]:
                    best = {
                        "price": int(round(price)),
                        "airline": item.get("airline", "?"),
                        "departure_at": dep,
                        "return_at": ret,
                        "nights": nights,
                        "link": AVIASALES + item.get("link", "") if item.get("link") else None,
                    }
        time.sleep(PACING_SECONDS)

    # Fallback: catch routes whose cached fares sit outside the scan window.
    if best is None and BROAD_FALLBACK:
        params = {
            "origin": origin,
            "destination": destination,
            "currency": CURRENCY,
            "one_way": cfg["one_way"],
            "direct": cfg["direct"],
            "sorting": "price",
            "limit": env_int("FETCH_LIMIT", 8),
            "token": TRAVELPAYOUTS_TOKEN,
        }
        try:
            r = requests.get(
                API_BASE,
                params=params,
                headers={"X-Access-Token": TRAVELPAYOUTS_TOKEN},
                timeout=30,
            )
            if r.status_code == 200:
                for item in r.json().get("data", []):
                    price = item.get("price")
                    dep = (item.get("departure_at") or "")[:10]
                    ret = (item.get("return_at") or "")[:10]
                    nights = trip_nights(dep, ret)
                    if price is None or not passes_filter(dep, nights, cfg):
                        continue
                    if best is None or price < best["price"]:
                        best = {
                            "price": int(round(price)),
                            "airline": item.get("airline", "?"),
                            "departure_at": dep,
                            "return_at": ret,
                            "nights": nights,
                            "link": AVIASALES + item.get("link", "") if item.get("link") else None,
                            "broad": True,
                        }
        except Exception as e:  # noqa: BLE001
            print(f"  ! {origin}->{destination} broad: {e}", file=sys.stderr)
        time.sleep(PACING_SECONDS)

    return best


# -------------------- History --------------------

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def rolling_avg(prices):
    if len(prices) < 3:
        return None
    recent = prices[-ROLLING_WINDOW_DAYS:]
    return mean(p["price"] for p in recent)


# -------------------- Telegram --------------------

def _post_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        print(f"Telegram error {r.status_code}: {r.text}", file=sys.stderr)
        r.raise_for_status()


def send_telegram(text):
    """Send a message to Telegram, splitting into chunks if over the limit."""
    if MUTE:
        print(f"[muted] Would have sent {len(text)} chars to Telegram.")
        return
    LIMIT = 3500
    if len(text) <= LIMIT:
        _post_telegram(text)
        return

    lines = text.split("\n")
    chunk = []
    size = 0
    chunks = []
    for line in lines:
        if size + len(line) + 1 > LIMIT and chunk:
            chunks.append("\n".join(chunk))
            chunk = []
            size = 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        chunks.append("\n".join(chunk))

    total = len(chunks)
    for i, c in enumerate(chunks, 1):
        suffix = f"\n\n<i>(part {i}/{total})</i>" if total > 1 else ""
        _post_telegram(c + suffix)
        time.sleep(0.5)


# -------------------- Air China / Chinese carriers likely to have luggage --------------------
# Budget carriers often charge for bags. For DXB -> China routes the relevant
# budget carriers are mostly Gulf/SE-Asian ones (AirAsia, Scoot, etc.).
_BUDGET_CARRIERS = {
    "FR",  # Ryanair
    "U2",  # easyJet
    "W6", "W4", "W9", "WZZ",  # Wizz Air variants
    "VY",  # Vueling
    "EW",  # Eurowings
    "PC",  # Pegasus
    "HV", "TO",  # Transavia
    "DY",  # Norwegian
    "F9",  # Frontier
    "NK",  # Spirit
    "TR",  # Scoot
    "AK",  # AirAsia
    "FD",  # Thai AirAsia
    "D7",  # AirAsia X
    "XJ",  # AirAsia X (alt)
    "QG",  # Citilink
    "JT",  # Lion Air
    "5J",  # Cebu Pacific
}


# -------------------- China season notes --------------------
# Best months to visit. Marked as travel context, NOT a buy signal.
# Peak season usually = peak prices.
_SEASON_CN = {
    # North China (continental climate, cold winters hot summers)
    "PEK": "Sep–Oct (autumn, clear skies)",
    "TSN": "Sep–Oct (autumn)",
    "DLC": "Jun–Sep (mild, beach season)",
    "TAO": "Jun–Aug (summer, beach)",

    # East China (4-season)
    "PVG": "Mar–May & Oct–Nov (mild)",
    "HGH": "Mar–May & Sep–Nov",
    "NKG": "Mar–May & Sep–Nov",
    "WUH": "Mar–May & Sep–Nov",

    # South China (subtropical, mild winters)
    "CAN": "Oct–Mar (cool, dry)",
    "SZX": "Oct–Mar (cool, dry)",
    "XMN": "Oct–Mar (cool, dry)",

    # Southwest
    "XIY": "Mar–May & Sep–Nov",
    "CTU": "Mar–May & Sep–Nov",
    "CKG": "Mar–May & Sep–Nov",
    "KMG": "Nov–Apr (dry)",

    # Hainan (tropical)
    "HAK": "Nov–Mar (warm, dry)",
    "SYX": "Nov–Mar (warm, dry)",

    # Greater China
    "HKG": "Oct–Dec (mild, dry)",
    "MFM": "Oct–Dec (mild, dry)",
    "TPE": "Oct–Dec (mild, dry)",
    "TSA": "Oct–Dec (mild, dry)",
    "KHH": "Oct–Dec (mild, dry)",
}


def season_note(dest):
    note = _SEASON_CN.get(dest)
    if note:
        return note
    # Fallback for cities not in the precise list
    return "Mar–May & Oct–Nov usually pleasant"


def baggage_hint(airline):
    """Best-effort baggage note. Inferred from the airline alone."""
    if airline and airline.upper() in _BUDGET_CARRIERS:
        return "  · <i>~cabin?</i>"
    return ""


def fmt_deal(d):
    arrow = d["cfg"]["arrow"]
    flag = d["cfg"]["flag"]
    city = d["city"]
    origin = d["origin"]
    price = d["price"]
    target = d["target"]
    dep = d["departure_at"] or "flexible"
    ret = d["return_at"]
    air = d["airline"]
    when = f"{fmt_date(dep)} → {fmt_date(ret)}" if ret else fmt_date(dep)
    nights = d.get("nights")
    if nights:
        when += f"  ({nights}n)"

    trend = ""
    avg = d.get("avg")
    if avg:
        if d["price"] <= avg * 0.95:
            trend = f"  📉 avg {CURRENCY_SYMBOL}{int(avg)}"
        elif d["price"] >= avg * 1.05:
            trend = f"  📈 avg {CURRENCY_SYMBOL}{int(avg)}"
        else:
            trend = f"  〰️ avg {CURRENCY_SYMBOL}{int(avg)}"

    lo = d.get("prev_lo")
    lo_str = f"  · low {CURRENCY_SYMBOL}{int(lo)}" if lo else ""

    line = (
        f"<b>{city}</b>  {origin}{arrow}  "
        f"<b>{CURRENCY_SYMBOL}{price}</b>{trend}"
        f"  (target {CURRENCY_SYMBOL}{target}){lo_str}\n"
        f"   {when}  · {air}{baggage_hint(air)}"
    )
    if d.get("link"):
        line += f'  · <a href="{d["link"]}">book</a>'
    line += f"\n   {flag} best season: {season_note(d['dest'])}"
    return line


# -------------------- Scan + digest --------------------

_origin_stats = {}  # origin -> [routes_with_data, routes_tried]


def scan_tier(cfg, destinations, history, today):
    deals = []
    checked = 0
    for origin in ORIGINS:
        stats = _origin_stats.setdefault(origin, [0, 0])
        for dest, (city, target) in destinations.items():
            key = f"{origin}-{dest}-{cfg['trip_type']}"
            cheapest = get_cheapest(origin, dest, cfg)
            stats[1] += 1
            if cheapest is None:
                print(f"  [{cfg['name']}] {origin}->{dest} {city}: no fares")
                continue
            checked += 1
            stats[0] += 1
            price = cheapest["price"]
            entry = history.get(key, {})
            if isinstance(entry, list):
                entry = {"series": entry, "lo": None}
            series = entry.get("series", [])
            prev_lo = entry.get("lo")

            avg = rolling_avg(series)
            below_target = price <= target * (1 - TARGET_MARGIN_PCT)
            big_drop = avg is not None and price <= avg * (1 - ROLLING_DROP_PCT)
            all_time_low = (
                prev_lo is not None
                and len(series) >= 3
                and price < prev_lo
            )

            print(
                f"  [{cfg['name']}] {origin}->{dest} {city}: "
                f"{CURRENCY_SYMBOL}{price} | target {CURRENCY_SYMBOL}{target}"
                f" | avg {CURRENCY_SYMBOL + str(int(avg)) if avg else 'n/a'}"
                f" | lo {CURRENCY_SYMBOL + str(int(prev_lo)) if prev_lo else 'n/a'}"
                f" | {'HIT' if (below_target or big_drop or all_time_low) else '-'}"
            )

            if below_target or big_drop or all_time_low:
                deals.append({
                    "cfg": cfg,
                    "origin": origin,
                    "dest": dest,
                    "city": city,
                    "price": price,
                    "target": target,
                    "avg": avg,
                    "prev_lo": prev_lo,
                    "airline": cheapest["airline"],
                    "departure_at": cheapest["departure_at"],
                    "return_at": cheapest["return_at"],
                    "nights": cheapest.get("nights"),
                    "broad": cheapest.get("broad", False),
                    "link": cheapest["link"],
                    "below_target": below_target,
                    "big_drop": big_drop,
                    "all_time_low": all_time_low,
                    "score": (target - price) / target,
                })

            series.append({"date": today, "price": price})
            new_lo = price if prev_lo is None else min(prev_lo, price)
            history[key] = {
                "series": series[-(ROLLING_WINDOW_DAYS * 2):],
                "lo": new_lo,
            }
    return deals, checked


def build_digest(deals, header):
    if not deals:
        return None
    # Priority order: all-time lows first, then big drops, then below-target.
    lows = sorted([d for d in deals if d.get("all_time_low")],
                  key=lambda d: d["score"], reverse=True)
    low_keys = {(d["origin"], d["dest"]) for d in lows}
    big = sorted([d for d in deals
                  if d["big_drop"] and (d["origin"], d["dest"]) not in low_keys],
                 key=lambda d: d["score"], reverse=True)
    big_keys = low_keys | {(d["origin"], d["dest"]) for d in big}
    cheap = sorted([d for d in deals
                    if d["below_target"] and (d["origin"], d["dest"]) not in big_keys],
                   key=lambda d: d["score"], reverse=True)
    lines = [header, ""]
    if lows:
        lines.append(f"🏆 <b>All-time lows</b>")
        lines += [fmt_deal(d) for d in lows]
        if big or cheap:
            lines.append("")
    if big:
        lines.append(f"📉 <b>Big drops vs recent average</b>")
        lines += [fmt_deal(d) for d in big]
        if cheap:
            lines.append("")
    if cheap:
        lines.append(f"💰 <b>Below target</b>")
        lines += [fmt_deal(d) for d in cheap]
    if any(d["airline"].upper() in _BUDGET_CARRIERS for d in deals):
        lines.append("")
        lines.append(
            "<i>~cabin? = budget carrier, likely hand-luggage only. "
            "Always confirm baggage at booking.</i>"
        )
    return "\n".join(lines).strip()


def scan_watchlist(history, today):
    """Fetch current cheapest fare for each watchlist route."""
    if not WATCHLIST:
        return None
    rows = []
    for dest in WATCHLIST:
        if dest not in CHINA_DESTINATIONS:
            print(f"  [watch] {dest}: not in CHINA_DESTINATIONS, skipping")
            continue
        label, target = CHINA_DESTINATIONS[dest]
        best = None
        for origin in ORIGINS:
            c = get_cheapest(origin, dest, CN)
            if c and (best is None or c["price"] < best["price"]):
                best = c
                best["origin"] = origin
        if best is None:
            rows.append((dest, label, None, None, target, None, ""))
            continue
        key = f"{best['origin']}-{dest}-{CN['trip_type']}"
        entry = history.get(key, {})
        if isinstance(entry, list):
            entry = {"series": entry, "lo": None}
        lo = entry.get("lo")
        avg = rolling_avg(entry.get("series", []))
        trend = ""
        if avg:
            if best["price"] <= avg * 0.95:
                trend = f"  📉 avg {CURRENCY_SYMBOL}{int(avg)}"
            elif best["price"] >= avg * 1.05:
                trend = f"  📈 avg {CURRENCY_SYMBOL}{int(avg)}"
            else:
                trend = f"  〰️ avg {CURRENCY_SYMBOL}{int(avg)}"
        is_deal = best["price"] <= target * (1 - TARGET_MARGIN_PCT)
        rows.append((dest, label, best, lo, target, is_deal, trend))

    if not rows:
        return None
    lines = [f"👀 <b>Watching · {fmt_date(today)}</b>", ""]
    for dest, label, best, lo, target, is_deal, trend in rows:
        if best is None:
            lines.append(f"<b>{label}</b>: no fares found (target {CURRENCY_SYMBOL}{target})")
            continue
        arrow = "→"
        price = best["price"]
        when = f"{fmt_date(best['departure_at'])} → {fmt_date(best['return_at'])}"
        nights = best.get("nights")
        when += f"  ({nights}n)" if nights else ""
        flag = "  · 🏆 <b>DEAL</b>" if is_deal else ""
        lo_str = f"  · low {CURRENCY_SYMBOL}{int(lo)}" if lo else ""
        line = (
            f"<b>{label}</b>  {best['origin']}{arrow}  "
            f"<b>{CURRENCY_SYMBOL}{price}</b>{trend}"
            f"  (target {CURRENCY_SYMBOL}{target}){flag}{lo_str}\n"
            f"   {when}  · {best['airline']}{baggage_hint(best['airline'])}"
        )
        if best["link"]:
            line += f'  · <a href="{best["link"]}">book</a>'
        line += f"\n   🇨🇳 best season: {season_note(dest)}"
        lines.append(line)
    return "\n".join(lines).strip()


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_disp = fmt_date(today)
    history = load_history()

    print(f"[{today}] DXB -> China scan starting (currency={CURRENCY}, origins={ORIGINS})")
    deals, checked = scan_tier(CN, CHINA_DESTINATIONS, history, today)

    total_routes = len(ORIGINS) * len(CHINA_DESTINATIONS)
    coverage = checked / max(total_routes, 1)
    print(f"Coverage: {checked}/{total_routes} routes returned data ({coverage:.0%})")
    for origin in ORIGINS:
        hit, tried = _origin_stats.get(origin, [0, 0])
        pct = hit / max(tried, 1)
        print(f"  origin {origin}: {hit}/{tried} routes returned data ({pct:.0%})")
    if coverage < 0.25:
        print("! Fewer than 25% of routes returned data -- API likely throttled or down. "
              "Leaving history untouched, not sending a digest.", file=sys.stderr)
        return 0

    watch_digest = scan_watchlist(history, today)
    save_history(history)

    header = (
        f"{CN['flag']} <b>DXB → China deal scan · {today_disp}</b>\n"
        f"   round-trip · 1 adult · currency {CURRENCY.upper()}"
    )
    digest = build_digest(deals, header)

    sent = 0
    if digest:
        send_telegram(digest)
        sent += 1
    if watch_digest:
        send_telegram(watch_digest)
        sent += 1

    if HEARTBEAT and not digest:
        send_telegram(
            f"{CN['flag']} <b>DXB → China scan · {today_disp}</b>\n"
            f"No deals today (scanned {checked} routes). "
            f"Targets are tight; nothing cleared them. Bot is running fine."
        )
        sent += 1

    if sent == 0:
        print(f"No messages sent. Checked {checked} routes.")
    else:
        print(f"Sent {sent} message(s); {len(deals)} deals across {checked} routes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())