"""Post Top 4 (players + decks + decklist links) from big events into each game's
#...-tournament-results forum, tagged ONLINE or OFFLINE with the event name.

Sources:
  1. Limitless TCG API (play.limitlesstcg.com) - structured standings, isOnline flag.
  2. Results-page watchers (Limitless majors pages, ChinoizeCup stats, RiftDecks, ...):
     new event pages are detected, Top 4 rows are parsed best-effort, link always included.

  python scripts/tournaments.py --test       # show what would be posted, post nothing
  python scripts/tournaments.py --list-games # print Limitless game IDs (needs API key)
"""
import re
import sys
from datetime import datetime, timedelta, timezone

from common import PREVIEW, embed, extract_links, http_get, http_json, load_config, load_json, log, post_webhook, save_json, strip_tags, with_ping

STATE = "tournament_state.json"
LIMITLESS = "https://play.limitlesstcg.com/api"
ORD = {1: "🥇 1st", 2: "🥈 2nd", 3: "🥉 3rd", 4: "4th"}


def limitless_headers():
    import os
    key = os.environ.get("LIMITLESS_API_KEY", "").strip()
    return {"X-Access-Key": key} if key else {}


def tag(online):
    return "🌐 ONLINE" if online else "🏟️ OFFLINE"


def guess_online(name, default):
    if default in ("Online", "Offline"):
        return default == "Online"
    return bool(re.search(r"\bonline\b|\bTTS\b|\bsim\b|webcam|\bPTCGL\b|\bTCG\s*Live\b", name, re.I))


# ------------------------------------------------------- Limitless API ---
def limitless_events(game_cfg, seen, test):
    out = []
    lid = game_cfg.get("limitless_game")
    if not lid:
        return out
    try:
        events = http_json(f"{LIMITLESS}/tournaments?game={lid}&limit=50", limitless_headers())
    except Exception as e:
        log(f"[limitless {lid}] {e}")
        return out
    cutoff = datetime.now(timezone.utc) - timedelta(days=4)
    for ev in events:
        eid = f"limitless:{ev['id']}"
        if eid in seen or (ev.get("players") or 0) < game_cfg.get("min_players", 32):
            continue
        try:
            when = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        except Exception:
            continue
        if when > datetime.now(timezone.utc) or when < cutoff:
            continue
        try:
            det = http_json(f"{LIMITLESS}/tournaments/{ev['id']}/details", limitless_headers())
            standings = http_json(f"{LIMITLESS}/tournaments/{ev['id']}/standings", limitless_headers())
        except Exception as e:
            log(f"[limitless {ev['id']}] {e}")
            continue
        if not standings or not standings[0].get("placing"):
            continue  # not finished yet
        top = sorted([s for s in standings if s.get("placing")], key=lambda s: s["placing"])[:4]
        base = f"https://play.limitlesstcg.com/tournament/{ev['id']}"
        rows = []
        for s in top:
            deck = (s.get("deck") or {}).get("name") or "Unknown deck"
            dl = f"{base}/player/{s.get('player')}/decklist" if s.get("decklist") is not None and s.get("player") else base + "/standings"
            rows.append((ORD.get(s["placing"], f"{s['placing']}th"), f"**{s.get('name', '?')}**: [{deck}]({dl})"))
        out.append(dict(id=eid, name=ev["name"], online=bool(det.get("isOnline", True)), url=base + "/standings",
                        players=ev.get("players"), date=when.strftime("%b %d, %Y"), rows=rows, source="Limitless TCG"))
    return out


# --------------------------------------------------- results-page watch ---
def parse_top4(html):
    """Best-effort: find table rows / list items that start with a 1st-4th placing."""
    rows = []
    for chunk in re.findall(r"<tr[\s\S]*?</tr>|<li[\s\S]*?</li>", html, re.I):
        text = strip_tags(chunk)
        m = re.match(r"^(?:#\s*)?([1-4])(?:st|nd|rd|th)?\b[\s.:|-]+(.{3,140})", text)
        if m:
            place = int(m.group(1))
            if place not in [r[0] for r in rows]:
                rows.append((place, m.group(2).strip()))
        if len(rows) == 4:
            break
    return [(ORD[p], t) for p, t in sorted(rows)]


def chinoize_events(game_cfg, src, seen):
    """ChinoizeCup Stats: real standings with leader, color and decklist links."""
    from chinoize import color_emoji, list_tournaments, tournament
    colors = load_json("op_leader_colors.json", {})
    out = []
    for t in list_tournaments(limit=src.get("max_new", 6)):
        eid = f"page:{t['url']}"
        if eid in seen:
            continue
        try:
            ev = tournament(t["id"])
        except Exception as e:
            log(f"[chinoize] {t['id']}: {e}")
            continue
        if not ev["standings"]:
            continue  # not finished yet
        top = ev["standings"][:4]
        rows = [(ORD.get(r["place"], f"{r['place']}th"),
                 f"**{r['player']}** — {color_emoji(r['code'], colors)} [{r['leader']}]({r['decklist']})") for r in top]
        counts = {}
        for r in ev["standings"][:8]:
            counts[r["leader"]] = counts.get(r["leader"], 0) + 1
        summary = ", ".join(f"{k} x{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        out.append(dict(id=eid, name=ev["name"], online=True, url=ev["url"], players=ev["players"], date=ev["date"],
                        rows=rows, source=src["label"], extra=f"📊 Top 8 leaders: {summary}",
                        decks=[(ORD.get(r["place"], f"{r['place']}th"), r["player"], r["leader"], r["decklist"]) for r in top]))
    return out


def page_events(game_cfg, src, seen):
    if "chinoizecupstats" in src.get("url", ""):
        return chinoize_events(game_cfg, src, seen)
    out = []
    try:
        html = http_get(src["url"])
    except Exception as e:
        log(f"[{src['id']}] {e}")
        return out
    for url, title in extract_links(html, src["url"], src["link_regex"])[: src.get("max_new", 6)]:
        eid = f"page:{url}"
        if eid in seen or not title:
            continue
        rows = []
        try:
            rows = parse_top4(http_get(url))
        except Exception as e:
            log(f"[{src['id']}] event page {url}: {e}")
        out.append(dict(id=eid, name=title, online=guess_online(title, src.get("event_type", "auto")), url=url,
                        players=None, date=None, rows=rows, source=src["label"]))
    return out


# ------------------------------------------------------------------ run ---
def post_event(game_cfg, ev, test):
    t = tag(ev["online"])
    title = f"[{'Online' if ev['online'] else 'Offline'}] {ev['name']} · Top 4"
    lines = [f"{p}: {txt}" for p, txt in ev["rows"]] or ["Top 4 couldn't be read automatically. Open the full results below."]
    meta = [x for x in [ev.get("date"), f"{ev['players']} players" if ev.get("players") else None, f"Source: {ev['source']}"] if x]
    payload = {"username": f"{game_cfg['name']} Results",
               "embeds": [embed(f"{t} | {ev['name']}", url=ev["url"], color=game_cfg.get("color", 0x5865F2),
                                description="\n".join(lines) + (f"\n\n{ev['extra']}" if ev.get('extra') else "") + f"\n\n[Full standings & decklists]({ev['url']})",
                                footer=" · ".join(meta))]}
    files = None
    if ev.get("decks") and not test:
        from deck_shot import decklist_png
        files = []
        for i, (place, player, leader, url) in enumerate(ev["decks"], 1):
            img = decklist_png(url)
            if not img:
                continue
            name = f"deck{i}.jpg"
            files.append((name, img))
            # same url as the summary embed -> Discord shows all 4 decks as one 2x2 grid in one card
            payload["embeds"].append({"url": ev["url"], "image": {"url": f"attachment://{name}"}})
    post_webhook(game_cfg["webhook"], with_ping(payload, "tournament_results"), thread_name=title, dry_run=test, files=files)


def main():
    cfg = load_config()
    test = "--test" in sys.argv
    if "--list-games" in sys.argv:
        for g in http_json(f"{LIMITLESS}/games", limitless_headers()):
            print(g.get("id"), "-", g.get("name"))
        return
    state = load_json(STATE, {"seen": []})
    seen = set(state["seen"])
    first_run = not seen
    if PREVIEW:  # one sample per live results channel: newest event (ChinoizeCup first for One Piece)
        for game in cfg["tournament_games"]:
            if not game.get("enabled", True):
                continue
            events = limitless_events(game, set(), test)
            for src in game.get("result_pages", []):
                if src.get("enabled", True):
                    events = page_events(game, src, set()) + events
            pick = next((e for e in events if "chinoize" in e.get("source", "").lower()), events[0] if events else None)
            if pick:
                post_event(game, pick, test)
            else:
                log(f"[preview] {game['name']}: no events found")
        return
    if "--seed-chinoize" in sys.argv:
        # Go-live helper: remember every result already out, then post ONLY the newest
        # finished ChinoizeCup (one message), so later runs only post genuinely new events.
        posted = False
        for game in cfg["tournament_games"]:
            if not game.get("enabled", True):
                continue
            events = limitless_events(game, seen, test)
            for src in game.get("result_pages", []):
                if src.get("enabled", True):
                    # re-read ChinoizeCup even if already logged, so the newest one can still be posted
                    events += page_events(game, src, set() if "chinoize" in src.get("url", "") else seen)
            for ev in events:
                if not posted and "chinoize" in ev.get("source", "").lower():
                    log(f"posting newest ChinoizeCup: {ev['name']} ({ev.get('date')})")
                    post_event(game, ev, test)
                    posted = True
                seen.add(ev["id"])
        if not posted:
            log("no finished ChinoizeCup found to post")
        if not test:
            state["seen"] = list(seen)[-3000:]
            save_json(STATE, state)
        return
    for game in cfg["tournament_games"]:
        if not game.get("enabled", True):
            continue
        events = limitless_events(game, seen, test)
        for src in game.get("result_pages", []):
            if src.get("enabled", True):
                events += page_events(game, src, seen)
        for ev in events:
            if first_run and not test:
                seen.add(ev["id"])  # don't flood the forum on the very first run
                continue
            post_event(game, ev, test)
            seen.add(ev["id"])
    if not test:
        state["seen"] = list(seen)[-3000:]
        save_json(STATE, state)


if __name__ == "__main__":
    main()
