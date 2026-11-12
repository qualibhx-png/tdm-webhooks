"""#buy-sell price checks: raw (ungraded) TCGplayer market prices only, free data (tcgcsv.com).

Members post one line (photo optional), any of:
    PC: Rocks.D.Xebec | OP17-039
    PC: Charizard ex 199/165
    PC: OP17-039
Anything after a second | (like a grade) is ignored.

The job replies through the #buy-sell webhook with the matching printings' raw market prices,
the TCGplayer link, the card photo as thumbnail, and a one-click eBay sold-listings link.
Reading messages needs a read-only bot token (webhooks can only send). See README.
"""
import os
import re
import sys
import urllib.parse

from common import embed, http_json, load_config, load_json, log, post_webhook, save_json, tcgplayer_image

API = "https://discord.com/api/v10"
NUM = re.compile(r"\b([A-Z]{1,4}\d{0,2}-\d{3}|\d{1,3}\s*/\s*\d{1,3}|[A-Z]{3}\s?-?\s?\d{1,3})\b", re.I)
GRADE = re.compile(r"(PSA|BGS|CGC|SGC|TAG|ACE|BECKETT)\b|raw|ungraded|near ?mint|nm\b", re.I)
LINE = re.compile(r"^\s*PC\s*:\s*(?P<q>[^\n]+?)\s*$", re.I | re.M)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def game_for(num, cfg):
    for g in cfg["market_games"]:
        if re.match(g.get("number_regex", "$^"), num.strip(), re.I):
            return g
    return None


def parse(q):
    """'Name | number | anything' -> (name, number)."""
    parts = [x.strip() for x in q.split("|")]
    name, num = parts[0], (parts[1] if len(parts) > 1 else "")
    if num and (GRADE.match(num) or not NUM.fullmatch(num)):
        num = ""  # second part was a grade/condition, not a card number
    if not num:  # number typed inline: 'Charizard ex 199/165' or just 'OP17-039'
        m = next((x for x in NUM.finditer(name) if not GRADE.match(x.group(1))), None)
        if m:
            num, name = m.group(1), (name[:m.start()] + name[m.end():]).strip(" -")
    return name, num.strip()


def game_for(num, cfg):
    for g in cfg["market_games"]:
        if g.get("enabled", True) and re.match(g.get("number_regex", "$^"), num.strip(), re.I):
            return g
    return None


def search(name, num, cfg):
    """[(score, pid, pname, number, setname, url, {printing: price})] best first."""
    games = [game_for(num, cfg)] if num and game_for(num, cfg) else [g for g in cfg["market_games"] if g.get("enabled", True)]
    words = norm(name).split()
    hits = []
    for game in games:
        index = load_json(f"index_{game['slug']}.json", {})
        snap = load_json(f"snap_{game['slug']}.json", {})
        for pid, (pname, pnum, url, setname, *_rest) in index.items():
            if num and norm(pnum) != norm(num):
                continue
            n = norm(pname)
            score = sum(w in n for w in words)
            if not num and (not words or score < len(words)):
                continue  # name-only search: every word must match
            prices = {k.split(":", 1)[1] or "Normal": v for k, v in snap.items() if k.startswith(pid + ":")}
            hits.append((score, pid, pname, pnum, setname, url, prices))
    hits.sort(key=lambda h: (-h[0], -max(h[6].values(), default=0)))
    return hits[:4]


def reply(cfg, msg, q):
    name, num = parse(q)
    hits = search(name, num, cfg)
    label = " · ".join(x for x in [name, num] if x) or q
    fields = []
    for _, pid, pname, pnum, setname, url, prices in hits:
        ptxt = " · ".join(f"{k}: **${v:,.2f}**" for k, v in prices.items()) or "no market price yet"
        fields.append((f"{pname} · {pnum}", f"[{setname}]({url})\n{ptxt}", False))
    if not fields:
        fields.append(("No match", "Couldn't find that card. Try `PC: card name | card number`, e.g. `PC: Rocks.D.Xebec | OP17-039`.", False))
    enc = urllib.parse.quote_plus(f"{name} {num}".strip())
    fields.append(("Recent sales", f"[eBay sold listings](https://www.ebay.com/sch/i.html?_nkw={enc}&LH_Sold=1&LH_Complete=1)", False))
    post_webhook(cfg["webhooks"]["buy_sell"], {
        "username": "TDM Price Check",
        "content": f"<@{msg['author']['id']}> raw price check for **{label}**",
        "allowed_mentions": {"parse": [], "users": [msg["author"]["id"]]},
        "embeds": [embed(f"💰 {label} · Raw (ungraded)", color=0x1ABC9C, fields=fields,
                         thumb=tcgplayer_image(hits[0][1]) if hits else None,
                         footer="TCGplayer market price, near-mint raw, updated nightly · estimates, not offers.")]})


def main():
    cfg = load_config()
    token = os.environ.get("DISCORD_READ_BOT_TOKEN", "").strip()
    chan = os.environ.get("BUYSELL_CHANNEL_ID", "").strip()
    if not token or not chan:
        print("DISCORD_READ_BOT_TOKEN / BUYSELL_CHANNEL_ID not set; price check disabled.")
        return
    state = load_json("pricecheck_state.json", {})
    hdr = {"Authorization": f"Bot {token}"}
    after = state.get("last_id")
    url = f"{API}/channels/{chan}/messages?limit=50" + (f"&after={after}" if after else "")
    msgs = sorted(http_json(url, hdr), key=lambda x: int(x["id"]))
    if not after:  # first run: start from now
        state["last_id"] = msgs[-1]["id"] if msgs else "0"
        save_json("pricecheck_state.json", state)
        return
    for msg in msgs:
        state["last_id"] = msg["id"]
        if msg.get("webhook_id") or msg["author"].get("bot"):
            continue
        for m in list(LINE.finditer(msg.get("content", "")))[:3]:
            reply(cfg, msg, m.group("q"))
    save_json("pricecheck_state.json", state)


if __name__ == "__main__":
    main()
