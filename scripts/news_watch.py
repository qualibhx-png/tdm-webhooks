"""Watch OFFICIAL Bandai / Pokémon / Riot news pages and post new items via webhook.

Only the publisher's own sites are configured, so everything posted is officially confirmed.
Items whose title matches reveal keywords go to #set-reveals, everything else to #tcg-news.

  python scripts/news_watch.py            # normal run
  python scripts/news_watch.py --test     # print what each source finds, post nothing
  python scripts/news_watch.py --seed-latest onepiece-en
      # go-live helper: remember everything already published on every source, then post
      # ONLY the newest reveal from that one source (no role ping). One message total.
"""
import re
import sys

from common import PREVIEW, embed, og_image, extract_links, http_get, load_config, load_json, log, post_webhook, save_json, with_ping

STATE = "news_state.json"


MONTHS = "january february march april may june july august september october november december".split()
DATE_RX = re.compile(r"^\s*(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s*(\d{4})\s*", re.I)


def event_info(url, text):
    """Readable title + details for an events-page link, or None for menu/category links.
    Bandai cards read like 'OFFICIAL EVENTS Tournament <name> Event Period: <when> Regulation: <fmt> <blurb>'."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if "Event Period:" not in t:
        try:  # short/generic link text: use the page's own headline instead
            page = http_get(url)
            m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S) or re.search(r"<title>(.*?)</title>", page, re.S)
            head = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).split("｜")[0].split("|")[0].strip() if m else ""
        except Exception:
            head = ""
        if not head or len(head.split()) <= 3 and head.upper() == head:
            return None  # e.g. the "OFFICIAL SHOP" menu tab, not an event
        return {"name": head, "period": "", "reg": "", "blurb": "", "kind": ""}
    t = re.sub(r"^OFFICIAL EVENTS\s+", "", t, flags=re.I)
    kind = ""
    m = re.match(r"^(Tournament|Event|Casual|Campaign|Release Event|Side Event)\s+", t)
    if m:
        kind, t = m.group(1), t[m.end():]
    name, _, rest = t.partition("Event Period:")
    period, _, rest = rest.partition("Regulation:")
    reg, _, blurb = rest.strip().partition(" ")
    return {"name": name.strip(), "period": period.strip(), "reg": reg.strip(), "blurb": blurb.strip(), "kind": kind}


def tidy(links):
    """Strip 'November 6, 2026 PRODUCTS ' style prefixes and sort newest first when dated."""
    out = []
    for i, (u, t) in enumerate(links):
        key = ""
        m = DATE_RX.match(t)
        if m:
            key = f"{m.group(3)}-{MONTHS.index(m.group(1).lower()) + 1:02d}-{int(m.group(2)):02d}"
            t = t[m.end():]
        t = re.sub(r"^(NEWS|PRODUCTS|EVENTS|TOPICS)\s+", "", t.strip())
        out.append((key, i, u, t))
    out.sort(key=lambda x: (x[0] == "", "" if not x[0] else "".join(chr(255 - ord(ch)) for ch in x[0]), x[1]))
    return [(u, t) for _, _, u, t in out]


def classify(title, src, cfg):
    words = src.get("reveal_keywords") or cfg["defaults"]["reveal_keywords"]
    t = title.lower()
    if any(x.lower() in t for x in src.get("reveal_exclude_keywords", [])):
        return "news"  # accessories etc. never count as a set reveal
    return "reveal" if any(w.lower() in t for w in words) else "news"


def seed_latest(cfg, sid_post):
    state = load_json(STATE, {})
    posted = False
    for src in cfg["news_sources"]:
        if not src.get("enabled", True):
            continue
        sid = src["id"]
        try:
            links = extract_links(http_get(src["url"]), src["url"], src["link_regex"])
        except Exception as e:
            log(f"[{sid}] fetch error: {e}")
            continue
        inc = [k.lower() for k in src.get("include_keywords", [])]
        if inc:
            links = [(u, t) for u, t in links if any(k in t.lower() for k in inc)]
        links = tidy([(u, t) for u, t in links if t and len(t) > 6])
        if sid == sid_post and links and not posted:
            reveals = [(u, t) for u, t in links if classify(t, src, cfg) == "reveal"]
            url, title = (reveals or links)[0]  # pages list newest first
            kind = "reveal" if reveals else "news"
            hook = src["reveal_webhook"] if kind == "reveal" else src["news_webhook"]
            log(f"[{sid}] posting newest {kind}: {title}")
            post_webhook(hook, {
                "username": f"{src['game']} Official",
                "embeds": [embed(title, url=url, color=src.get("color", 0x5865F2), thumb=og_image(url),
                                 description=("🃏 **Confirmed reveal**" if kind == "reveal" else "📰 Official news"),
                                 footer=f"{src['flag']} {src['game']} · Official {src['publisher']} ({src['region']})")],
            })
            posted = True
        state[sid] = list(dict.fromkeys([u for u, _ in links] + state.get(sid, [])))[:400]
        log(f"[{sid}] remembered {len(links)} existing items")
    save_json(STATE, state)
    if not posted:
        log(f"[{sid_post}] nothing found to post")


def main():
    test = "--test" in sys.argv
    cfg = load_config()
    if "--seed-latest" in sys.argv:
        return seed_latest(cfg, sys.argv[sys.argv.index("--seed-latest") + 1])
    state = load_json(STATE, {})
    for src in cfg["news_sources"]:
        if not src.get("enabled", True):
            continue
        sid = src["id"]
        try:
            html = http_get(src["url"])
        except Exception as e:  # keep other sources running
            log(f"[{sid}] fetch error: {e}")
            continue
        links = extract_links(html, src["url"], src["link_regex"])
        inc = [k.lower() for k in src.get("include_keywords", [])]
        if inc:
            links = [(u, t) for u, t in links if any(k in t.lower() for k in inc)]
        links = tidy([(u, t) for u, t in links if t and len(t) > 6])
        if test:
            print(f"\n== {sid}: {len(links)} matching links")
            for u, t in links[:15]:
                print(f"  [{classify(t, src, cfg)}] {t}  ->  {u}")
            continue

        seen = set(state.get(sid, []))
        first_run = not seen
        new = [(u, t) for u, t in links if u not in seen]
        if PREVIEW:  # newest item + newest reveal from each source, regardless of what's been seen
            if src.get("kind") == "event":  # first real event, not a menu tab
                links = [l for l in links if "Event Period:" in l[1]] or links
            rev = next(((u, t) for u, t in links if classify(t, src, cfg) == "reveal"), None)
            new, first_run = list(dict.fromkeys([x for x in [rev] + links[:1] if x])), False
        if first_run:
            log(f"[{sid}] first run: remembering {len(new)} existing items, not posting")
        else:
            for url, title in reversed(new[:8]):  # oldest first, cap per run
                if src.get("kind") == "event":
                    # Raw event-page alerts go to the private #staff-alerts channel; staff repost real
                    # signups in #event-signups with the template (and the Event Alerts ping).
                    info = event_info(url, title)
                    if not info:
                        log(f"[{sid}] skipping menu link: {title}")
                        continue
                    details = " · ".join(x for x in [f"📅 {info['period']}" if info["period"] else "",
                                                     f"🃏 {info['reg']}" if info["reg"] else "",
                                                     info["kind"]] if x)
                    desc = "\n".join(x for x in [details, info["blurb"][:300],
                                                 f"New on the {src['game']} events page. Check it, then repost in #📅-event-signups if registration is open."] if x)
                    post_webhook(src.get("webhook", "WEBHOOK_EVENT_WATCH"), {
                        "username": "TDM Event Watch",
                        "embeds": [embed(info["name"][:250], url=url, color=0xE67E22, description=desc, thumb=og_image(url),
                                         footer=f"{src['flag']} {src['label'] if 'label' in src else src['game']}")],
                    })
                    continue
                kind = classify(title, src, cfg)
                hook = src["reveal_webhook"] if kind == "reveal" else src["news_webhook"]
                label = f"{src['flag']} {src['game']} · Official {src['publisher']} ({src['region']})"
                payload = {
                    "username": f"{src['game']} Official",
                    "embeds": [embed(title, url=url, color=src.get("color", 0x5865F2), thumb=og_image(url),
                                     description=("🃏 **Confirmed reveal**" if kind == "reveal" else "📰 Official news"),
                                     footer=label)],
                }
                post_webhook(hook, with_ping(payload, "set_reveals", cfg) if kind == "reveal" else payload)
        state[sid] = list(dict.fromkeys([u for u, _ in links] + list(seen)))[:400]
    if not test:
        save_json(STATE, state)


if __name__ == "__main__":
    main()
