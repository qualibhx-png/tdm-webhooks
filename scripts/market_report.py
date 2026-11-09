"""Nightly 9:00 PM PT market report -> #market-movers and #sales-anomalies.

RAW cards: TCGplayer market prices via tcgcsv.com (free nightly mirror of TCGplayer's API).
GRADED (PSA 10 / BGS 10 / Grade 9.5 / CGC 10): PriceCharting Prices API (needs a
PRICECHARTING_TOKEN from a PriceCharting subscription) for cards in data/graded_watchlist.json.

  python scripts/market_report.py --force      # run now regardless of the clock
  python scripts/market_report.py --force --test
"""
import os
import sys
import urllib.parse

from common import webhook_url, embed, http_json, load_config, load_json, log, now_la, post_webhook, save_json, with_ping
from comps import get_comps, remaining

TCGCSV = "https://tcgcsv.com/tcgplayer"
PC = "https://www.pricecharting.com/api"
import re
SEALED_RX = re.compile(r"booster (box|bundle|pack|display|case)|elite trainer|\bETB\b|display|case\b|collection|starter deck|premium|tin\b|blister|build & battle|ultra.premium|double pack|gift box|carton", re.I)
PREMIUM_NAME_RX = re.compile(r"\((?:Manga|Alternate Art|Parallel|SP|Special|Treasure|Gold|Serial|Wanted Poster|Full Art|Signature|Showcase|Overnumbered)[^)]*\)|Manga Rare|Special Illustration|Illustration Rare|Hyper Rare|Gold Star", re.I)
PREMIUM_RARITY_RX = re.compile(r"^(SP|TR|Manga|Treasure Rare|Special Illustration Rare|Illustration Rare|Hyper Rare|Showcase|Overnumbered|Signature)", re.I)
GRADES = [  # (PriceCharting field, label)
    ("loose-price", "Raw"), ("box-only-price", "Grade 9.5 (BGS/any)"), ("manual-only-price", "PSA 10"),
    ("bgs-10-price", "BGS 10"), ("condition-17-price", "CGC 10"),
]


def money(x):
    return f"${x:,.2f}"


def pct(a, b):
    return (b - a) / a * 100 if a else 0.0


# ------------------------------------------------------------- raw side ---
def resolve_category(name_match):
    for c in http_json(f"{TCGCSV}/categories")["results"]:
        if name_match.lower() in (c.get("displayName") or c.get("name") or "").lower():
            return c["categoryId"]
    return None


LEADER_COLORS = {}


def snapshot_game(game):
    cat = game.get("tcgcsv_category_id") or resolve_category(game["tcgcsv_category_match"])
    if not cat:
        log(f"[{game['name']}] tcgcsv category not found")
        return None, None
    groups = http_json(f"{TCGCSV}/{cat}/groups")["results"]
    groups.sort(key=lambda g: g.get("publishedOn") or "", reverse=True)
    groups = groups[: game.get("max_sets", 80)]
    prices, index = {}, {}
    for g in groups:
        gid = g["groupId"]
        try:
            prods = http_json(f"{TCGCSV}/{cat}/{gid}/products")["results"]
            prs = http_json(f"{TCGCSV}/{cat}/{gid}/prices")["results"]
        except Exception as e:
            log(f"[{game['name']}] group {gid}: {e}")
            continue
        for p in prods:
            ext = {d.get("name"): d.get("value") for d in p.get("extendedData", [])}
            num = ext.get("Number") or ""
            sealed = (not num) and bool(SEALED_RX.search(p.get("name", "")))
            premium = bool(num) and bool(PREMIUM_NAME_RX.search(p.get("name", "")) or PREMIUM_RARITY_RX.search(ext.get("Rarity") or ""))
            index[str(p["productId"])] = [p.get("name", ""), num, p.get("url", ""), g.get("name", ""), sealed, premium]
            if game.get("slug") == "onepiece" and (ext.get("CardType") or "").lower() == "leader" and ext.get("Color"):
                LEADER_COLORS.setdefault(num, ext["Color"])
        for r in prs:
            if r.get("marketPrice"):
                prices[f"{r['productId']}:{r.get('subTypeName', '')}"] = round(r["marketPrice"], 2)
    return prices, index


def movers(cfg, today, yesterday, index, sealed):
    """Return (gainers, losers, anomalies) as row tuples (pct, before, now, key)."""
    m = cfg["market"]
    floor = m["sealed_min_price"] if sealed else m["min_price"]
    rows = []
    for key, now in today.items():
        before = yesterday.get(key)
        info = index.get(key.split(":", 1)[0])
        if not before or not info or bool(info[4] if len(info) > 4 else False) != sealed:
            continue
        if max(now, before) < floor:
            continue
        rows.append((pct(before, now), before, now, key))
    rows.sort()
    gain = [r for r in reversed(rows) if r[0] > 0][: m["top_n"]]
    lose = [r for r in rows if r[0] < 0][: m["top_n"]]
    a_pct = m["sealed_anomaly_pct"] if sealed else m["anomaly_pct"]
    anomalies = [r for r in rows if abs(r[0]) >= a_pct and r[2] >= m["anomaly_min_price"]]
    return gain, lose, anomalies


def fmt(r, index):
    pid, sub = r[3].split(":", 1)
    info = index.get(pid, ["Unknown", "", "", ""])
    name, num, url = info[0], info[1], info[2]
    label = f"{name} {('(' + num + ')') if num else ''}{' · ' + sub if sub and sub != 'Normal' else ''}"
    return f"[{label.strip()}]({url}) · {money(r[1])} → **{money(r[2])}** ({r[0]:+.1f}%)"


# ------------------------------------------------ playable vs collector ---
def meta_set(cfg):
    """{card number: lists} from recent top cuts (One Piece via ChinoizeCup Stats). Cached per day."""
    st = load_json("meta_cards.json", {})
    today = now_la().date().isoformat()
    if st.get("date") == today:
        return st.get("counts", {}), st.get("lists", 0)
    try:
        from chinoize import meta_cards
        counts, lists = meta_cards(cups=cfg.get("meta", {}).get("cups_for_playables", 6), top=cfg.get("meta", {}).get("top_cut", 8))
    except Exception as e:
        log(f"[meta] {e}")
        return st.get("counts", {}), st.get("lists", 0)
    save_json("meta_cards.json", {"date": today, "counts": counts, "lists": lists})
    return counts, lists


def split_movers(cfg, game, today, yesterday, index, meta, lists):
    """Return (playable_rows, collector_rows) as (pct, before, now, key, label)."""
    m = cfg["market"]
    cheapest = {}  # number -> (price, key) among non-premium singles
    for key, now in today.items():
        info = index.get(key.split(":", 1)[0])
        if not info or len(info) < 6 or info[4] or info[5] or not info[1]:
            continue
        if info[1] not in cheapest or now < cheapest[info[1]][0]:
            cheapest[info[1]] = (now, key)
    playable, collector = [], []
    for key, now in today.items():
        before = yesterday.get(key)
        info = index.get(key.split(":", 1)[0])
        if not before or not info or len(info) < 6 or max(now, before) < m["min_price"]:
            continue
        ch = pct(before, now)
        if abs(ch) < m.get("split_min_move_pct", 8):
            continue
        num, sealed, premium = info[1], info[4], info[5]
        played = meta.get(num, 0)
        if sealed or premium:
            if sealed and max(now, before) < m["sealed_min_price"]:
                continue
            label = "📦 Sealed" if sealed else (f"⚔️ also played ({played}/{lists} lists)" if played else "🔥 Hype")
            collector.append((ch, before, now, key, label))
        elif game.get("slug") == "onepiece" and played and cheapest.get(num, (0, None))[1] == key:
            playable.append((ch, before, now, key, f"📊 Meta: in {played} of {lists} top-cut lists"))
    by = lambda rows: sorted(rows, key=lambda r: -abs(r[0]))[: m["top_n"]]
    return by(playable), by(collector)


def fmt_split(r, index, emoji):
    arrow = "📈" if r[0] > 0 else "📉"
    return f"{arrow} {emoji} {fmt(r, index)} — {r[4]}"


# ---------------------------------------------------------- graded side ---
def graded_report(cfg):
    token = os.environ.get("PRICECHARTING_TOKEN", "").strip()
    watch = load_json("graded_watchlist.json", [])
    if not token or not watch:
        return None
    prev = load_json("graded_snapshot.json", {})
    snap, movers, anomalies = {}, [], []
    m = cfg["market"]
    for item in watch:
        try:
            if not item.get("id"):
                res = http_json(f"{PC}/products?t={token}&q={urllib.parse.quote(item['q'])}")
                if not res.get("products"):
                    log(f"[graded] no match for {item['q']}")
                    continue
                item["id"] = res["products"][0]["id"]
            d = http_json(f"{PC}/product?t={token}&id={item['id']}")
        except Exception as e:
            log(f"[graded] {item}: {e}")
            continue
        pid = str(item["id"])
        name = f"{d.get('product-name', item['q'])} ({d.get('console-name', '')})"
        url = f"https://www.pricecharting.com/game/{d.get('id', pid)}"
        cur = {f: (d.get(f) or 0) / 100 for f, _ in GRADES}
        cur["vol"] = int(d.get("sales-volume") or 0)
        snap[pid] = cur
        old = prev.get(pid)
        if not old:
            continue
        for f, label in GRADES[1:]:  # graded tiers only
            if cur[f] and old.get(f):
                ch = pct(old[f], cur[f])
                if abs(ch) >= m["graded_mover_pct"]:
                    movers.append((ch, f"[{name}]({url}) **{label}** · {money(old[f])} → **{money(cur[f])}** ({ch:+.1f}%)"))
                if abs(ch) >= m["graded_anomaly_pct"]:
                    anomalies.append(f"💲 Price spike · [{name}]({url}) **{label}** {ch:+.1f}% → {money(cur[f])}")
        vol_delta = cur["vol"] - old.get("vol", cur["vol"])
        if vol_delta >= m["graded_volume_spike"]:
            anomalies.append(f"📦 Volume spike · [{name}]({url}) · ~{vol_delta} more sales than yesterday's yearly count")
    save_json("graded_watchlist.json", watch)  # caches resolved ids
    save_json("graded_snapshot.json", snap)
    movers.sort(key=lambda x: -abs(x[0]))
    return [t for _, t in movers[:15]], anomalies


# ------------------------------------------------- graded via CompsAPI ---
def graded_comps_report(cfg):
    """Free-tier graded tracking: real eBay sales for a small watchlist, within the monthly budget."""
    import os
    if not os.environ.get("COMPSAPI_KEY", "").strip():
        return None
    watch = load_json("graded_comps_watchlist.json", [])
    reserve = cfg["comps"]["reserve_for_price_checks"]
    prev = load_json("graded_comps_snapshot.json", {})
    snap, movers, anomalies = dict(prev), [], []
    today = now_la().date()
    for item in watch:
        if remaining() <= reserve:
            log("[comps] nightly budget used; keeping the rest for #buy-sell price checks")
            break
        d = get_comps(item["name"], item["num"], item["grade"], min_remaining=reserve)
        if not d or d.get("budget_out") or not d.get("median"):
            continue
        key = f"{item['name']} {item['num']} {item['grade']}"
        recent = [s for s in d["sales"] if s["date"] and (today - __import__("datetime").date.fromisoformat(s["date"][:10])).days <= 1]
        old = prev.get(key)
        snap[key] = {"median": d["median"], "count": d["count"]}
        if old and old.get("median"):
            ch = pct(old["median"], d["median"])
            if abs(ch) >= cfg["market"]["graded_mover_pct"]:
                movers.append(f"**{key}** · median {money(old['median'])} → **{money(d['median'])}** ({ch:+.1f}%)")
            if abs(ch) >= cfg["market"]["graded_anomaly_pct"]:
                anomalies.append(f"💲 Price spike · **{key}** {ch:+.1f}% → {money(d['median'])}")
        if len(recent) >= cfg["market"]["graded_volume_spike"]:
            anomalies.append(f"📦 Volume spike · **{key}** · {len(recent)} eBay sales in the last 24h")
        if recent:
            hi = max(recent, key=lambda s: s["price"])
            if d["median"] and hi["price"] >= d["median"] * (1 + cfg["market"]["graded_anomaly_pct"] / 100):
                anomalies.append(f"🔥 Outlier sale · **{key}** sold {money(hi['price'])} vs {money(d['median'])} median")
    save_json("graded_comps_snapshot.json", snap)
    return movers, anomalies


# ------------------------------------------------------------------ run ---
def main():
    cfg = load_config()
    force, test = "--force" in sys.argv, "--test" in sys.argv
    now = now_la()
    state = load_json("market_state.json", {})
    if not force and (now.hour != cfg["market"]["post_hour_pt"] or state.get("last") == now.date().isoformat()):
        print(f"Not report time ({now:%H:%M} PT); exiting.")
        return
    day = now.strftime("%a %b %d, %Y")
    anomaly_lines, cross_gain, cross_lose = [], [], []
    play_lines, coll_lines = [], []
    meta, lists = meta_set(cfg)
    for game in cfg["market_games"]:
        if not game.get("enabled", True):
            continue
        slug = game["slug"]
        today, index = snapshot_game(game)
        if today is None:
            continue
        yesterday = load_json(f"snap_{slug}.json", {})
        save_json(f"snap_{slug}.json", today)
        save_json(f"index_{slug}.json", index)
        if not yesterday:
            log(f"[{slug}] first snapshot saved; movers start tomorrow")
            continue
        pl, co = split_movers(cfg, game, today, yesterday, index, meta, lists)
        play_lines += [(abs(r[0]), fmt_split(r, index, game["emoji"])) for r in pl]
        coll_lines += [(abs(r[0]), fmt_split(r, index, game["emoji"])) for r in co]
        sg, sl, sa = movers(cfg, today, yesterday, index, sealed=False)
        bg, bl, ba = movers(cfg, today, yesterday, index, sealed=True)
        f = lambda rows: "\n".join(fmt(r, index) for r in rows) or "No qualifying moves"
        hook = game.get("webhook") if webhook_url(game.get("webhook")) else cfg["webhooks"]["market_movers"]
        post_webhook(hook, {"username": f"{game['name']} Market", "embeds": [embed(
            f"{game['emoji']} {game['name']} · Nightly Market Report · {day}", color=game.get("color", 0x2ECC71),
            fields=[("📈 Singles: Top Gainers", f(sg), False), ("📉 Singles: Top Losers", f(sl), False),
                    ("📦 Sealed: Top Gainers", f(bg), False), ("📦 Sealed: Top Losers", f(bl), False)],
            footer=f"TCGplayer market price (via tcgcsv.com) · day-over-day · singles ≥{money(cfg['market']['min_price'])} · sealed ≥{money(cfg['market']['sealed_min_price'])}")]}, dry_run=test)
        for r in sg[:5] + bg[:5]:
            cross_gain.append((r[0], f"{game['emoji']} " + fmt(r, index)))
        for r in sl[:5] + bl[:5]:
            cross_lose.append((r[0], f"{game['emoji']} " + fmt(r, index)))
        anomaly_lines += [f"{game['emoji']} {fmt(r, index)}" for r in sa[:10]] + [f"{game['emoji']} 📦 {fmt(r, index)}" for r in ba[:5]]

    if LEADER_COLORS and not test:
        save_json("op_leader_colors.json", LEADER_COLORS)
    if play_lines:
        play_lines.sort(key=lambda x: -x[0])
        post_webhook(cfg["webhooks"].get("playable_movers", "WEBHOOK_PLAYABLE_MOVERS"), {"username": "TDM Playable Movers", "embeds": [embed(
            f"⚔️ Playable Movers · {day}", color=0xE67E22, description="\n".join(t for _, t in play_lines[:15]),
            footer="Cards seen in recent top cuts (ChinoizeCup Stats) · cheapest NM printing · Not financial advice.")]}, dry_run=test)
    if coll_lines:
        coll_lines.sort(key=lambda x: -x[0])
        post_webhook(cfg["webhooks"].get("collector_movers", "WEBHOOK_COLLECTOR_MOVERS"), {"username": "TDM Collector Movers", "embeds": [embed(
            f"💎 Collector Movers · {day}", color=0x9B59B6, description="\n".join(t for _, t in coll_lines[:15]),
            footer="Alt arts, manga, SP, special illustrations and sealed · Not financial advice. Do your own research.")]}, dry_run=test)
    if cross_gain or cross_lose:
        cross_gain.sort(key=lambda x: -x[0]); cross_lose.sort(key=lambda x: x[0])
        post_webhook(cfg["webhooks"]["market_movers"], with_ping({"username": "TDM Market Movers", "content": "Tonight's market report is up.", "embeds": [embed(
            f"🌐 All Games · Biggest Movers · {day}", color=0x2ECC71,
            fields=[("📈 Top Gainers", "\n".join(t for _, t in cross_gain[:8]), False),
                    ("📉 Top Losers", "\n".join(t for _, t in cross_lose[:8]), False)],
            footer="Pokémon · One Piece · Riftbound · singles + sealed · full reports in each game's market channel")]}, "market_alerts", cfg), dry_run=test)

    graded = graded_report(cfg) or graded_comps_report(cfg)
    if graded:
        gmovers, ganom = graded
        post_webhook(cfg["webhooks"]["market_movers"], {"username": "TDM Market Movers", "embeds": [embed(
            f"🏅 Graded Movers (PSA 10 · BGS 10 · 9.5 · CGC 10) · {day}", color=0xF1C40F,
            description="\n".join(gmovers) or "No graded moves past threshold today.",
            footer="Watchlist cards only · PriceCharting values or eBay sold medians (CompsAPI)")]}, dry_run=test)
        anomaly_lines += ganom
    post_webhook(cfg["webhooks"]["sales_anomalies"], {"username": "TDM Sales Anomalies", "embeds": [embed(
        f"🚨 Sales Anomalies · {day}", color=0xE74C3C,
        description="\n".join(anomaly_lines[:30]) or "Quiet day. Nothing unusual.",
        footer=f"Raw: ≥{cfg['market']['anomaly_pct']}% move on cards ≥{money(cfg['market']['anomaly_min_price'])} · Graded: price/volume spikes")]}, dry_run=test)
    if not test:
        state["last"] = now.date().isoformat()
        save_json("market_state.json", state)


if __name__ == "__main__":
    main()
