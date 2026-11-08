"""Watch OFFICIAL Bandai / Pokémon / Riot news pages and post new items via webhook.

Only the publisher's own sites are configured, so everything posted is officially confirmed.
Items whose title matches reveal keywords go to #set-reveals, everything else to #tcg-news.

  python scripts/news_watch.py            # normal run
  python scripts/news_watch.py --test     # print what each source finds, post nothing
"""
import re
import sys

from common import embed, extract_links, http_get, load_config, load_json, log, post_webhook, save_json

STATE = "news_state.json"


def classify(title, src, cfg):
    words = src.get("reveal_keywords") or cfg["defaults"]["reveal_keywords"]
    return "reveal" if any(w.lower() in title.lower() for w in words) else "news"


def main():
    test = "--test" in sys.argv
    cfg = load_config()
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
        links = [(u, t) for u, t in links if t and len(t) > 6]
        if test:
            print(f"\n== {sid}: {len(links)} matching links")
            for u, t in links[:15]:
                print(f"  [{classify(t, src, cfg)}] {t}  ->  {u}")
            continue

        seen = set(state.get(sid, []))
        first_run = not seen
        new = [(u, t) for u, t in links if u not in seen]
        if first_run:
            log(f"[{sid}] first run: remembering {len(new)} existing items, not posting")
        else:
            for url, title in reversed(new[:8]):  # oldest first, cap per run
                kind = classify(title, src, cfg)
                hook = src["reveal_webhook"] if kind == "reveal" else src["news_webhook"]
                label = f"{src['flag']} {src['game']} · Official {src['publisher']} ({src['region']})"
                post_webhook(hook, {
                    "username": f"{src['game']} Official",
                    "embeds": [embed(title, url=url, color=src.get("color", 0x5865F2),
                                     description=("🃏 **Confirmed reveal**" if kind == "reveal" else "📰 Official news"),
                                     footer=label)],
                })
        state[sid] = list(dict.fromkeys([u for u, _ in links] + list(seen)))[:400]
    if not test:
        save_json(STATE, state)


if __name__ == "__main__":
    main()
