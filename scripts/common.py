"""Shared helpers for the Top Deck Merchant webhook jobs (stdlib only)."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"
LA = ZoneInfo("America/Los_Angeles")
UA = "TopDeckMerchant-Webhooks/1.0 (+community discord feed)"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- state ---
def load_json(name, default):
    p = DATA / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


def save_json(name, obj):
    DATA.mkdir(parents=True, exist_ok=True)
    compact = name.startswith(("snap_", "index_"))
    text = json.dumps(obj, indent=None if compact else 1, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":") if compact else None)
    (DATA / name).write_text(text, encoding="utf-8")


# ----------------------------------------------------------------- http ---
def http_get(url, headers=None, retries=2, timeout=30):
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def http_json(url, headers=None):
    return json.loads(http_get(url, headers=headers))


# -------------------------------------------------------------- webhook ---
def webhook_url(env_name):
    url = os.environ.get(env_name or "", "").strip()
    return url or None


def post_webhook(env_name, payload, thread_name=None, dry_run=False):
    """POST to a Discord webhook. thread_name is required for forum channels."""
    url = webhook_url(env_name)
    if dry_run or not url:
        tag = "DRY-RUN" if dry_run else f"SKIP (secret {env_name} not set)"
        print(f"[{tag}] -> {env_name} thread={thread_name!r}\n{json.dumps(payload, indent=1)[:1500]}")
        return
    body = dict(payload)
    body.setdefault("allowed_mentions", {"parse": []})
    if thread_name:
        body["thread_name"] = thread_name[:100]
    data = json.dumps(body).encode()
    for attempt in range(4):
        req = urllib.request.Request(url + "?wait=true", data=data, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30):
                time.sleep(1.2)  # stay well under webhook rate limits
                return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = float(json.loads(e.read() or b"{}").get("retry_after", 5))
                time.sleep(wait + 0.5)
                continue
            raise RuntimeError(f"Webhook {env_name} failed {e.code}: {e.read()[:300]}")


def embed(title, url=None, description=None, color=0x5865F2, fields=None, footer=None):
    e = {"title": title[:256], "color": color}
    if url:
        e["url"] = url
    if description:
        e["description"] = description[:4000]
    if fields:
        e["fields"] = [{"name": n[:256], "value": (v or "—")[:1024], "inline": inl} for n, v, inl in fields][:25]
    e["footer"] = {"text": (footer or "Top Deck Merchant")[:2048]}
    e["timestamp"] = datetime.utcnow().isoformat() + "Z"
    return e


# ---------------------------------------------------------- html parse ---
class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self.links.append((self._href, text))
            self._href = None


def extract_links(html, base_url, pattern):
    """Return [(absolute_url, link_text)] whose URL matches pattern, de-duplicated, page order."""
    p = _LinkParser()
    p.feed(html)
    rx = re.compile(pattern)
    seen, out = set(), []
    for href, text in p.links:
        if not href:
            continue
        absu = urllib.parse.urljoin(base_url, href.strip()).split("#")[0]
        if not rx.search(absu) or absu in seen:
            continue
        seen.add(absu)
        out.append((absu, text))
    return out


def strip_tags(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def now_la():
    return datetime.now(LA)


def log(*a):
    print(*a, file=sys.stderr)
