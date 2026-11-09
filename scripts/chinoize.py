"""ChinoizeCup Stats (chinoizecupstats.com) readers. The site has no public API, so these
functions read its server-rendered pages. Everything is cached in data/chinoize_cache.json so
each tournament page and decklist is only downloaded once.

  python scripts/chinoize.py --test     # print the latest cups, the tier list and one top 4
"""
import html as _html
import re
import sys
import time

from common import http_get, load_json, log, save_json

BASE = "https://chinoizecupstats.com"
CACHE = "chinoize_cache.json"
CODE_RX = re.compile(r"\b(?:OP|ST|EB|PRB|P)\d{0,2}-\d{3}\b")
COLOR_EMOJI = {"Red": "🔴", "Green": "🟢", "Blue": "🔵", "Purple": "🟣", "Black": "⚫", "Yellow": "🟡"}


def _cache():
    return load_json(CACHE, {"tournaments": {}, "decklists": {}})


def _text(s):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def list_tournaments(limit=12):
    """Newest first: [{id, url, title}]"""
    page = http_get(f"{BASE}/tournaments")
    out, seen = [], set()
    for m in re.finditer(r'href="/tournaments/([0-9a-f]{24})"[^>]*>(.*?)</a>', page, re.S):
        tid = m.group(1)
        if tid in seen:
            continue
        seen.add(tid)
        out.append({"id": tid, "url": f"{BASE}/tournaments/{tid}", "title": _text(m.group(2))[:120]})
        if len(out) >= limit:
            break
    return out


def tournament(tid, use_cache=True):
    """{id, url, name, date, players, standings:[{place, player, country, leader, code, decklist, record}]}"""
    c = _cache()
    if use_cache and tid in c["tournaments"]:
        return c["tournaments"][tid]
    url = f"{BASE}/tournaments/{tid}"
    page = http_get(url)
    title = _text((re.search(r"<title>(.*?)</title>", page, re.S) or [None, ""])[1]).split("|")[0].strip()
    text = _text(page)
    date = (re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", text) or [None, ""])[1]
    players = int((re.search(r"Players\s+(\d+)", text) or [None, 0])[1] or 0)
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        img = re.search(r'<img[^>]*alt="([^"]+)"[^>]*src="[^"]*/([A-Z]{2,3}\d{0,2}-\d{3})_', tr)
        dl = re.search(r'href="(/decklists/[^"]+)"', tr)
        if not img:
            continue
        cells = [_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        spans = [_text(x) for x in re.findall(r"<span[^>]*>([^<]*)</span>", tr)]
        place = int(re.sub(r"\D", "", cells[0]) or 0) if cells else 0
        player = spans[0] if spans else ""
        country = spans[1] if len(spans) > 1 and re.fullmatch(r"[A-Z]{2}", spans[1] or "") else ""
        rec = re.search(r"(\d+)\s*-\s*(\d+)", " ".join(cells[2:]) if len(cells) > 2 else "")
        rows.append({"place": place or len(rows) + 1, "player": player, "country": country,
                     "leader": _html.unescape(img.group(1)), "code": img.group(2),
                     "decklist": BASE + dl.group(1) if dl else url,
                     "record": f"{rec.group(1)}-{rec.group(2)}" if rec else ""})
    rows.sort(key=lambda r: r["place"])
    data = {"id": tid, "url": url, "name": title, "date": date, "players": players, "standings": rows}
    if rows:  # only cache finished events
        c["tournaments"][tid] = data
        c["tournaments"] = dict(list(c["tournaments"].items())[-60:])
        save_json(CACHE, c)
    return data


def decklist_codes(url):
    """Card codes in a decklist page (cached)."""
    c = _cache()
    if url in c["decklists"]:
        return c["decklists"][url]
    try:
        codes = sorted(set(CODE_RX.findall(http_get(url))))
    except Exception as e:
        log(f"[chinoize] {url}: {e}")
        return []
    c["decklists"][url] = codes
    c["decklists"] = dict(list(c["decklists"].items())[-600:])
    save_json(CACHE, c)
    time.sleep(0.5)
    return codes


def tier_list():
    """[{rank, leader, code, win_rate, top4, play_rate, entries, record}] from /tier-list."""
    text = _text(http_get(f"{BASE}/tier-list"))
    fmt = (re.search(r"Rankings\s+Standard\s+(.+?)\(Current\)", text) or [None, ""])[1].strip()
    rx = re.compile(r"\b(\d{1,2}) ([A-Z][A-Za-z.'’ -]{1,40}?) \d+ pts ([A-Z]{2,3}\d{0,2}-\d{3})(?: \d+)? ([\d.]+)% Win Rate ([\d.]+)% Top 4 "
                    r"([\d.]+)% Play Rate (\d+) Entries (\d+) - (\d+) - (\d+) Record")
    out, seen = [], set()
    for m in rx.finditer(text):
        if m.group(3) in seen:
            continue
        seen.add(m.group(3))
        out.append({"rank": int(m.group(1)), "leader": m.group(2).strip(), "code": m.group(3),
                    "win_rate": float(m.group(4)), "top4": float(m.group(5)), "play_rate": float(m.group(6)),
                    "entries": int(m.group(7)), "record": f"{m.group(8)}-{m.group(9)}-{m.group(10)}"})
    return fmt, out


def color_emoji(code, colors=None):
    """Leader code -> color emoji, using data/op_leader_colors.json (built by the market job)."""
    colors = colors if colors is not None else load_json("op_leader_colors.json", {})
    col = colors.get(code, "")
    return "".join(COLOR_EMOJI.get(x.strip(), "") for x in re.split(r"[/;,]", col)) or "🃏"


def meta_cards(cups=6, top=8):
    """{card_code: number of top-cut lists it appeared in} over the last `cups` cups."""
    counts, lists = {}, 0
    for t in list_tournaments(limit=cups):
        try:
            data = tournament(t["id"])
        except Exception as e:
            log(f"[chinoize] {t['id']}: {e}")
            continue
        for row in data["standings"][:top]:
            if "/decklists/" not in row["decklist"]:
                continue
            lists += 1
            for code in decklist_codes(row["decklist"]):
                counts[code] = counts.get(code, 0) + 1
    return counts, lists


if __name__ == "__main__":
    if "--test" in sys.argv:
        ts = list_tournaments(5)
        for t in ts:
            print(t)
        if ts:
            d = tournament(ts[0]["id"], use_cache=False)
            print(d["name"], d["date"], d["players"])
            for r in d["standings"][:4]:
                print(" ", r)
        fmt, tl = tier_list()
        print("format:", fmt)
        for r in tl[:5]:
            print(" ", r)
