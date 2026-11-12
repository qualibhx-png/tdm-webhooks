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


def with_ping(payload, role_key, cfg=None):
    """Add a role mention (from config.roles) to a webhook payload. Only that role can be pinged."""
    cfg = cfg or load_config()
    rid = str((cfg.get("roles") or {}).get(role_key) or "").strip()
    if not rid:
        return payload
    p = dict(payload)
    p["content"] = (f"<@&{rid}> " + p.get("content", "")).strip()
    p["allowed_mentions"] = {"parse": [], "roles": [rid]}
    return p


def _multipart(body, files):
    """payload_json + files[n] multipart body for Discord webhooks."""
    b = "tdmboundary7c1f"
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n'.encode()
             + json.dumps(body).encode() + b"\r\n"]
    for i, (name, blob) in enumerate(files):
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="files[{i}]"; filename="{name}"\r\nContent-Type: {"image/jpeg" if name.endswith(".jpg") else "image/png"}\r\n\r\n'.encode()
                     + blob + b"\r\n")
    return b"".join(parts) + f"--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"


def post_webhook(env_name, payload, thread_name=None, dry_run=False, files=None):
    """POST to a Discord webhook. thread_name is required for forum channels.
    files: optional [(filename, bytes)], referenced in embeds as attachment://filename."""
    url = webhook_url(env_name)
    if dry_run or not url:
        tag = "DRY-RUN" if dry_run else f"SKIP (secret {env_name} not set)"
        print(f"[{tag}] -> {env_name} thread={thread_name!r} files={[f[0] for f in files or []]}\n{json.dumps(payload, indent=1)[:1500]}")
        return
    body = dict(payload)
    body.setdefault("allowed_mentions", {"parse": []})
    if thread_name:
        body["thread_name"] = thread_name[:100]
    if files:
        data, ctype = _multipart(body, files)
    else:
        data, ctype = json.dumps(body).encode(), "application/json"
    for attempt in range(4):
        req = urllib.request.Request(url + "?wait=true", data=data, method="POST",
                                     headers={"Content-Type": ctype, "User-Agent": UA})
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


def upsert_webhook(env_name, payload, key, dry_run=False):
    """Post once, then edit that same message on later runs (keeps channels tidy).
    Message ids live in data/webhook_messages.json under `key`; a new key means a fresh post."""
    url = webhook_url(env_name)
    if dry_run or not url:
        return post_webhook(env_name, payload, dry_run=dry_run)
    ids = load_json("webhook_messages.json", {})
    body = dict(payload)
    body.setdefault("allowed_mentions", {"parse": []})
    data = json.dumps(body).encode()
    mid = ids.get(key)
    if mid:
        req = urllib.request.Request(f"{url}/messages/{mid}", data=data, method="PATCH",
                                     headers={"Content-Type": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30):
                time.sleep(1.2)
                return mid
        except urllib.error.HTTPError as e:
            if e.code not in (404, 403):
                raise RuntimeError(f"Webhook edit {env_name} failed {e.code}: {e.read()[:300]}")
    req = urllib.request.Request(url + "?wait=true", data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        mid = json.loads(r.read() or b"{}").get("id")
    time.sleep(1.2)
    if mid:
        ids[key] = mid
        save_json("webhook_messages.json", ids)
    return mid


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
