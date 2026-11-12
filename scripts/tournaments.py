"""Tournament results -> each game's #...-tournament-results forum, one post per event:
Top 4 (player, colors, leader/legend, decklist link) + a 2x2 grid of real decklist screenshots
+ the winning leader/legend card as the thumbnail. An event is posted ONLY when all four Top 4
decklists can be captured; otherwise it is retried on later runs for a few days, then dropped.

Sources (config.tournament_games[].result_pages[].kind):
  chinoize  - ChinoizeCup Stats (online, One Piece)
  limitless - onepiece.limitlesstcg.com majors (offline + big online), min_players filter
  riftdecks - riftdecks.com Riftbound results, min_players filter, rune colors

  python scripts/tournaments.py --test           # show what would be posted, post nothing
  python scripts/tournaments.py --seed-chinoize  # go-live: post newest ChinoizeCup only
"""
import re
import sys
from datetime import datetime, timedelta

from common import PREVIEW, embed, load_config, load_json, log, now_la, op_card_image, post_webhook, save_json, with_ping

STATE = "tournament_state.json"
ORD = {1: "🥇 1st", 2: "🥈 2nd", 3: "🥉 3rd", 4: "4th"}
OP_COLOR = {"red": "🔴", "green": "🟢", "blue": "🔵", "purple": "🟣", "black": "⚫", "yellow": "🟡"}
RUNE = {"fury": "🔴", "calm": "🟢", "mind": "🔵", "body": "🟠", "chaos": "🟣", "order": "🟡"}
RETRY_DAYS = 4


def tag(online):
    return "🌐 ONLINE" if online else "🏟️ OFFLINE"


def guess_online(name):
    return bool(re.search(r"\bonline\b|\bTTS\b|\bsim\b|webcam|chinoize", name, re.I))


def _date(text, fmt):
    try:
        return datetime.strptime((text or "").strip(), fmt)
    except ValueError:
        return None


def op_colors(text):
    return "".join(OP_COLOR.get(c.strip().lower(), "") for c in re.split(r"[/ ,]+", text or "")) or "🃏"


# ------------------------------------------------------------ ChinoizeCup ---
def chinoize_events(src, seen, ignore_seen=False):
    from chinoize import color_emoji, list_tournaments, tournament
    colors = load_json("op_leader_colors.json", {})
    out = []
    for t in list_tournaments(limit=src.get("max_new", 6)):
        eid = f"page:{t['url']}"
        if eid in seen and not ignore_seen:
            continue
        try:
            ev = tournament(t["id"])
        except Exception as e:
            log(f"[chinoize] {t['id']}: {e}")
            continue
        if len(ev["standings"]) < 4:
            continue  # not finished yet
        top = ev["standings"][:4]
        counts = {}
        for r in ev["standings"][:8]:
            counts[r["leader"]] = counts.get(r["leader"], 0) + 1
        summary = ", ".join(f"{k} x{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        out.append(dict(
            id=eid, name=ev["name"], online=True, url=ev["url"], players=ev["players"], date=ev["date"],
            source=src["label"], extra=f"📊 Top 8 leaders: {summary}", thumb=op_card_image(top[0]["code"]),
            rows=[(ORD[i + 1], f"**{r['player']}** · {color_emoji(r['code'], colors)} [{r['leader']}]({r['decklist']})")
                  for i, r in enumerate(top)],
            shots=[("chinoize", r["decklist"], None) for r in top], when=_date(ev["date"], "%B %d, %Y")))
    return out


# ------------------------------------------------- Limitless OP majors ---
LIM_LIST_JS = """[...document.querySelectorAll('table tr')].map(tr => {
  const a = tr.querySelector('a[href^="/tournaments/"]'); if (!a) return null;
  const td = [...tr.children].map(x => x.innerText.trim());
  return {href: a.getAttribute('href'), date: td[0], name: td[2], players: parseInt(td[4]) || 0};
}).filter(Boolean)"""

LIM_EVENT_JS = """[...document.querySelectorAll('table tr')].slice(1, 9).map(tr => {
  const td = [...tr.children];
  const list = tr.querySelector('a[href^="/decks/list/"]');
  const deck = (td[2] ? td[2].innerText : '').split('\\n').map(s => s.trim()).filter(Boolean);
  return {place: parseInt(td[0] && td[0].innerText) || 0, player: td[1] ? td[1].innerText.trim() : '',
          colors: deck[0] || '', leader: deck[1] || deck[0] || '', list: list ? list.getAttribute('href') : ''};
})"""


def limitless_events(br, src, seen, ignore_seen=False):
    base = "https://onepiece.limitlesstcg.com"
    rows = br.read(src["url"], LIM_LIST_JS) or []
    out = []
    for r in rows[: src.get("max_new", 12)]:
        eid = f"lim:{r['href']}"
        if (eid in seen and not ignore_seen) or r["players"] < src.get("min_players", 128):
            continue
        try:
            when = datetime.strptime(r["date"], "%d %b %y")
        except ValueError:
            when = None
        top = [x for x in (br.read(base + r["href"], LIM_EVENT_JS) or []) if x["place"]]
        top4 = top[:4]
        lists = [x["list"] for x in top4]
        if len(top4) < 4 or not all(lists) or len(set(lists)) < 4:
            log(f"[limitless] {r['name']}: Top 4 decklists not all published yet")
            out.append(dict(id=eid, incomplete=True, when=when, name=r["name"]))
            continue
        counts = {}
        for x in top[:8]:
            counts[x["leader"]] = counts.get(x["leader"], 0) + 1
        summary = ", ".join(f"{k} x{v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        out.append(dict(
            id=eid, name=r["name"], online=guess_online(r["name"]), url=base + r["href"], players=r["players"],
            date=when.strftime("%b %d, %Y") if when else r["date"], source=src["label"],
            extra=f"📊 Top 8 leaders: {summary}", thumb=None, when=when,
            rows=[(ORD[i + 1], f"**{x['player']}** · {op_colors(x['colors'])} [{x['leader']}]({base + x['list']})")
                  for i, x in enumerate(top4)],
            shots=[("limitless", base + x["list"], x["player"]) for x in top4],
            thumb_js=("limitless", base + top4[0]["list"])))
    return out


# --------------------------------------------------------- riftdecks.com ---
RD_LIST_JS = """[...document.querySelectorAll('table tr')].map(tr => {
  const a = [...tr.querySelectorAll('a')].find(a => /\\/riftbound-tournaments\\/.+-\\d+$/.test(a.pathname)); if (!a) return null;
  const td = [...tr.children].map(x => x.innerText.trim());
  return {href: a.pathname, date: td[0], name: (td[2] || '').replace(/\\s*New$/, ''), players: parseInt(td[3]) || 0};
}).filter(Boolean)"""

RD_EVENT_JS = """[...document.querySelectorAll('table tr')].map(tr => {
  const a = tr.querySelector('a[href*="/riftbound-metagame/deck-"]'); if (!a) return null;
  const td = [...tr.children];
  const who = (td[2] ? td[2].innerText : a.innerText).split('\\n').map(s => s.trim()).filter(Boolean);
  return {place: td[0] ? td[0].innerText.trim() : '', deck: who[0] || '', player: (who[1] || '').replace(/^by\\s+/i, ''),
          href: a.pathname, runes: [...tr.querySelectorAll('img')].map(i => (i.alt || '').toLowerCase())
            .filter(x => ['fury','calm','mind','body','chaos','order'].includes(x))};
}).filter(Boolean)"""

RD_DECK_JS = """(() => {
  const lg = document.querySelector('a[href^="/legends/constructed/"]');
  const m = document.documentElement.innerHTML.match(/\\/decks\\/visual\\/([0-9a-f-]{36})\\//);
  const img = [...document.images].map(i => i.src).find(s => s.includes('/img/cards/riftbound/'));
  return {legend: lg ? lg.textContent.trim() : '', visual: m ? m[1] : '', img: img || ''};
})()"""

PLACE_OK = re.compile(r"^(1st|2nd|3rd|4th|top\s*4|3rd\s*-\s*4th)$", re.I)


def riftdecks_events(br, src, seen, ignore_seen=False):
    base = "https://riftdecks.com"
    rows = br.read(src["url"], RD_LIST_JS) or []
    out = []
    for r in rows[: src.get("max_new", 40)]:
        eid = f"rd:{r['href']}"
        if (eid in seen and not ignore_seen) or r["players"] < src.get("min_players", 64):
            continue
        try:
            when = datetime.strptime(r["date"], "%Y-%m-%d")
        except ValueError:
            when = None
        top = (br.read(base + r["href"], RD_EVENT_JS) or [])[:4]
        if len(top) < 4 or not all(PLACE_OK.match(x["place"]) for x in top):
            log(f"[riftdecks] {r['name']}: Top 4 decklists not all published yet")
            out.append(dict(id=eid, incomplete=True, when=when, name=r["name"]))
            continue
        decks = []
        for x in top:
            d = br.read(base + x["href"], RD_DECK_JS) or {}
            if not d.get("visual"):
                break
            decks.append({**x, **d})
        if len(decks) < 4:
            log(f"[riftdecks] {r['name']}: a Top 4 visual decklist is missing")
            out.append(dict(id=eid, incomplete=True, when=when, name=r["name"]))
            continue
        out.append(dict(
            id=eid, name=r["name"], online=guess_online(r["name"]), url=base + r["href"], players=r["players"],
            date=when.strftime("%b %d, %Y") if when else r["date"], source=src["label"], extra="",
            thumb=decks[0]["img"] or None, when=when,
            rows=[(ORD[i + 1], f"**{d['player'] or d['deck']}** · {''.join(RUNE[x] for x in d['runes']) or '🌀'} "
                               f"[{d['legend'] or d['deck']}]({base + d['href']})") for i, d in enumerate(decks)],
            shots=[("riftdecks", f"{base}/decks/visual/{d['visual']}/horizontal/type", None) for d in decks]))
    return out


# ------------------------------------------------------------------ post ---
LIM_THUMB_JS = "(() => { const i = document.querySelector('img.card-picture'); return i ? i.src : ''; })()"


def post_event(br, game_cfg, ev, test):
    """Returns True when posted (or would be, in --test)."""
    images = []
    for kind, url, who in ev["shots"]:
        img = None if test else br.shot(kind, url, expect_player=who)
        if not test and not img:
            log(f"[{ev['name']}] decklist screenshot failed ({url}); holding the post")
            return False
        images.append(img)
    if not ev.get("thumb") and ev.get("thumb_js") and not test:
        ev["thumb"] = br.read(ev["thumb_js"][1], LIM_THUMB_JS) or None
    t = tag(ev["online"])
    title = f"[{'Online' if ev['online'] else 'Offline'}] {ev['name']} · Top 4"
    lines = [f"{p}: {txt}" for p, txt in ev["rows"]]
    meta = [x for x in [ev.get("date"), f"{ev['players']} players" if ev.get("players") else None, f"Source: {ev['source']}"] if x]
    payload = {"username": f"{game_cfg['name']} Results",
               "embeds": [embed(f"{t} | {ev['name']}", url=ev["url"], color=game_cfg.get("color", 0x5865F2),
                                description="\n".join(lines) + (f"\n\n{ev['extra']}" if ev.get("extra") else "")
                                + f"\n\n[Full standings & decklists]({ev['url']})",
                                footer=" · ".join(meta), thumb=ev.get("thumb"))]}
    files = []
    for i, img in enumerate(images, 1):
        if img:
            files.append((f"deck{i}.jpg", img))
            # same url as the summary embed -> Discord shows all 4 decks as one 2x2 grid in one card
            payload["embeds"].append({"url": ev["url"], "image": {"url": f"attachment://deck{i}.jpg"}})
    post_webhook(game_cfg["webhook"], with_ping(payload, "tournament_results"), thread_name=title,
                 dry_run=test, files=files or None)
    return True


def gather(br, game, src, seen, ignore_seen=False):
    kind = src.get("kind")
    if kind == "chinoize":
        return chinoize_events(src, seen, ignore_seen)
    if kind == "limitless":
        return limitless_events(br, src, seen, ignore_seen)
    if kind == "riftdecks":
        return riftdecks_events(br, src, seen, ignore_seen)
    return []


def main():
    from deck_shot import Browser
    cfg = load_config()
    test = "--test" in sys.argv
    state = load_json(STATE, {"seen": []})
    seen = set(state["seen"])
    seeded = set(state.get("seeded", []))
    if "seeded" not in state and any(x.startswith("page:https://chinoizecupstats") for x in seen):
        seeded.add("chinoizecup")  # ChinoizeCup was already live before sources were tracked
    games = [g for g in cfg["tournament_games"] if g.get("enabled", True)]
    with Browser() as br:
        if PREVIEW:  # one sample per live results channel, ignoring what's been posted
            for game in games:
                for src in game.get("result_pages", []):
                    evs = [e for e in gather(br, game, src, seen, ignore_seen=True) if not e.get("incomplete")]
                    if any(post_event(br, game, e, test) for e in evs[:3]):
                        break
                else:
                    log(f"[preview] {game['name']}: no event with all Top 4 decklists right now")
            return
        if "--seed-chinoize" in sys.argv:
            for game in games:
                for src in game.get("result_pages", []):
                    if src.get("kind") == "chinoize":
                        evs = gather(br, game, src, seen, ignore_seen=True)
                        if evs and post_event(br, game, evs[0], test):
                            log(f"posted newest ChinoizeCup: {evs[0]['name']}")
                        seen.update(e["id"] for e in evs)
            if not test:
                state["seen"] = list(seen)[-3000:]
                save_json(STATE, state)
            return
        cutoff = now_la().replace(tzinfo=None) - timedelta(days=RETRY_DAYS)
        for game in games:
            for src in game.get("result_pages", []):
                if not src.get("enabled", True):
                    continue
                evs = gather(br, game, src, seen)
                if src["id"] not in seeded:  # new source: remember what's already out, post nothing
                    seen.update(e["id"] for e in evs)
                    seeded.add(src["id"])
                    log(f"[{src['id']}] first run: remembered {len(evs)} existing events")
                    continue
                for ev in evs:
                    stale = ev.get("when") and ev["when"] < cutoff
                    if ev.get("incomplete"):
                        if stale:
                            seen.add(ev["id"])  # lists never came; stop checking
                        continue
                    if post_event(br, game, ev, test) or stale:
                        seen.add(ev["id"])
    if not test:
        state["seen"] = list(seen)[-3000:]
        state["seeded"] = sorted(seeded)
        save_json(STATE, state)


if __name__ == "__main__":
    main()
