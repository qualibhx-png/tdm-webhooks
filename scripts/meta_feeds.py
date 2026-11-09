"""Weekly One Piece meta feeds (Monday):
  * Weekly Leader Rankings -> #📊-op-meta            (WEBHOOK_OP_META)
  * Latest 5 top-cut lists  -> each leader channel    (config.leader_channels[].webhook)

Source: ChinoizeCup Stats (see chinoize.py). Online cups only, so the label says so.

  python scripts/meta_feeds.py --rankings --latest5          # what the workflow runs
  python scripts/meta_feeds.py --rankings --latest5 --test   # print, post nothing
"""
import re
import sys

from chinoize import color_emoji, list_tournaments, tier_list, tournament
from common import embed, load_config, load_json, log, now_la, post_webhook, save_json

STATE = "meta_state.json"
ORD = lambda n: {1: "🥇 1st", 2: "🥈 2nd", 3: "🥉 3rd"}.get(n, f"{n}th")


def rankings(cfg, test):
    fmt, rows = tier_list()
    if not rows:
        log("[rankings] tier list empty or layout changed")
        return
    state = load_json(STATE, {})
    last = {r["code"]: r["rank"] for r in state.get("last_rankings", [])}
    colors = load_json("op_leader_colors.json", {})
    top = sorted(rows, key=lambda r: r["rank"])[:10]
    lines = []
    for r in top:
        prev = last.get(r["code"])
        delta = "" if prev is None else (" (new)" if prev > 10 else (f" (▲{prev - r['rank']})" if prev > r["rank"] else (f" (▼{r['rank'] - prev})" if prev < r["rank"] else " (=)")))
        lines.append(f"{r['rank']}. {color_emoji(r['code'], colors)} **{r['leader']}** `{r['code']}` · "
                     f"{r['top4']:.1f}% top 4 · {r['win_rate']:.1f}% WR · {r['entries']} entries{delta}")
    moved = [(last[r["code"]] - r["rank"], r) for r in rows if r["code"] in last]
    rising = max(moved, key=lambda x: x[0], default=(0, None))
    falling = min(moved, key=lambda x: x[0], default=(0, None))
    extra = []
    if rising[1] and rising[0] > 0:
        extra.append(f"📈 Rising: {color_emoji(rising[1]['code'], colors)} {rising[1]['leader']} (▲{rising[0]})")
    if falling[1] and falling[0] < 0:
        extra.append(f"📉 Falling: {color_emoji(falling[1]['code'], colors)} {falling[1]['leader']} (▼{-falling[0]})")
    total = sum(r["entries"] for r in rows)
    day = now_la().strftime("%b %d, %Y")
    desc = (f"Based on {total} entries across Chinoize Cups (online) · format {fmt or 'current'}\n\n"
            + "\n".join(lines) + ("\n\n" + "\n".join(extra) if extra else "")
            + "\n\n💬 Talk it over in the leader channels.")
    post_webhook(cfg["webhooks"].get("op_meta", "WEBHOOK_OP_META"), {"username": "TDM Leader Rankings", "embeds": [embed(
        f"📊 Weekly Leader Rankings · Week of {day}", url="https://chinoizecupstats.com/tier-list",
        description=desc, color=0x9B59B6, footer="Source: ChinoizeCup Stats leader rankings")]}, dry_run=test)
    if not test:
        state["last_rankings"] = rows
        save_json(STATE, state)


def _matches(row, lc, colors):
    if lc.get("codes") and row["code"] in lc["codes"]:
        return True
    if lc.get("name") and row["leader"].lower() != lc["name"].lower():
        return False
    want = sorted(c.lower() for c in lc.get("colors", []))
    if want and row["code"] in colors:
        have = sorted(x.strip().lower() for x in re.split(r"[/;,]", colors[row["code"]]))
        return have == want
    return bool(lc.get("name")) and not lc.get("codes")


def latest5(cfg, test):
    colors = load_json("op_leader_colors.json", {})
    events = []
    for t in list_tournaments(limit=cfg.get("meta", {}).get("cups_to_scan", 15)):
        try:
            events.append(tournament(t["id"]))
        except Exception as e:
            log(f"[latest5] {t['id']}: {e}")
    top_n = cfg.get("meta", {}).get("top_cut", 8)
    day = now_la().strftime("%b %d, %Y")
    for lc in cfg.get("leader_channels", []):
        if lc.get("game", "onepiece") != "onepiece" or not lc.get("webhook"):
            continue
        hits = []
        for ev in events:  # newest first
            for row in ev["standings"][:top_n]:
                if _matches(row, lc, colors):
                    hits.append((ev, row))
        hits = hits[:5]
        title = f"📋 Latest 5 Lists · {lc['emoji']} {lc['label']} · week of {day}"
        if hits:
            lines = [f"{i}. **{row['player']}** — {ORD(row['place'])} at {ev['name']} ({ev['date']}) · [list]({row['decklist']})"
                     for i, (ev, row) in enumerate(hits, 1)]
            desc = "\n".join(lines)
        else:
            desc = f"No top {top_n} finishes in the last {len(events)} Chinoize Cups. Post yours in the channel!"
        post_webhook(lc["webhook"], {"username": "TDM Latest 5", "embeds": [embed(
            title, description=desc, color=0x3498DB, footer="Source: ChinoizeCup Stats (online cups)")]}, dry_run=test)


def main():
    cfg = load_config()
    test = "--test" in sys.argv
    if "--rankings" in sys.argv:
        rankings(cfg, test)
    if "--latest5" in sys.argv:
        latest5(cfg, test)


if __name__ == "__main__":
    main()
