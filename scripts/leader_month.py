"""Monthly leader reports for each One Piece leader channel.

For every leader channel in config.leader_channels (game: onepiece) this posts ONE message per
month and edits it in place on later runs:
  * weighted win rate this month (each event weighted by log2(players), so a 128-player cup
    counts more than an 8-player local)
  * this month's event winners on that leader
  * the "median top-cut list": card-by-card weighted median of every top-cut list this month,
    trimmed/filled to 50 cards, plus an import block for OPTCGSim / deck builders

Sources (all documented events with decklists):
  * Limitless Play API - every online One Piece event hosted there, ChinoizeCup included,
    with full decklists and W/L/T records
  * onepiece.limitlesstcg.com - offline majors (Regionals, Treasure Cups, Finals) with
    top-cut decklists (placings only, no records, so they count toward lists + winners)

  python scripts/leader_month.py           # post / update
  python scripts/leader_month.py --test    # print, post nothing
"""
import math
import re
import sys
from datetime import datetime

from common import (embed, http_get, http_json, load_config, load_json, log, now_la,
                    upsert_webhook)

PLAY = "https://play.limitlesstcg.com/api"
MAJORS = "https://onepiece.limitlesstcg.com"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def cut_size(players):
    return 8 if players >= 64 else 4 if players >= 24 else 2 if players >= 12 else 1


def weight(players):
    return math.log2(max(players, 4))


# --------------------------------------------------------------- sources ---
def play_events(month_start, min_players):
    """Online events this month from Limitless Play, with decklists + records."""
    out = []
    ts = http_json(f"{PLAY}/tournaments?game=OP&limit=500")
    for t in ts:
        if t.get("date", "") < month_start or (t.get("players") or 0) < min_players:
            continue
        try:
            st = http_json(f"{PLAY}/tournaments/{t['id']}/standings")
        except Exception as e:
            log(f"[play] {t['id']}: {e}")
            continue
        lists = []
        for p in st:
            dl, rec = p.get("decklist"), p.get("record") or {}
            if not dl or not dl.get("leader"):
                continue
            ld = dl["leader"]
            cards = {}
            names = {}
            for sect, items in dl.items():
                if sect == "leader" or not isinstance(items, list):
                    continue
                for c in items:
                    code = f"{c.get('set')}-{c.get('number')}"
                    cards[code] = cards.get(code, 0) + int(c.get("count") or 0)
                    names[code] = c.get("name") or code
            lists.append({
                "code": f"{ld.get('set')}-{ld.get('number')}", "leader": ld.get("name", ""),
                "player": p.get("name") or p.get("player"), "placing": p.get("placing") or 999,
                "wins": rec.get("wins", 0), "losses": rec.get("losses", 0), "ties": rec.get("ties", 0),
                "cards": cards, "names": names,
            })
        if lists:
            out.append({"name": t["name"], "players": t["players"], "date": t["date"][:10],
                        "url": f"https://play.limitlesstcg.com/tournament/{t['id']}/standings",
                        "lists": lists, "has_records": True})
    return out


def _major_date(txt):
    m = re.match(r"(\d{1,2}) (\w{3}) (\d{2})", txt.strip())
    if not m or m.group(2) not in MONTHS:
        return None
    return f"20{m.group(3)}-{MONTHS.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"


def major_events(month_start):
    """Offline majors this month from onepiece.limitlesstcg.com (top-cut lists, no records)."""
    out = []
    html = http_get(f"{MAJORS}/tournaments?time=1months")
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        link = re.search(r'href="(/tournaments/\d+)"', row)
        if len(cells) < 3 or not link:
            continue
        date = _major_date(re.sub("<[^>]+>", "", cells[0]))
        if not date or date < month_start[:10]:
            continue
        txt = [re.sub(r"<[^>]+>|\s+", " ", c).strip() for c in cells[1:]]
        # columns: date | flag | name | set | players | winner
        name = next((t for t in txt if t and not re.fullmatch(r"[A-Z]{2,3}\d{0,2}|\d+", t)), "Major")
        players = int(next((t for t in txt if re.fullmatch(r"\d+", t)), "0"))
        try:
            page = http_get(MAJORS + link.group(1))
        except Exception as e:
            log(f"[majors] {link.group(1)}: {e}")
            continue
        lists = []
        for r in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
            place = re.search(r"<td[^>]*>\s*(\d+)\s*</td>", r)
            dl = re.search(r'href="(/decks/list/\d+)"', r)
            pl = re.search(r'href="/players/\d+"[^>]*>([^<]+)<', r)
            if not place or not dl:
                continue
            placing = int(place.group(1))
            if placing > cut_size(players):
                continue
            try:
                dhtml = http_get(MAJORS + dl.group(1))
            except Exception:
                continue
            cards, names, leader = {}, {}, None
            for cnt, cid, nm in re.findall(
                    r'class="decklist-card" data-count="(\d+)" data-id="([A-Z0-9]+-\d{3})".*?class="card-name">([^<]+)<',
                    dhtml, re.S):
                if leader is None:
                    leader = (cid, re.sub(r"\s*\([^)]*\)\s*$", "", nm).strip())
                    continue
                cards[cid] = cards.get(cid, 0) + int(cnt)
                names[cid] = re.sub(r"\s*\([^)]*\)\s*$", "", nm).strip()
            if leader:
                lists.append({"code": leader[0], "leader": leader[1], "player": (pl.group(1).strip() if pl else "?"),
                              "placing": placing, "wins": 0, "losses": 0, "ties": 0,
                              "cards": cards, "names": names})
        if lists:
            out.append({"name": name, "players": players, "date": date, "url": MAJORS + link.group(1),
                        "lists": lists, "has_records": False})
    return out


# ---------------------------------------------------------------- maths ---
def weighted_median(pairs):
    """pairs: [(value, weight)] -> weighted median value."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return 0


def median_list(pool, size=50):
    """pool: [(cards dict, weight)]. Card-by-card weighted median, then trim/fill to `size`."""
    codes = set().union(*(c.keys() for c, _ in pool)) if pool else set()
    tw = sum(w for _, w in pool) or 1
    mean = {k: sum(c.get(k, 0) * w for c, w in pool) / tw for k in codes}
    deck = {k: weighted_median([(c.get(k, 0), w) for c, w in pool]) for k in codes}
    deck = {k: v for k, v in deck.items() if v > 0}
    total = sum(deck.values())
    while total < size:
        cands = [k for k in codes if deck.get(k, 0) < 4]
        if not cands:
            break
        k = max(cands, key=lambda k: mean[k] - deck.get(k, 0))
        deck[k] = deck.get(k, 0) + 1
        total += 1
    while total > size:
        k = min(deck, key=lambda k: mean[k] - deck[k] + 1)
        deck[k] -= 1
        total -= 1
        if deck[k] == 0:
            del deck[k]
    return deck


def _matches(lst, lc, colors):
    if lc.get("codes"):
        return lst["code"] in lc["codes"]
    if lc.get("name") and lst["leader"].lower() != lc["name"].lower():
        return False
    want = sorted(c.lower() for c in lc.get("colors", []))
    if want and lst["code"] in colors:
        return sorted(x.strip().lower() for x in re.split(r"[/;,]", colors[lst["code"]])) == want
    return True


# ---------------------------------------------------------------- report ---
def report(cfg, test=False):
    now = now_la()
    month_start = now.replace(day=1).strftime("%Y-%m-%dT00:00:00")
    month_key = now.strftime("%Y-%m")
    month_name = now.strftime("%B")
    mcfg = cfg.get("meta", {})
    events = play_events(month_start, mcfg.get("min_players", 8))
    try:
        events += major_events(month_start)
    except Exception as e:
        log(f"[majors] {e}")
    colors = load_json("op_leader_colors.json", {})
    n_events = len(events)
    log(f"[leaders] {n_events} events with decklists since {month_start[:10]}")

    for lc in cfg.get("leader_channels", []):
        if lc.get("game", "onepiece") != "onepiece" or not lc.get("webhook"):
            continue
        num = den = 0.0
        n_lists, evs, pool, winners, names = 0, set(), [], [], {}
        lead_code = None
        for ev in events:
            w = weight(ev["players"])
            for l in ev["lists"]:
                if not _matches(l, lc, colors):
                    continue
                lead_code = lead_code or l["code"]
                n_lists += 1
                evs.add(ev["name"])
                names.update(l["names"])
                games = l["wins"] + l["losses"] + l["ties"]
                if ev["has_records"] and games:
                    num += w * (l["wins"] + 0.5 * l["ties"])
                    den += w * games
                if l["placing"] <= cut_size(ev["players"]):
                    pool.append((l["cards"], w * (1.5 if l["placing"] == 1 else 1.0)))
                if l["placing"] == 1:
                    winners.append(f"**{l['player']}** · {ev['name']} ({ev['players']}p)")
        title = f"{lc['emoji']} {lc['label']} · {month_name} so far"
        if not n_lists:
            desc = f"No documented lists for this leader yet in {month_name}. Check back after this week's events."
        else:
            wr = f"**{100 * num / den:.1f}%** weighted win rate" if den else "win rate n/a (majors only)"
            lines = [f"{wr} · {n_lists} lists · {len(evs)} events"]
            if winners:
                lines.append("🏆 " + " / ".join(winners[:5]) + (f" +{len(winners) - 5} more" if len(winners) > 5 else ""))
            if len(pool) < 3:  # thin month: fall back to every list with a winning record
                pool = []
                for ev in events:
                    for l in ev["lists"]:
                        g = l["wins"] + l["losses"] + l["ties"]
                        if _matches(l, lc, colors) and g and l["wins"] / g >= 0.6:
                            pool.append((l["cards"], weight(ev["players"])))
            if pool:
                deck = median_list(pool)
                rows = sorted(deck.items(), key=lambda kv: (-kv[1], kv[0]))
                pretty = "\n".join(f"{n}x {names.get(c, c)[:28]} ({c})" for c, n in rows)
                imp = "\n".join([f"1x{lead_code}"] + [f"{n}x{c}" for c, n in rows])
                lines.append(f"\n**Median top-cut list** · built from {len(pool)} lists")
                lines.append(f"```\n{pretty}\n```")
                lines.append(f"Sim / builder import (tap to reveal): ||```\n{imp}\n```||")
            desc = "\n".join(lines)[:4000]
        payload = {"username": "TDM Leader Report", "embeds": [embed(
            title, color=0xE67E22, description=desc,
            footer=f"All documented events with decklists (Limitless online incl. ChinoizeCup + majors) · updated {now:%b %d}")]}
        if test:
            print(f"\n=== {lc['label']} ===\n{desc}")
            continue
        upsert_webhook(lc["webhook"], payload, key=f"leader:{lc['label']}:{month_key}")


if __name__ == "__main__":
    report(load_config(), test="--test" in sys.argv)
