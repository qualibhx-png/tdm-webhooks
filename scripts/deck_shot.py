"""Clean screenshot of a ChinoizeCupStats decklist page (no sidebar, no buttons) as JPEG bytes.
Uses Playwright (installed in the workflow). Returns None on failure so the post still goes out."""
from common import log

HIDE = """
document.querySelectorAll('aside, nav, header, footer').forEach(e => e.style.display = 'none');
document.querySelectorAll('main a[href^="/tournaments"], main button').forEach(e => e.style.visibility = 'hidden');
document.querySelectorAll('img').forEach(i => { i.loading = 'eager'; });
const P = document.querySelector('main .page-enter');
if (P) { P.style.maxWidth = '1180px'; P.style.margin = '0'; }
document.querySelector('main').style.padding = '12px';
const L = document.querySelector('main img');  // leader thumbnail in the header -> full-size card
if (L) { const box = L.parentElement; box.style.width = '200px'; box.style.height = '280px';
         box.style.minWidth = '200px'; box.style.flexShrink = '0'; L.style.objectFit = 'cover'; L.style.objectPosition = 'top'; }
"""


def decklist_png(url):
    """JPEG bytes of the leader + full 50-card list, or None."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("[deck_shot] playwright missing; skipping image")
        return None
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1.5, color_scheme="dark")
            pg.goto(url, wait_until="networkidle", timeout=60000)
            pg.evaluate(HIDE)
            pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            pg.wait_for_function("[...document.images].every(i => i.complete)", timeout=30000)
            pg.evaluate("window.scrollTo(0, 0)")
            pg.wait_for_timeout(800)
            el = pg.locator("main .page-enter").first
            png = el.screenshot(type="jpeg", quality=88)
            b.close()
            return png
    except Exception as e:
        log(f"[deck_shot] {url}: {e}")
        return None
