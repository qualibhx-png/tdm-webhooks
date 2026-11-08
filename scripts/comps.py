"""CompsAPI (compsapi.com): real eBay sold prices, graded or raw. Free tier = 100 requests/month.

Every lookup is cached for 24h and counted against a monthly budget (config.comps.monthly_budget)
so the free tier never runs dry mid-month. Price checks in #buy-sell get priority; the nightly
graded watchlist only uses what's left above config.comps.reserve_for_price_checks.
"""
import os
import re
import time
import urllib.parse

from common import http_json, load_json, log, now_la, save_json

API = "https://api.compsapi.com/v1/comps"
CACHE = "comps_cache.json"


def parse_grade(grade):
    """'PSA 10' -> {grader: PSA, grade: 10}; 'Raw' -> {graded: false}; Black Label -> BGS 10 + keyword."""
    g = grade.strip()
    if re.search(r"raw|ungraded|nm|near mint", g, re.I):
        return {"graded": "false"}, ""
    extra = ""
    if re.search(r"black\s*label|\bBL\b", g, re.I):
        return {"grader": "BGS", "grade": "10"}, " black label"
    m = re.search(r"(PSA|BGS|BECKETT|CGC|SGC|TAG|ACE)\s*([0-9]+(?:\.5)?)", g, re.I)
    if m:
        grader = "BGS" if m.group(1).upper() == "BECKETT" else m.group(1).upper()
        return {"grader": grader, "grade": m.group(2)}, extra
    m = re.search(r"([0-9]+(?:\.5)?)", g)
    return ({"graded": "true", "grade": m.group(1)} if m else {"graded": "true"}), extra


def _budget():
    st = load_json(CACHE, {"month": "", "used": 0, "items": {}})
    month = now_la().strftime("%Y-%m")
    if st.get("month") != month:
        st.update(month=month, used=0)
    return st


def remaining():
    from common import load_config
    cfg = load_config()["comps"]
    return cfg["monthly_budget"] - _budget()["used"]


def get_comps(name, num, grade, min_remaining=0):
    """Return dict(median, count, sales=[{title, price, date, url}], query) or None."""
    key = os.environ.get("COMPSAPI_KEY", "").strip()
    if not key:
        return None
    params, extra = parse_grade(grade)
    q = f"{name} {num}{extra}".strip()
    ck = q.lower() + "|" + "|".join(f"{k}={v}" for k, v in sorted(params.items()))
    st = _budget()
    hit = st["items"].get(ck)
    if hit and time.time() - hit["t"] < 24 * 3600:
        return hit["data"]
    from common import load_config
    budget = load_config()["comps"]["monthly_budget"]
    if budget - st["used"] <= min_remaining:
        log("[comps] monthly budget reached; skipping live lookup")
        return {"budget_out": True}
    url = API + "?" + urllib.parse.urlencode({"q": q, "cards": 1, "exclude": "junk", **params})
    try:
        raw = http_json(url, {"X-API-Key": key})
    except Exception as e:
        log(f"[comps] {e}")
        return None
    st["used"] += 1
    data = normalize(raw, q)
    st["items"][ck] = {"t": time.time(), "data": data}
    # keep the cache small
    st["items"] = dict(sorted(st["items"].items(), key=lambda kv: -kv[1]["t"])[:300])
    save_json(CACHE, st)
    return data


def _num(x):
    try:
        return float(str(x).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def normalize(raw, q):
    """Defensive parse: CompsAPI returns stats + recent sales; field names are read loosely."""
    stats = raw.get("stats") or raw.get("summary") or raw
    sales = raw.get("sales") or raw.get("comps") or raw.get("items") or raw.get("results") or []
    rows = []
    for s in sales:
        price = _num(s.get("price") or s.get("sold_price") or s.get("total_price") or (s.get("price_info") or {}).get("value"))
        date = s.get("sold_date") or s.get("date") or s.get("end_date") or ""
        rows.append({"title": (s.get("title") or "")[:90], "price": price, "date": str(date)[:10],
                     "url": s.get("url") or s.get("item_url") or s.get("link") or "",
                     "best_offer": bool(s.get("best_offer") or s.get("best_offer_accepted"))})
    rows = [r for r in rows if r["price"]]
    rows.sort(key=lambda r: r["date"], reverse=True)
    median = _num(stats.get("median") or stats.get("median_price"))
    if median is None and rows:
        ps = sorted(r["price"] for r in rows)
        median = ps[len(ps) // 2]
    count = stats.get("count") or stats.get("sales_count") or stats.get("total") or len(rows)
    return {"query": q, "median": median, "count": count, "sales": rows[:10]}


def format_last3(data):
    if not data:
        return None
    if data.get("budget_out"):
        return "Monthly free lookups used up. Resets on the 1st. Use the eBay sold link below."
    if not data["sales"]:
        return f"No eBay sales found in the last year for *{data['query']}*."
    lines = []
    for r in data["sales"][:3]:
        bo = " (Best Offer)" if r["best_offer"] else ""
        link = f"[eBay]({r['url']})" if r["url"] else ""
        lines.append(f"**${r['price']:,.2f}**{bo} · {r['date']} {link}")
    med = f"\nMedian **${data['median']:,.2f}** across {data['count']} sales (1 yr)" if data.get("median") else ""
    return "\n".join(lines) + med
