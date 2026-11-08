# Top Deck Merchant: Webhook Feeds

Everything posts through **Discord webhooks**. There's no bot user in your member list. The jobs run for free on **GitHub Actions** on a schedule. No server and no PC need to stay on.

| Job | Schedule | Posts to | Source |
|---|---|---|---|
| Official news & reveals | every 30 min | #set-reveals (reveals) · #tcg-news (product/release news) | Official sites only: Bandai (One Piece EN + JP), The Pokémon Company (US + JP), Riot (Riftbound). Naruto is ready to switch on |
| Tournament Top 4 (One Piece, Riftbound; Pokémon competitive is off) | every 3 h | each game's `#…-tournament-results` forum, one post per event, title tagged **[Online]** / **[Offline]** | Limitless TCG API + Limitless majors pages, ChinoizeCup Stats (online), RiftDecks |
| Market report | **9:00 PM PT** nightly | Per game (#pokemon-market, #op-market, #riftbound-market): singles + sealed gainers/losers · #market-movers: cross-game top movers · #sales-anomalies | Raw: TCGplayer market prices via tcgcsv.com (free). Graded PSA 10 / BGS 10 / 9.5 / CGC 10: PriceCharting API |
| Price check | every 10 min | #buy-sell reply | Same data + sold-comp links (eBay sold, PriceCharting) |

---

## 1. Put this folder on GitHub (5 min)
1. Create a free account at github.com, then **New repository** → name it `tdm-webhooks` → **Private** → Create.
2. Click **uploading an existing file** and drag in *everything* from this folder, including the hidden `.github` folder. On a Mac, press ⌘⇧. to show hidden files. Commit.
3. Go to **Settings → Actions → General** and set **Workflow permissions** to *Read and write*. Save.

## 2. Create the webhooks in Discord
For each channel: **Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL**.
Then in GitHub go to **Settings → Secrets and variables → Actions → New repository secret** and paste the URL under this name:

| Secret name | Channel | Status |
|---|---|---|
| `WEBHOOK_SET_REVEALS` | #set-reveals | ✅ webhook already created ("TDM Set Reveals"), just copy its URL |
| `WEBHOOK_MARKET_MOVERS` | #market-movers | ✅ already created ("TDM Market Movers") |
| `WEBHOOK_SALES_ANOMALIES` | #sales-anomalies | ✅ already created ("TDM Sales Anomalies") |
| `WEBHOOK_TCG_NEWS` | #tcg-news | create |
| `WEBHOOK_BUY_SELL` | #buy-sell | create |
| `WEBHOOK_RESULTS_ONEPIECE` | #op-tournament-results | create |
| `WEBHOOK_RESULTS_RIFTBOUND` | #riftbound-tournament-results | create |
| `WEBHOOK_MARKET_POKEMON` | #pokemon-market | Create channel + webhook (Integrations → Webhooks) |
| `WEBHOOK_MARKET_ONEPIECE` | #op-market | Create channel + webhook (Integrations → Webhooks) |
| `WEBHOOK_MARKET_RIFTBOUND` | #riftbound-market | Create channel + webhook (Integrations → Webhooks) |
| `WEBHOOK_RESULTS_NARUTO` | #naruto-tournament-results | create (used once Naruto launches) |

> A webhook URL works like a password: anyone who has it can post in that channel. Keep these URLs in GitHub Secrets only.

## 3. Optional keys (unlock more data)
| Secret | What it unlocks | How to get it |
|---|---|---|
| `COMPSAPI_KEY` | **Last 3 eBay sales** in #buy-sell price checks (raw or exact grade: PSA, BGS, CGC, SGC, Black Label) + a small graded watchlist in the 9pm report | Free key at compsapi.com, no card. **100 lookups/month** |
| `LIMITLESS_API_KEY` | Structured Top 4 + decklists from play.limitlesstcg.com events | Request one through the Limitless developer docs (docs.limitlesstcg.com) |
| `PRICECHARTING_TOKEN` | Graded values (PSA 10, BGS 10, Grade 9.5, CGC 10, SGC 10), graded movers, volume spikes, and graded price checks | Paid PriceCharting subscription → API token |
| `DISCORD_READ_BOT_TOKEN` + `BUYSELL_CHANNEL_ID` | The #buy-sell price checker. Webhooks can only *send*, so reading members' posts needs a read-only app token | See step 4 |

Raw-card movers and the #buy-sell raw prices work with **no keys at all**.

## 4. #buy-sell price checker (read-only token)
1. Go to discord.com/developers → **New Application** "TDM Price Check". Open the **Bot** tab → **Reset Token** → copy it into the secret `DISCORD_READ_BOT_TOKEN`.
2. On the same page, turn on **Message Content Intent**.
3. Go to **OAuth2 → URL Generator**, tick `bot`, then tick only **View Channels** + **Read Message History**. Open the URL and add it to Top Deck Merchant.
4. In Discord, open #buy-sell's channel settings and allow the app to view the channel. Turn on Developer Mode, right-click #buy-sell → **Copy Channel ID** → paste it into the secret `BUYSELL_CHANNEL_ID`.

It only reads. All replies go out through the #buy-sell webhook. Members post in this format (already pinned as a guide in #buy-sell):
```
PC: Rocks.D.Xebec Alternate Art | OP17-039 | PSA 10
```

## 5. Turn it on and test
In GitHub go to **Actions**, open each workflow and click **Run workflow** once.
* The **first run of the news and tournament jobs only memorizes** what already exists, so the channels don't get flooded. New items post from then on.
* The **market report needs two nights**: the first night saves a snapshot, and the movers start the night after.
* To test the market report before 9pm, run it locally with `python scripts/market_report.py --force --test`. With `--test` it prints and doesn't post.

## Customizing (all in `config.json`)
* **Graded watchlist:** `data/graded_watchlist.json`. Add the cards you want tracked as graded, e.g. `{"q": "Shanks OP17-020 alternate art"}`.
* **Thresholds:** in the `market` section. `min_price` filters out cheap cards. `anomaly_pct` / `anomaly_min_price` decide what counts as an anomaly.
* **Naruto:** when Bandai's official Naruto Card Game site is live, put its news URL in the `naruto` news source and set `"enabled": true`. Do the same for the Naruto tournament and market entries once results and prices exist.
* **Limitless game IDs:** Pokémon is `PTCG`. For One Piece and Riftbound, run `LIMITLESS_API_KEY=… python scripts/tournaments.py --list-games` and put the IDs in `limitless_game`.
* **Is a source returning nothing?** Run `python scripts/news_watch.py --test` and `python scripts/tournaments.py --test`. Some sites change their layout. If that happens, adjust that source's `link_regex`.

## CompsAPI free tier: how the 100 lookups/month are spent
* Each `PC:` price check costs **1 lookup**. The same card and grade within 24h is served from cache for free.
* By default the nightly graded watchlist (`data/graded_comps_watchlist.json`) can only use lookups while **more than 60** remain, so at least 60 a month are always kept for #buy-sell. Change this with `comps.reserve_for_price_checks` in `config.json`.
* When the month's lookups run out, the reply says so and still shows the eBay sold link. The count resets on the 1st.
* Upgrading later only means raising `comps.monthly_budget`; the code stays the same.

## Data notes & limits
* **Alt and Card Ladder** have no public API, and their data sits behind a member login. These jobs don't scrape them; the price-check reply tells members to search the card there. PriceCharting is the automated graded source.
* **BGS Black Label** isn't in any price API, so Black Label checks return sold-comp links only.
* **Grade 9.5** in PriceCharting is "9.5 from any grader". It's the closest available match to BGS 9.5.
* **Raw anomalies** are based on price moves only. TCGplayer doesn't publish sales counts. **Graded volume spikes** come from PriceCharting's sales volume.
* GitHub's scheduled runs can start a few minutes late, so the 9 PM report may arrive at about 9:05–9:30 PM PT.
