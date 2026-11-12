"""Screenshots of real decklist pages (never generated images) + small page reads, in one browser.

  kinds: chinoize  -> chinoizecupstats.com decklist page, sidebar/buttons hidden, leader enlarged
         limitless -> onepiece.limitlesstcg.com/decks/list/<id>, switched to its card-picture view
         riftdecks -> riftdecks.com/decks/visual/<uuid>/horizontal/type (the site's own visual decklist)

Uses Playwright (installed in the workflow). Every method returns None on failure.
"""
from common import log

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

CHINOIZE_HIDE = """
document.querySelectorAll('aside, nav, header, footer').forEach(e => e.style.display = 'none');
document.querySelectorAll('main a[href^="/tournaments"], main button').forEach(e => e.style.visibility = 'hidden');
const P = document.querySelector('main .page-enter');
if (P) { P.style.maxWidth = '1180px'; P.style.margin = '0'; }
document.querySelector('main').style.padding = '12px';
const L = document.querySelector('main img');  // leader thumbnail in the header -> full-size card
if (L) { const box = L.parentElement; box.style.width = '200px'; box.style.height = '280px';
         box.style.minWidth = '200px'; box.style.flexShrink = '0'; L.style.objectFit = 'cover'; L.style.objectPosition = 'top'; }
"""

EAGER = "document.querySelectorAll('img').forEach(i => { i.loading = 'eager'; });"


class Browser:
    def __init__(self):
        self.p = self.b = self.pg = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log("[browser] playwright missing")
            return self
        try:
            self.p = sync_playwright().start()
            self.b = self.p.chromium.launch()
            ctx = self.b.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=1.5,
                                     color_scheme="dark", user_agent=UA)
            self.pg = ctx.new_page()
        except Exception as e:
            log(f"[browser] start failed: {e}")
        return self

    def __exit__(self, *a):
        try:
            if self.b:
                self.b.close()
            if self.p:
                self.p.stop()
        except Exception:
            pass

    @property
    def ok(self):
        return self.pg is not None

    def read(self, url, js, wait="domcontentloaded"):
        """Open url and return the result of a JS expression (page data), or None."""
        if not self.ok:
            return None
        try:
            self.pg.goto(url, wait_until=wait, timeout=60000)
            self.pg.wait_for_timeout(1200)
            return self.pg.evaluate(js)
        except Exception as e:
            log(f"[browser] read {url}: {e}")
            return None

    def _settle(self):
        self.pg.evaluate(EAGER)
        self.pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        self.pg.wait_for_function("[...document.images].every(i => i.complete)", timeout=30000)
        self.pg.evaluate("window.scrollTo(0, 0)")
        self.pg.wait_for_timeout(800)

    def shot(self, kind, url, expect_player=None):
        """JPEG bytes of the decklist area, or None. expect_player: Limitless page title must name them."""
        if not self.ok:
            return None
        try:
            self.pg.goto(url, wait_until="networkidle", timeout=60000)
            if kind == "chinoize":
                self.pg.evaluate(CHINOIZE_HIDE)
                sel = "main .page-enter"
            elif kind == "limitless":
                if expect_player and expect_player.lower() not in (self.pg.title() or "").lower():
                    log(f"[shot] {url}: list isn't {expect_player}'s (title: {self.pg.title()})")
                    return None
                tab = self.pg.locator("[data-view-image]").first
                if tab.count():
                    tab.click()
                    self.pg.wait_for_timeout(1000)
                sel = ".decklist-visual"
            elif kind == "riftdecks":
                sel = ".visual-deck-background"
            else:
                return None
            self._settle()
            el = self.pg.locator(sel).first
            if not el.count():
                log(f"[shot] {url}: {sel} not found")
                return None
            return el.screenshot(type="jpeg", quality=88)
        except Exception as e:
            log(f"[shot] {url}: {e}")
            return None


def decklist_png(url):
    """Back-compat: one ChinoizeCup screenshot."""
    with Browser() as br:
        return br.shot("chinoize", url)
