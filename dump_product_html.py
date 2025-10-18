import os, time, sys
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "http://www.etmoc.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
}

OUT_DIR = os.path.join(os.path.dirname(__file__), 'etmoc_output')
# allow product id from argv
PRODUCT_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 3595
PRODUCT_URL = f"{BASE}/Firms/Product?Id={PRODUCT_ID}"


def hex_str(s: str) -> str:
    return ''.join(format(ord(c), 'x') for c in s)


def ensure_out():
    os.makedirs(OUT_DIR, exist_ok=True)


def dump_product_html(url: str, product_id: int):
    ensure_out()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS['User-Agent'])
        page = context.new_page()
        page.set_default_navigation_timeout(45000)
        page.set_default_timeout(45000)
        # set srcurl cookie and trigger verification URL
        srcurl_hex = hex_str(f"{BASE}/firms/BrandAll")
        context.add_cookies([{"name": "srcurl", "value": srcurl_hex, "domain": "www.etmoc.com", "path": "/"}])
        try:
            sv_hex = page.evaluate("(()=>{const s=`${screen.width},${screen.height}`;return Array.from(s).map(c=>c.charCodeAt(0).toString(16)).join('')})()")
        except Exception:
            sv_hex = hex_str("1280,900")
        page.goto(f"{BASE}/firms/BrandAll?security_verify_data={sv_hex}", wait_until='load')
        try:
            page.wait_for_load_state('networkidle')
        except PlaywrightTimeoutError:
            page.wait_for_timeout(1200)
        # go to product page
        page.goto(url, wait_until='domcontentloaded')
        try:
            page.wait_for_selector('div.brand-title > h2, h1, .title, .product-title', timeout=15000)
        except PlaywrightTimeoutError:
            page.wait_for_timeout(1200)
        html = page.content()
        html_path = os.path.join(OUT_DIR, f'debug_product_{product_id}.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        png_path = os.path.join(OUT_DIR, f'debug_product_{product_id}.png')
        try:
            page.screenshot(path=png_path, full_page=True)
        except Exception:
            pass
        print(f"Saved: {html_path}\nScreenshot: {png_path}")
        browser.close()


if __name__ == '__main__':
    dump_product_html(PRODUCT_URL, PRODUCT_ID)