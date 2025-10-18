import os, re, time, json, csv, shutil
from tqdm import tqdm
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "http://www.etmoc.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}

# 集中管理所有路径选择器与分页常量（顶部定义，避免运行期未定义）
SELECTORS = {
    "catalog_left_col": "body > div.container > div.row > div.col-8",
    "product_links_in_catalog": 'body > div.container > div.row > div.col-8 > ul a[href*="Product?Id="]',
    "product_title": "div.brand-title > h2",
    "image": "div.proImg img[src]",
    "pro_bar": "div.proBars div.proBar",
    "wait_product_ready": "div.brand-title > h2, h1, .title, .product-title",
}
PAGINATION_CANDIDATES = [
    'nav.pagination a[rel="next"]',
    "ul.pagination li.next a",
    ".pagination a.next",
    ".pager a.next",
]
NEXT_TEXT_REGEX = r"(下一页|下页|›|»)"


def select_next_page_href(soup: BeautifulSoup):
    root = soup.select_one(SELECTORS["catalog_left_col"]) or soup
    # 优先使用精确的分页候选选择器
    for sel in PAGINATION_CANDIDATES:
        a = root.select_one(sel)
        if a and a.has_attr("href"):
            return a["href"]
    # 结构化兜底：分页容器最后一个链接
    a2 = root.select_one(".pagination a[href]:last-child")
    if a2 and a2.has_attr("href"):
        href = a2["href"]
        if href and not re.search(r"javascript:", href, re.I):
            return href
    # 文本兜底：限制在左列 root 中查找“下一页/下页/›/»”
    for a in root.find_all("a", href=True):
        txt = text_clean(a.get_text(" "))
        if re.search(NEXT_TEXT_REGEX, txt):
            return a["href"]
    return None



class ProgressBar:
    def __init__(self, total: int, prefix: str = ""):
        self.total = max(int(total or 0), 1)
        self.prefix = prefix
        self.start = time.time()

    def _eta(self, current: int) -> str:
        elapsed = time.time() - self.start
        if current <= 0 or elapsed <= 0:
            return "--:--"
        rate = current / elapsed
        if rate <= 0:
            return "--:--"
        remain = (self.total - current) / rate
        if not (remain and remain < 360000):
            return "--:--"
        m = int(remain // 60)
        s = int(remain % 60)
        return f"{m:02d}:{s:02d}"

    def render(self, current: int):
        width = 28
        cur = min(current, self.total)
        completed = int(width * cur / self.total) if self.total > 0 else 0
        bar = "#" * completed + "-" * (width - completed)
        elapsed = int(time.time() - self.start)
        msg = f"{self.prefix}[{cur}/{self.total}] |{bar}| Elapsed {elapsed}s | ETA {self._eta(cur)}"
        print("\r" + msg, end="", flush=True)

    def done(self):
        print()


def text_clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def find_links(html: str, pattern: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(pattern, href, flags=re.IGNORECASE):
            links.add(href)
    return list(links)


def to_abs(page_url: str, hrefs: list) -> list:
    return [urljoin(page_url, h) for h in hrefs]


def cookies_to_requests(session: requests.Session, pw_cookies: list):
    for c in pw_cookies:
        try:
            session.cookies.set(
                c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/")
            )
        except Exception:
            session.cookies.set(c["name"], c["value"], path=c.get("path", "/"))


def hex_str(s: str) -> str:
    return "".join(f"{ord(c):x}" for c in s)


def ensure_clean_out(out_dir: str):
    if os.path.isdir(out_dir):
        for name in os.listdir(out_dir):
            path = os.path.join(out_dir, name)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except Exception as e:
                print(f"清理失败 {path}: {e}")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)


def save_json(items: list, path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def save_csv(items: list, path: str):
    keys = set()
    for it in items:
        keys.update(it.get("info", {}).keys())
    cols = ["title", "url"] + sorted(keys)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for it in items:
            row = [it.get("title", ""), it.get("url", "")] + [
                it.get("info", {}).get(k, "") for k in cols[2:]
            ]
            w.writerow(row)


def parse_images(soup: BeautifulSoup, page_url: str) -> list:
    img = soup.select_one(SELECTORS["image"])
    if not img:
        return []
    src = img.get("src")
    if not src:
        return []
    return [urljoin(page_url, src)]


def clean_time_value(v: str) -> str:
    v = text_clean(v)
    if not v:
        return v
    m = re.search(r"(20\d{2}\s*年(?:\s*\d{1,2}\s*月)?(?:\s*\d{1,2}\s*日)?)", v)
    if m:
        return text_clean(m.group(1))
    m2 = re.search(r"(20\d{2})", v)
    if m2:
        return text_clean(m2.group(1) + " 年")
    v2 = re.split(
        r"(在线评分|同品牌产品|真伪鉴别|首页|关于我们|免责声明|用户协议|站点地图|版权所有)",
        v,
    )[0]
    return text_clean(v2)


def clean_info_values(info: dict) -> dict:
    cleaned = dict(info)
    for key in ["上市时间", "发行时间"]:
        if key in cleaned and cleaned[key]:
            cleaned[key] = clean_time_value(cleaned[key])
    return cleaned


def parse_product_names(soup: BeautifulSoup, title_text: str = "") -> dict:
    h2 = soup.select_one(SELECTORS["product_title"])
    out = {}
    if h2:
        zh = text_clean(h2.get_text(" "))
        sm = h2.select_one("small")
        if sm:
            en = text_clean(sm.get_text(" "))
            zh = text_clean(zh.replace(en, ""))
            out["中文品名"] = zh
            out["英文品名"] = en
            return out
        if zh:
            out["中文品名"] = zh
    return out


# 极简统一信息提取：优先 proBars，其次表格，最后文本兜底
KEYS_WHITELIST = [
    "产品类型",
    "类型",
    "焦油量",
    "烟碱量",
    "一氧化碳量",
    "包装形式",
    "烟支规格",
    "烟支长度",
    "过滤嘴长度",
    "小盒条码",
    "条盒条码",
    "小盒零售价",
    "条盒零售价",
    "建议零售价",
    "上市时间",
    "发行时间",
    "小盒售价",
    "条盒售价",
    "单盒售价",
    "单条售价",
    "规格",
    "香型",
]


def extract_info(soup: BeautifulSoup) -> dict:
    info: dict = {}

    # 名称（精确路径）
    h2 = soup.select_one(SELECTORS["product_title"])
    title_text = (
        text_clean(h2.get_text(" "))
        if h2
        else text_clean((soup.title.string if soup.title else "") or "")
    )
    info.update(parse_product_names(soup, title_text))

    # 详情参数（仅用路径，不做全文搜索）
    root = soup.select_one(SELECTORS["catalog_left_col"]) or soup
    for bar in root.select(SELECTORS["pro_bar"]):
        children = bar.find_all("div", recursive=False)
        targets = children if children else [bar]
        for sub in targets:
            lab = sub.select_one("span")
            if not lab:
                continue
            key = text_clean(lab.get_text()).strip("：:")
            lab.extract()
            val = text_clean(sub.get_text(" "))
            if val:
                info[key] = val

    return clean_info_values(info)


# 共享工具函数：标题解析、构建 item、通用等待与图片下载

def get_title_from_soup(soup: BeautifulSoup) -> str:
    h2 = soup.select_one(SELECTORS["product_title"])
    return (
        text_clean(h2.get_text(" "))
        if h2
        else text_clean((soup.title.string if soup.title else "") or "")
    )


def build_item_from_soup(soup: BeautifulSoup, page_url: str) -> dict:
    title = get_title_from_soup(soup)
    values = extract_info(soup)
    images = parse_images(soup, page_url)
    return {"title": title, "url": page_url, "info": values, "images": images}


def wait_for_selector_safe(page, selector: str, timeout: int = 15000):
    try:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_selector(selector, timeout=timeout)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(1200)


def download_image(
    session: requests.Session, img_url: str, out_dir_images: str
) -> str | None:
    try:
        name = re.sub(
            r"[^a-zA-Z0-9._-]",
            "_",
            urlparse(img_url).path.split("/")[-1] or "image.jpg",
        )
        path = os.path.join(out_dir_images, name)
        if not os.path.exists(path):
            r = session.get(img_url, timeout=30)
            if r.status_code == 200:
                with open(path, "wb") as f:
                    f.write(r.content)
        return path if os.path.exists(path) else None
    except Exception as e:
        print("图片下载异常", e)
        return None


def download_images_for_items(items: list[dict], session: requests.Session, out_dir: str):
    out_dir_images = os.path.join(out_dir, "images")
    os.makedirs(out_dir_images, exist_ok=True)
    for it in items:
        imgs = it.get("images") or []
        if not imgs:
            continue
        local = download_image(session, imgs[0], out_dir_images)
        if local:
            it["image_local"] = local


def crawl_with_playwright(
    limit: int = None, delay: float = 0.5, out_dir: str = "etmoc_output"
):
    ensure_clean_out(out_dir)
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        page = context.new_page()

        # 预置 cookie 并触发校验 URL
        srcurl_hex = hex_str(f"{BASE}/Firms/BrandAll")
        context.add_cookies(
            [
                {
                    "name": "srcurl",
                    "value": srcurl_hex,
                    "domain": "www.etmoc.com",
                    "path": "/",
                }
            ]
        )
        sv_hex = page.evaluate(
            "(()=>{const s=`${screen.width},${screen.height}`;return Array.from(s).map(c=>c.charCodeAt(0).toString(16)).join('')})()"
        )
        page.goto(
            f"{BASE}/Firms/BrandAll?security_verify_data={sv_hex}", wait_until="load"
        )
        page.wait_for_timeout(1500)
        page.wait_for_load_state("networkidle")
        brand_html = page.content()
        with open(os.path.join(out_dir, "brand_all.html"), "w", encoding="utf-8") as f:
            f.write(brand_html)
        page.screenshot(path=os.path.join(out_dir, "brand_all.png"), full_page=True)

        brand_hrefs = find_links(brand_html, r"(?i)BrandShow\?Id=\d+")
        prod_hrefs = find_links(brand_html, r"(?i)Product\?Id=\d+")
        brand_links = list(set(to_abs(page.url, brand_hrefs + prod_hrefs)))
        if not brand_links:
            print("品牌链接为空，页面可能仍受防护，稍后重试或增大等待时间。")
            browser.close()
            return

        session = requests.Session()
        session.headers.update(HEADERS)
        cookies_to_requests(session, context.cookies())

        product_urls = []
        for b in brand_links:
            if "Product?Id=" in b:
                product_urls.append(b)
                continue
            page.goto(b, wait_until="load")
            page.wait_for_load_state("networkidle")
            bh = page.content()
            product_urls.extend(
                to_abs(page.url, find_links(bh, r"(?i)Product\?Id=\d+"))
            )
            time.sleep(delay)
            if limit and len(product_urls) >= limit:
                break

        seen = set()
        product_urls = [u for u in product_urls if not (u in seen or seen.add(u))]
        if limit:
            product_urls = product_urls[:limit]
        pb = tqdm(
            total=len(product_urls), desc="详情解析", unit="项", dynamic_ncols=True
        )

        for i, pu in enumerate(product_urls, 1):
            page.goto(pu, wait_until="load")
            page.wait_for_load_state("networkidle")
            ph = page.content()
            soup = BeautifulSoup(ph, "html.parser")
            it = build_item_from_soup(soup, pu)
            items.append(it)
            tqdm.write(f"[{i}/{len(product_urls)}] {it.get('title', '')}")
            pb.update(1)
            time.sleep(delay)

        pb.close()
        # 统一下载图片，避免解析阶段的网络阻塞
        download_images_for_items(items, session, out_dir)
        browser.close()

    save_json(items, os.path.join(out_dir, "products_playwright.json"))
    save_csv(items, os.path.join(out_dir, "products_playwright.csv"))
    print(f"完成：{len(items)} 条，输出目录：{out_dir}")


def wait_for_catalog_ready(page, timeout: int = 15000):
    wait_for_selector_safe(page, SELECTORS["product_links_in_catalog"], timeout)


def wait_for_product_ready(page, timeout: int = 15000):
    wait_for_selector_safe(page, SELECTORS["wait_product_ready"], timeout)


def collect_catalog_links(
    page, pages_limit: int = 0, delay: float = 0.7, limit: int = 0
):
    start_url = f"{BASE}/Firms/Brands"
    page.goto(start_url, wait_until="domcontentloaded")
    wait_for_catalog_ready(page)
    seen = set()
    links = []
    page_index = 1
    while True:
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.select(SELECTORS["product_links_in_catalog"])
        hrefs = [a["href"] for a in anchors if a.has_attr("href")]
        abs_links = to_abs(page.url, hrefs)
        print(f"目录页 {page_index}，产品链接 {len(abs_links)} 条")
        for u in abs_links:
            if u in seen:
                continue
            if limit and len(links) >= limit:
                break
            seen.add(u)
            links.append(u)
        # 当前页处理完成
        # 处理完后再进行上限和翻页判断
        if limit and len(links) >= limit:
            break
        if pages_limit and page_index >= pages_limit:
            break
        next_href = select_next_page_href(soup)
        if not next_href:
            break
        next_url = urljoin(page.url, next_href)
        page.goto(next_url, wait_until="domcontentloaded")
        wait_for_catalog_ready(page)
        page_index += 1
        time.sleep(delay)
    return links


def parse_product_item(
    page, session: requests.Session, url: str, out_dir: str, delay: float = 0.7
):
    try:
        page.goto(url, wait_until="domcontentloaded")
        wait_for_product_ready(page)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(800)
    prod_html = page.content()
    soup = BeautifulSoup(prod_html, "html.parser")
    item = build_item_from_soup(soup, url)
    # 统一下载图片移动到任务末尾
    time.sleep(delay)
    return item


def crawl_catalog_with_playwright(
    limit: int = 0,
    delay: float = 0.7,
    out_dir: str = "etmoc_output",
    pages_limit: int = 0,
):
    ensure_clean_out(out_dir)
    products = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        page = context.new_page()
        page.set_default_navigation_timeout(45000)
        page.set_default_timeout(45000)
        session = requests.Session()
        session.headers.update(HEADERS)
        cookies_to_requests(session, context.cookies())
        links = collect_catalog_links(
            page, pages_limit=pages_limit, delay=delay, limit=limit
        )
        print(f"目录页链接合计：{len(links)}")
        pb = tqdm(total=len(links), desc="详情解析", unit="项", dynamic_ncols=True)
        for i, link in enumerate(links, 1):
            try:
                item = parse_product_item(page, session, link, out_dir, delay)
                products.append(item)
                tqdm.write(f"[{i}/{len(links)}] 已解析：{item.get('title', '')}")
                pb.update(1)
            except Exception as e:
                print(f"详情解析失败: {link} -> {e}")
        pb.close()
        # 统一下载图片，避免解析阶段的网络阻塞
        download_images_for_items(products, session, out_dir)
        browser.close()
    save_json(products, os.path.join(out_dir, "products_catalog.json"))
    save_csv(products, os.path.join(out_dir, "products_catalog.csv"))
    print(f"完成目录抓取：{len(products)} 条，输出目录：{out_dir}")


def crawl_catalog_links(
    out_dir: str = "etmoc_output",
    pages_limit: int = 0,
    limit: int = 0,
    delay: float = 0.7,
):
    ensure_clean_out(out_dir)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        page = context.new_page()
        page.set_default_navigation_timeout(45000)
        page.set_default_timeout(45000)
        links = collect_catalog_links(
            page, pages_limit=pages_limit, delay=delay, limit=limit
        )
        browser.close()
    out = {"count": len(links), "links": links}
    save_json(out, os.path.join(out_dir, "product_links.json"))
    print(f"完成链接收集：{len(links)} 条，输出目录：{out_dir}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="使用 Playwright 爬取 ETMOC 烟草产品信息")
    ap.add_argument(
        "--limit", type=int, default=0, help="最多抓取的产品条数，0 表示不限"
    )
    ap.add_argument("--delay", type=float, default=0.5, help="请求间隔秒数")
    ap.add_argument("--out", type=str, default="etmoc_output", help="输出目录")
    ap.add_argument(
        "--pages",
        type=str,
        default=None,
        help="分页上限；传 all 表示不限；未提供默认 1 页",
    )
    ap.add_argument(
        "--source", type=str, choices=["catalog", "brands"], default="catalog"
    )
    ap.add_argument(
        "--action",
        type=str,
        choices=["list", "detail"],
        default="detail",
        help="catalog 源的动作：list 仅收集链接，detail 解析详情",
    )
    args = ap.parse_args()

    # 计算分页上限：--pages 优先；支持整数或 all；默认 1 页
    if args.pages is None:
        pages_limit = 1
    else:
        pg = args.pages.strip().lower()
        if pg == "all":
            pages_limit = 0
        else:
            try:
                pages_limit = int(pg)
            except ValueError:
                print("参数错误: --pages 需为整数或 all；使用默认 1 页")
                pages_limit = 1

    if args.source == "catalog":
        if args.action == "list":
            crawl_catalog_links(
                out_dir=args.out,
                pages_limit=pages_limit,
                limit=args.limit,
                delay=args.delay,
            )
        else:
            crawl_catalog_with_playwright(
                limit=args.limit,
                delay=args.delay,
                out_dir=args.out,
                pages_limit=pages_limit,
            )
    else:
        crawl_with_playwright(
            limit=args.limit or None, delay=args.delay, out_dir=args.out
        )

# 集中管理所有路径选择器，方便后续统一修改
