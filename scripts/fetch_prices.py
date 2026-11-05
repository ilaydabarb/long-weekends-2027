#!/usr/bin/env python3
"""Refresh live long-weekend fares and hotel rates into data/prices.json.

Flights:
- Ryanair's public fare finder: live Ryanair prices from the London airports for
  every long-weekend pattern, one request per airport/date pair covering all
  destinations. Free, no key.
- Travelpayouts (Aviasales) Data API: the cheapest London return fares on any
  airline found in recent searches. Free; needs TRAVELPAYOUTS_TOKEN.
Hotels (optional): SerpApi Google Hotels for destinations listed in
data/hotel_watchlist.txt. Needs SERPAPI_KEY; capped per run to fit the free plan.
"""
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_FILE = os.path.join(ROOT, "data", "destinations.json")
PRICES_FILE = os.path.join(ROOT, "data", "prices.json")
WATCHLIST_FILE = os.path.join(ROOT, "data", "hotel_watchlist.txt")

YEAR = 2027
ORIGIN = "LON"  # all London airports
TP_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
SERP_URL = "https://serpapi.com/search.json"

# Travelpayouts prefers IATA city codes where a city has several airports
CITY_CODES = {"FCO": "ROM", "MXP": "MIL", "CDG": "PAR", "ARN": "STO", "GYD": "BAK"}
# Destinations without their own airport are searched via the gateway airport
VIA = {"grx": "AGP"}

RYANAIR_URL = "https://services-api.ryanair.com/farfnd/v4/roundTripFares"
RYANAIR_LONDON = ["STN", "LTN", "LGW", "SEN"]
USER_AGENT = "long-weekends-2027 personal trip planner"
# Long-weekend patterns as (start offset from Friday, nights), matching the page
PATTERNS = [(-1, 3), (0, 3), (-1, 4), (0, 4), (-2, 5), (-1, 5)]
# Secondary airports that serve a destination
EXTRA_AIRPORTS = {
    "rom": ["CIA"], "mil": ["BGY", "LIN"], "par": ["BVA", "ORY"], "vce": ["TSF"],
    "arn": ["NYO", "VST"], "osl": ["TRF"], "bru": ["CRL"], "gnt": ["CRL"],
    "muc": ["FMM"], "waw": ["WMI"], "flr": ["FLR"], "bio": ["EAS"],
    "tbs": ["KUT"], "cqt": ["GOA"], "rmi": ["RMI", "FRL"], "tfs": ["TFN"],
}
AIRLINES = {
    "FR": "Ryanair", "RK": "Ryanair", "U2": "easyJet", "EC": "easyJet", "W6": "Wizz Air", "W9": "Wizz Air",
    "BA": "British Airways", "LS": "Jet2", "VY": "Vueling", "TP": "TAP", "DY": "Norwegian", "D8": "Norwegian",
    "A3": "Aegean", "TK": "Turkish Airlines", "PC": "Pegasus", "VF": "AJet", "XQ": "SunExpress",
    "LH": "Lufthansa", "KL": "KLM", "AF": "Air France", "AZ": "ITA Airways", "IB": "Iberia", "SK": "SAS",
    "AY": "Finnair", "OS": "Austrian", "LX": "SWISS", "EI": "Aer Lingus", "J2": "AZAL", "TU": "Tunisair",
    "MS": "EgyptAir", "AT": "Royal Air Maroc", "LO": "LOT", "RO": "TAROM", "JU": "Air Serbia",
    "OU": "Croatia Airlines", "FI": "Icelandair", "BT": "airBaltic", "KM": "KM Malta", "EW": "Eurowings",
    "LG": "Luxair", "SN": "Brussels Airlines", "BY": "TUI", "S4": "Azores Airlines", "2L": "Helvetic",
}

TP_DELAY = float(os.environ.get("TP_DELAY", "1.1"))
RYANAIR_DELAY = float(os.environ.get("RYANAIR_DELAY", "1.0"))
HOTEL_SEARCHES_PER_RUN = int(os.environ.get("HOTEL_SEARCHES_PER_RUN", "3"))


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def month_key(m):
    return "%d-%02d" % (YEAR, m + 1)


def month_is_past(m, today):
    last = date(YEAR, m + 2, 1) - timedelta(days=1) if m < 11 else date(YEAR, 12, 31)
    return last < today


def get_json(url, params, retries=3):
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            raise


def qualifies(dep, ret):
    """3-5 nights, leaving Wed/Thu/Fri, and the stay includes a Friday and Saturday night."""
    nights = (ret - dep).days
    if nights < 3 or nights > 5 or dep.weekday() not in (2, 3, 4):
        return False
    stay = {dep + timedelta(days=i) for i in range(nights)}
    return any(d.weekday() == 4 and d + timedelta(days=1) in stay for d in stay)


def search_code(dest):
    if dest["id"] in VIA:
        return VIA[dest["id"]]
    return CITY_CODES.get(dest["code"], dest["code"])


def parse_fares(payload):
    """Turn a prices_for_dates response into {"dep|ret": [price, airline, stops, airport, link]}."""
    out = {}
    for t in payload.get("data") or []:
        try:
            dep = date.fromisoformat(t["departure_at"][:10])
            ret = date.fromisoformat(t["return_at"][:10])
        except (KeyError, TypeError, ValueError):
            continue
        price = t.get("price")
        if not isinstance(price, (int, float)) or price <= 0 or not qualifies(dep, ret):
            continue
        stops = max(t.get("transfers") or 0, t.get("return_transfers") or 0)
        code = t.get("airline") or ""
        # [price, airline, stops, London airport, booking path (Aviasales), source]
        rec = [round(price), AIRLINES.get(code, code), stops, t.get("origin_airport") or "", t.get("link") or "", "tp"]
        key = "%s|%s" % (dep.isoformat(), ret.isoformat())
        if key not in out or rec[0] < out[key][0]:
            out[key] = rec
    return out


def fetch_flights(dest, m, token):
    params = {
        "origin": ORIGIN,
        "destination": search_code(dest),
        "departure_at": month_key(m),
        "one_way": "false",
        "currency": "gbp",
        "sorting": "price",
        "limit": 1000,
        "page": 1,
        "token": token,
    }
    return parse_fares(get_json(TP_URL, params))


def airports_for(dest):
    codes = {dest["code"]} | set(EXTRA_AIRPORTS.get(dest["id"], []))
    if dest["id"] in VIA:
        codes.add(VIA[dest["id"]])
    if len(dest.get("sky", "")) == 3:
        codes.add(dest["sky"].upper())
    return codes


def weekend_searches(today):
    """Every (start date, nights) long-weekend pattern from tomorrow to the end of August."""
    out = []
    fri = date(YEAR, 1, 1)
    while fri <= date(YEAR, 8, 31):
        for offset, nights in PATTERNS:
            start = fri + timedelta(days=offset)
            if start > today:
                out.append((start, nights))
        fri += timedelta(days=7)
    return out


def parse_ryanair(payload):
    """Turn a fare-finder response into [(arrival airport, "dep|ret", record)]."""
    out = []
    for f in payload.get("fares") or []:
        try:
            ob, ib = f["outbound"], f["inbound"]
            origin = ob["departureAirport"]["iataCode"]
            arrival = ob["arrivalAirport"]["iataCode"]
            dep = ob["departureDate"][:10]
            ret = ib["departureDate"][:10]
            price = f["summary"]["price"]["value"]
        except (KeyError, TypeError):
            continue
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        if not qualifies(date.fromisoformat(dep), date.fromisoformat(ret)):
            continue
        # [price, airline, stops, London airport, arrival airport, source]; the page builds the Ryanair link
        rec = [round(price), "Ryanair", 0, origin, arrival, "fr"]
        out.append((arrival, "%s|%s" % (dep, ret), rec))
    return out


def fetch_ryanair(origin, start, nights):
    ret = start + timedelta(days=nights)
    params = {
        "departureAirportIataCode": origin,
        "outboundDepartureDateFrom": start.isoformat(),
        "outboundDepartureDateTo": start.isoformat(),
        "inboundDepartureDateFrom": ret.isoformat(),
        "inboundDepartureDateTo": ret.isoformat(),
        "durationFrom": nights,
        "durationTo": nights,
        "currency": "GBP",
        "market": "en-gb",
    }
    return parse_ryanair(get_json(RYANAIR_URL, params))


def refresh_ryanair(dests, prices, today):
    flights = prices.setdefault("flights", {})
    by_airport = {}
    for dest in dests:
        for code in airports_for(dest):
            by_airport.setdefault(code, []).append(dest["id"])
    ok, failed, added = 0, set(), 0
    for start, nights in weekend_searches(today):
        for origin in RYANAIR_LONDON:
            try:
                fares = fetch_ryanair(origin, start, nights)
                ok += 1
            except Exception as e:
                print("  ! Ryanair %s %s +%d: %s" % (origin, start, nights, e), file=sys.stderr)
                failed.add((origin, start.isoformat()))
                time.sleep(RYANAIR_DELAY)
                continue
            for arrival, key, rec in fares:
                for did in by_airport.get(arrival, []):
                    cur = flights.setdefault(did, {}).get(key)
                    if cur is None or rec[0] < cur[0]:
                        flights[did][key] = rec
                        added += 1
            time.sleep(RYANAIR_DELAY)
    print("Ryanair: %d searches ok, %d failed, %d fares merged" % (ok, len(failed), added))
    return failed


def restore_failed(prices, previous, failed):
    """Keep yesterday's Ryanair fare where today's search for that airport and date failed."""
    for did, old in previous.items():
        cur = prices["flights"].setdefault(did, {})
        for key, rec in old.items():
            if len(rec) > 5 and rec[5] == "fr" and (rec[3], key[:10]) in failed and key not in cur:
                cur[key] = rec


def second_friday(m):
    d = date(YEAR, m + 1, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d + timedelta(days=7)


def hotel_query(dest):
    return dest["city"].split(" (")[0].split(" & ")[0] + " hotels"


def parse_hotels(payload):
    rates = []
    for p in payload.get("properties") or []:
        rate = (p.get("rate_per_night") or {}).get("extracted_lowest")
        if isinstance(rate, (int, float)) and rate > 0:
            rates.append(rate)
    rates = sorted(rates)[:10]
    if len(rates) < 3:
        return None
    return {"nightly": round(statistics.median(rates)), "samples": len(rates)}


def fetch_hotel(dest, m, key):
    fri = second_friday(m)
    check_in, check_out = fri - timedelta(days=1), fri + timedelta(days=2)
    params = {
        "engine": "google_hotels",
        "q": hotel_query(dest),
        "check_in_date": check_in.isoformat(),
        "check_out_date": check_out.isoformat(),
        "adults": 2,
        "currency": "GBP",
        "gl": "uk",
        "hl": "en",
        "hotel_class": "3,4,5",
        "rating": "8",  # 4.0+ guest rating
        "sort_by": "3",  # lowest price
        "api_key": key,
    }
    result = parse_hotels(get_json(SERP_URL, params))
    if result:
        result.update({"checkin": check_in.isoformat(), "checked": now_iso()})
    return result


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_watchlist():
    try:
        with open(WATCHLIST_FILE) as f:
            return [ln.split("#")[0].strip() for ln in f if ln.split("#")[0].strip()]
    except OSError:
        return []


def refresh_flights(dests, prices, token, today):
    flights, checked = prices.setdefault("flights", {}), prices.setdefault("checked", {})
    ok = failed = 0
    for dest in dests:
        did = dest["id"]
        old = flights.get(did, {})
        fresh = {}
        for m in dest["months"]:
            if month_is_past(m, today):
                continue
            mk = month_key(m)
            try:
                fresh.update(fetch_flights(dest, m, token))
                checked.setdefault(did, {})[mk] = now_iso()
                ok += 1
            except Exception as e:  # keep yesterday's fares for this month if today's call fails
                print("  ! %s %s: %s" % (did, mk, e), file=sys.stderr)
                fresh.update({k: v for k, v in old.items() if k.startswith(mk)})
                failed += 1
            time.sleep(TP_DELAY)
        flights[did] = {k: v for k, v in fresh.items() if date.fromisoformat(k[:10]) >= today}
    print("Flights: %d month searches ok, %d failed, %d live fares stored"
          % (ok, failed, sum(len(v) for v in flights.values())))


def refresh_hotels(dests, prices, key, today):
    hotels = prices.setdefault("hotels", {})
    by_id = {d["id"]: d for d in dests}
    jobs = []
    for did in read_watchlist():
        dest = by_id.get(did)
        if not dest:
            print("  ! watchlist id %r not found" % did, file=sys.stderr)
            continue
        for m in dest["months"]:
            if not month_is_past(m, today):
                last = (hotels.get(did, {}).get(month_key(m)) or {}).get("checked", "")
                jobs.append((last, did, m))
    jobs.sort()  # never-checked and stalest first
    done = 0
    for _, did, m in jobs[:HOTEL_SEARCHES_PER_RUN]:
        try:
            result = fetch_hotel(by_id[did], m, key)
            if result:
                hotels.setdefault(did, {})[month_key(m)] = result
                done += 1
        except Exception as e:
            print("  ! hotel %s %s: %s" % (did, month_key(m), e), file=sys.stderr)
    print("Hotels: %d of %d watchlist searches refreshed this run" % (done, min(len(jobs), HOTEL_SEARCHES_PER_RUN)))


def main():
    token = os.environ.get("TRAVELPAYOUTS_TOKEN", "").strip()
    serp = os.environ.get("SERPAPI_KEY", "").strip()
    dests = load_json(DEST_FILE, {}).get("destinations", [])
    prices = load_json(PRICES_FILE, {})
    today = date.today()
    ids = {d["id"] for d in dests}
    for section in ("flights", "checked", "hotels"):
        prices[section] = {k: v for k, v in prices.get(section, {}).items() if k in ids}
    previous = {k: dict(v) for k, v in prices["flights"].items()}

    if token:
        refresh_flights(dests, prices, token, today)
    else:
        print("TRAVELPAYOUTS_TOKEN not set: skipping other airlines, Ryanair only", file=sys.stderr)
        prices["flights"] = {}
    failed = refresh_ryanair(dests, prices, today)
    restore_failed(prices, previous, failed)
    for did in list(prices["flights"]):
        prices["flights"][did] = {k: v for k, v in prices["flights"][did].items()
                                  if date.fromisoformat(k[:10]) > today}
    if serp:
        refresh_hotels(dests, prices, serp, today)
    prices["updated"] = now_iso()
    prices["currency"] = "GBP"
    with open(PRICES_FILE, "w") as f:
        json.dump(prices, f, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    main()
