"""#buy-sell price checks. Members post a line in this exact format (photo optional):

    PC: <card name> | <card number> | <grade>
    e.g.  PC: Rocks.D.Xebec Alternate Art | OP17-039 | PSA 10
          PC: Charizard ex | 199/165 | Raw

The job replies (through the #buy-sell webhook) with TCGplayer raw market, PriceCharting graded
value for that exact grade, and one-click sold-comp links.

Reading messages needs a read-only bot token (webhooks can only send). See README.
"""
import os
import re
import sys
import urllib.parse

from common import embed, http_json, load_config, load_json, log, post_webhook, save_json
from comps import format_last3, get_comps

API = "https://discord.com/api/v10"
LINE = re.compile(r"^\s*PC\s*:\s*(?P<name>[^|\n]+?)\s*\|\s*(?P<num>[^|\n]+?)\s*\|\s*(?P<grade>[^|\n]+?)\s*$", re.I | re.M)
GRADE_FIELD = [  # regex on grade text -> PriceCharting field
    (r"psa\s*10", "manual-only-price"), (r"bgs\s*10|beckett\s*10", "bgs-10-price"),
    (r"9\.5", "box-only-price"), (r"cgc\s*10", "condition-17-price"), (r"sgc\s*10", "condition-18-price"),
    (r"\b9\b", "graded-price"), (r"\b8(\.5)?\b", "new-price"), (r"raw|ungraded|nm|near mint", "loose-price"),
]


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def game_for(num, cfg):
    for g in cfg["market_games"]:
        if re.match(g.get("number_regex", "$^"), num.strip(), re.I):
            return g
    return None


def find_raw(name, num, game):
    index = load_json(f"index_{game['slug']}.json", {})
    snap = load_json(f"snap_{game['slug']}.json", {})
    words = norm(name).split()
    hits = []
    for pid, (pname, pnum, url, setname, *_rest) in index.items():
        if norm(pnum) != norm(num):
            continue
        score = sum(w in norm(pname) for w in words)
        prices = {k.split(":", 1)[1] or "Normal": v for k, v in snap.items() if k.startswith(pid + ":")}
        hits.append((score, pname, setname, url, prices))
    hits.sort(key=lambda h: -h[0])
    return hits[:3]


def graded_value(name, num, grade):
    token = os.environ.get("PRICECHARTING_TOKEN", "").strip()
    field = next((f for rx, f in GRADE_FIELD if re.search(rx, grade, re.I)), None)
    if not token or not field:
        return None, field
    try:
        res = http_json(f"https://www.pricecharting.com/api/products?t={token}&q={urllib.parse.quote(name + ' ' + num)}")
        if not res.get("products"):
            return None, field
        d = http_json(f"https://www.pricecharting.com/api/product?t={token}&id={res['products'][0]['id']}")
        val = d.get(field)
        return ((val / 100) if val else None, f"{d.get('product-name')} · {d.get('console-name')}"), field
    except Exception as e:
        log(f"[pricecharting] {e}")
        return None, field


def reply(cfg, msg, m):
    name, num, grade = m["name"].strip(), m["num"].strip(), m["grade"].strip()
    q = f"{name} {num} {'' if re.search('raw|ungraded', grade, re.I) else grade}".strip()
    game = game_for(num, cfg)
    fields = []
    if game:
        for score, pname, setname, url, prices in find_raw(name, num, game):
            ptxt = " · ".join(f"{k}: **${v:,.2f}**" for k, v in prices.items()) or "no market price yet"
            fields.append((f"Raw (TCGplayer market) · {pname}", f"[{setname}]({url})\n{ptxt}", False))
    if not fields:
        fields.append(("Raw (TCGplayer market)", "No exact match for that card number in the price index.", False))
    last3 = format_last3(get_comps(name, num, grade))
    if last3:
        fields.insert(0, (f"🧾 Last 3 eBay sales · {grade}", last3, False))
    gv, field = graded_value(name, num, grade)  # optional PriceCharting (paid token)
    if gv and gv[0]:
        fields.append((f"{grade} (PriceCharting)", f"**${gv[0]:,.2f}** · {gv[1]}", False))
    elif not last3 and not re.search("raw|ungraded", grade, re.I):
        fields.append((f"{grade}", "No graded sales data (COMPSAPI_KEY not set).", False))
    enc = urllib.parse.quote_plus(q)
    fields.append(("Sold comps", f"[eBay sold]({'https://www.ebay.com/sch/i.html?_nkw=' + enc + '&LH_Sold=1&LH_Complete=1'}) · "
                                 f"[PriceCharting](https://www.pricecharting.com/search-products?type=prices&q={enc}) · "
                                 f"Card Ladder / Alt: search *{q}* in their apps", False))
    post_webhook(cfg["webhooks"]["buy_sell"], {
        "username": "TDM Price Check",
        "content": f"<@{msg['author']['id']}> price check for **{name} | {num} | {grade}**",
        "embeds": [embed(f"💰 {name} · {num} · {grade}", color=0x1ABC9C, fields=fields,
                         footer="Values are market estimates, not offers. Always check recent sold comps.")]})


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
            reply(cfg, msg, m)
    save_json("pricecheck_state.json", state)


if __name__ == "__main__":
    main()
