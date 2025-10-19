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

# 选择器与分页常量集中管理（统一调整、避免运行期未定义与分散维护）
SELECTORS = {
    "catalog_left_col": "body > div.container > div.row > div.col-8",
    "product_links_in_catalog": 'body > div.container > div.row > div.col-8 > ul a[href*="Product?Id="]',
    "product_title": "div.brand-title > h2",
    "image": "div.proImg img[src]",
    "pro_bar": "div.proBars div.proBar",
    "wait_product_ready": "div.brand-title > h2, h1, .title, .product-title",
    "total_pages_anchor": "body > div.container > nav > ul > li:nth-child(12) > a",
}
PAGINATION_CANDIDATES = [
    'nav.pagination a[rel="next"]',
    "ul.pagination li.next a",
    ".pagination a.next",
    ".pager a.next",
]
NEXT_TEXT_REGEX = r"(下一页|下页|›|»)"


def select_next_page_href(soup: BeautifulSoup):
    """解析目录页的“下一页”链接。
    优先匹配结构化分页选择器，其次在左列容器文本中兜底识别“下一页/下页/›/»”。
    返回相对或绝对 href；无法识别时返回 None。
    """
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


def enable_resource_blocking(context, blocked_types: set | None = None, intercepted_images: set | None = None):
    """启用路由拦截，阻止重资源请求；可记录被拦截的图片 URL。
    返回用于累积图片 URL 的集合（若未提供则返回新集合）。
    """
    if blocked_types is None:
        blocked_types = {"image", "media", "font", "stylesheet"}
    images_set = intercepted_images if intercepted_images is not None else set()

    def handle(route, request):
        rtype = request.resource_type
        if rtype in blocked_types:
            try:
                if rtype == "image":
                    images_set.add(request.url)
            except Exception:
                pass
            return route.abort()
        return route.continue_()

    context.route("**/*", handle)
    return images_set


def hex_str(s: str) -> str:
    return "".join(f"{ord(c):x}" for c in s)


def ensure_clean_out(out_dir: str, create_images_dir: bool = True):
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
    if create_images_dir:
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


def extract_image_urls(page, intercepted_images: set | None = None) -> list[str]:
    """从页面提取图片 URL：
    - <img> 的 src、data-src/data-original/data-lazy、srcset 最大密度候选
    - 行内样式与计算样式中的 background-image:url(...)
    - 与拦截到的图片请求 URL 合并去重
    返回绝对 URL 列表。
    """
    js = r"""
    () => {
      const urls = new Set();
      const toAbs = (u) => {
        if (!u) return null;
        try { return new URL(u, location.href).href; } catch { return null; }
      };
      const add = (u) => {
        const h = toAbs(u);
        if (!h) return;
        if (h.startsWith('data:')) return;
        urls.add(h.split('#')[0]);
      };
      const imgs = Array.from(document.querySelectorAll('img'));
      for (const img of imgs) {
        add(img.getAttribute('src'));
        const ds = ['data-src','data-original','data-lazy','data-url'];
        for (const k of ds) {
          const v = img.getAttribute(k);
          if (v) add(v);
        }
        const srcset = img.getAttribute('srcset') || img.getAttribute('data-srcset');
        if (srcset) {
          const candidates = srcset.split(',').map(s=>s.trim()).filter(Boolean).map(part=>{
            const m = part.split(/\s+/);
            return { url: m[0], d: m[1] || '' };
          });
          if (candidates.length) {
            const score = (c) => {
              const mm = (c.d||'').match(/(\d+(?:\.\d+)?)(x|w)/i);
              return mm ? parseFloat(mm[1]) : 0;
            };
            let best = candidates[0];
            for (const c of candidates) {
              if (score(c) > score(best)) best = c;
            }
            add(best.url);
          }
        }
      }

      const styleUrlRegex = /url\((['"]?)(.*?)\1\)/gi;
      const getBgUrls = (s) => {
        const arr = [];
        if (!s) return arr;
        let m;
        while ((m = styleUrlRegex.exec(s))) {
          const u = m[2];
          if (!u || u.startsWith('data:')) continue;
          arr.push(u);
        }
        return arr;
      };
      const all = Array.from(document.querySelectorAll('*'));
      for (const el of all) {
        const style = el.getAttribute('style') || '';
        getBgUrls(style).forEach(add);
        try {
          const cs = window.getComputedStyle(el);
          const bg = cs && cs.backgroundImage;
          if (bg && bg !== 'none') getBgUrls(bg).forEach(add);
        } catch {}
      }
      return Array.from(urls);
    }
    """
    try:
        urls: list[str] = page.evaluate(js)
    except Exception:
        urls = []
    # 合并拦截到的图片请求
    if intercepted_images:
        try:
            urls.extend(list(intercepted_images))
        except Exception:
            pass
    # 规范化、去重
    seen = set()
    out: list[str] = []
    for u in urls:
        if not u:
            continue
        try:
            abs_u = urljoin(page.url, u)
        except Exception:
            abs_u = u
        abs_u = abs_u.split('#')[0]
        if abs_u not in seen:
            seen.add(abs_u)
            out.append(abs_u)
    return out


def extract_text_content(page) -> str:
    """提取页面主体文本（col-8 容器或 body），压缩空白。"""
    root_sel = SELECTORS.get("catalog_left_col") or "body"
    try:
        txt = page.evaluate(
            f"() => ((document.querySelector('{root_sel}') || document.body).innerText || '')"
        )
    except Exception:
        try:
            txt = page.evaluate("() => document.body.innerText || ''")
        except Exception:
            txt = ""
    return text_clean(txt)


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
        imgs = it.get("image_urls") or it.get("images") or []
        if not imgs:
            continue
        local = download_image(session, imgs[0], out_dir_images)
        if local:
            it["image_local"] = local


def crawl_with_playwright(
    limit: int = None,
    delay: float = 0.5,
    out_dir: str = "etmoc_output",
    block_resources: bool = True,
    download_images: bool = False,
):
    ensure_clean_out(out_dir, create_images_dir=download_images)
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        # 路由拦截：默认阻断重资源
        intercepted_images = set()
        if block_resources:
            intercepted_images = enable_resource_blocking(context, intercepted_images=intercepted_images)
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
            f"{BASE}/Firms/BrandAll?security_verify_data={sv_hex}", wait_until="domcontentloaded"
        )
        page.wait_for_timeout(1500)
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
            page.goto(b, wait_until="domcontentloaded")
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
            before_intercepts = set(intercepted_images)
            page.goto(pu, wait_until="domcontentloaded")
            ph = page.content()
            soup = BeautifulSoup(ph, "html.parser")
            it = build_item_from_soup(soup, pu)
            # 补充文本与图片 URL（不下载图片）
            try:
                only_new = set(intercepted_images) - before_intercepts
                it["image_urls"] = extract_image_urls(page, only_new)
            except Exception:
                it["image_urls"] = []
            try:
                it["text_content"] = extract_text_content(page)
            except Exception:
                it["text_content"] = ""
            # 兼容旧字段：若 images 为空，用 image_urls 的首个填充
            if (not it.get("images")) and it.get("image_urls"):
                it["images"] = [it["image_urls"][0]]
            items.append(it)
            tqdm.write(f"[{i}/{len(product_urls)}] {it.get('title', '')}")
            pb.update(1)
            time.sleep(delay)

        pb.close()
        # 可选图片下载（默认关闭）
        if download_images:
            download_images_for_items(items, session, out_dir)
        browser.close()

    save_json(items, os.path.join(out_dir, "products_playwright.json"))
    save_csv(items, os.path.join(out_dir, "products_playwright.csv"))
    print(f"完成：{len(items)} 条，输出目录：{out_dir}")


def wait_for_catalog_ready(page, timeout: int = 15000):
    wait_for_selector_safe(page, SELECTORS["product_links_in_catalog"], timeout)


def wait_for_product_ready(page, timeout: int = 15000):
    wait_for_selector_safe(page, SELECTORS["wait_product_ready"], timeout)


def get_total_pages_number(page, root_url: str, timeout: int = 15000) -> int:
    """跳转目录首页并读取总页数。
    优先从 `SELECTORS["total_pages_anchor"]` 的文本或 href 提取页号；
    若站点结构改变或锚点不可用，则回退扫描导航中的分页链接并取最大页号。
    返回 >= 1 的整数；无法识别时返回 0（后续逻辑会视为“未知上限”）。
    """
    try:
        page.goto(root_url, wait_until="domcontentloaded")
        wait_for_selector_safe(page, SELECTORS["total_pages_anchor"], timeout)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(800)
    # 尝试直接从锚点读取
    try:
        a = page.query_selector(SELECTORS["total_pages_anchor"])  # type: ignore
        if a:
            txt = text_clean(a.inner_text())
            m = re.search(r"\d+", txt)
            if m:
                return int(m.group(0))
            href = a.get_attribute("href") or ""
            m2 = re.search(r"page=(\d+)", href)
            if m2:
                return int(m2.group(1))
    except Exception:
        pass
    # 回退：扫描导航中的所有分页链接，取最大页号
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    last_num = 0
    for a in soup.select("body > div.container nav ul li a[href]"):
        t = text_clean(a.get_text(" "))
        m1 = re.search(r"\d+", t)
        if m1:
            try:
                last_num = max(last_num, int(m1.group(0)))
            except Exception:
                pass
        href = a.get("href", "")
        m2 = re.search(r"page=(\d+)", href)
        if m2:
            try:
                last_num = max(last_num, int(m2.group(1)))
            except Exception:
                pass
    return last_num


def collect_catalog_links(
    page,
    pages_limit: int = 0,
    delay: float = 0.7,
    limit: int = 0,
    start_page: int | str | None = None,
    incremental: bool = False,
    out_dir: str | None = None,
):
    """收集目录页中的产品详情链接（去重），支持总页数限制与增量模式。
    参数：
    - pages_limit：最多遍历的页数；0 表示不限（仍受站点总页数约束）。
    - delay：每页间隔秒数。
    - limit：最多收集的链接条数；0 表示不限。
    - start_page：起始页，支持整数或 'latest'；'latest' 在有检查点时从上次完成页+1继续。
    - incremental：增量模式；默认从第 1 页开始，结合 'latest' 可继续深页。
    - out_dir：输出目录；用于保存检查点 `catalog_checkpoint.json`。
    行为：
    - 自动检测目录总页数，遍历范围为 `min(pages_limit(若>0), total_pages)`。
    - `numeric_mode` 为 True（设置了起始页或启用增量）时使用 `?page=N` 方式跳转，否则按“下一页”链接跟踪。
    - 结束时在增量模式写入 `{"last_page": N}` 检查点。
    返回：
    - 去重后的产品详情链接列表（绝对 URL）。
    """
    root_url = f"{BASE}/Firms/Brands"
    checkpoint_path = (
        os.path.join(out_dir, "catalog_checkpoint.json") if out_dir else None
    )
    numeric_mode = start_page is not None or incremental  # 数值分页模式：显式起始页或增量模式

    def goto_and_ready(url: str) -> bool:
        try:
            page.goto(url, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            print(f"页面跳转超时，结束当前抓取：{url}")
            page.wait_for_timeout(800)
            return False
        try:
            wait_for_catalog_ready(page)
        except PlaywrightTimeoutError:
            print(f"目录选择器等待超时，跳过当前页：{url}")
            page.wait_for_timeout(800)
            return False
        return True

    # 总页数检测（目录首页）
    total_pages = get_total_pages_number(page, root_url)

    # 计算起始页（增量默认从第一页开始；latest 显式从检查点继续）
    if numeric_mode:
        sp = 1
        if isinstance(start_page, str):
            st = start_page.strip().lower()
            if st == "latest" and checkpoint_path and os.path.exists(checkpoint_path):
                try:
                    with open(checkpoint_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    last_page = int(data.get("last_page", 0))
                    sp = last_page + 1 if last_page > 0 else 1
                except Exception:
                    sp = 1
            elif st.isdigit():
                try:
                    sp = int(st)
                except Exception:
                    sp = 1
        elif isinstance(start_page, int):
            sp = start_page
        # incremental 且未显式设置 start_page 时，默认从 1 开始
        page_index = max(sp, 1)
    else:
        if not goto_and_ready(root_url):
            print("目录首页无法加载，提前结束。")
            return []
        page_index = 1

    seen = set()
    links: list[str] = []
    pages_processed = 0

    while True:
        # 若已超过总页数，终止
        if total_pages and page_index > total_pages:
            break
        if numeric_mode:
            page_url = f"{root_url}?page={page_index}"
            if not goto_and_ready(page_url):
                print(f"分页跳转失败或超时，结束于第 {page_index} 页。")
                break
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.select(SELECTORS["product_links_in_catalog"])
        hrefs = [a["href"] for a in anchors if a.has_attr("href")]
        abs_links = to_abs(page.url, hrefs)
        if total_pages:
            print(f"目录页 {page_index}/{total_pages}，产品链接 {len(abs_links)} 条")
        else:
            print(f"目录页 {page_index}，产品链接 {len(abs_links)} 条")
        for u in abs_links:
            if u in seen:
                continue
            if limit and len(links) >= limit:
                break
            seen.add(u)
            links.append(u)

        # 当前页处理完成后的退出条件
        if limit and len(links) >= limit:
            break
        if pages_limit:
            if numeric_mode and (pages_processed + 1) >= pages_limit:
                pages_processed += 1
                break
            if not numeric_mode and page_index >= pages_limit:
                break

        # 下一页跳转
        if numeric_mode:
            pages_processed += 1
            page_index += 1
        else:
            next_href = select_next_page_href(soup)
            if not next_href:
                break
            next_url = urljoin(page.url, next_href)
            if not goto_and_ready(next_url):
                print(f"下一页加载失败或超时，结束于第 {page_index} 页。")
                break
            page_index += 1
        time.sleep(delay)

    # 增量检查点：记录最后完成页号（数值分页时为当前索引-1）
    if incremental and checkpoint_path:
        try:
            last_page_done = page_index - (1 if numeric_mode else 0)
            save_json({"last_page": max(last_page_done, 1)}, checkpoint_path)
        except Exception:
            pass

    return links


def parse_product_item(
    page,
    session: requests.Session,
    url: str,
    out_dir: str,
    delay: float = 0.7,
    intercepted_images: set | None = None,
):
    # 记录拦截集合快照，便于提取当前页面新增的图片请求
    before_intercepts = set(intercepted_images) if intercepted_images is not None else None
    try:
        page.goto(url, wait_until="domcontentloaded")
        wait_for_product_ready(page)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(800)
    prod_html = page.content()
    soup = BeautifulSoup(prod_html, "html.parser")
    item = build_item_from_soup(soup, url)
    # 新增：文本与图片 URL 提取
    try:
        only_new = None
        if before_intercepts is not None and intercepted_images is not None:
            only_new = set(intercepted_images) - before_intercepts
        item["image_urls"] = extract_image_urls(page, only_new)
    except Exception:
        item["image_urls"] = []
    try:
        item["text_content"] = extract_text_content(page)
    except Exception:
        item["text_content"] = ""
    # 兼容旧字段：若 images 为空，用 image_urls 的首个填充
    if (not item.get("images")) and item.get("image_urls"):
        item["images"] = [item["image_urls"][0]]
    # 统一下载图片移动到任务末尾
    time.sleep(delay)
    return item


def crawl_catalog_with_playwright(
    limit: int = 0,
    delay: float = 0.7,
    out_dir: str = "etmoc_output",
    pages_limit: int = 0,
    start_page: int | str | None = None,
    incremental: bool = False,
    block_resources: bool = True,
    download_images: bool = False,
):
    """目录源：先收集产品链接，再解析详情并下载图片。
    参数：同 `collect_catalog_links` 的分页/起始/增量语义；另含 `limit/delay/out_dir`。
    行为：
    - 链接收集后使用进度条解析详情；统一在末尾下载第一张图片以避免阻塞。
    - 非增量模式清理输出目录；增量模式仅确保目录存在。
    输出：
    - `out/products_catalog.json`、`out/products_catalog.csv`，图片保存在 `out/images/`。
    """
    if incremental:
        os.makedirs(out_dir, exist_ok=True)
        if download_images:
            os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    else:
        ensure_clean_out(out_dir, create_images_dir=download_images)
    products = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        # 路由拦截：默认阻断重资源
        intercepted_images = set()
        if block_resources:
            intercepted_images = enable_resource_blocking(context, intercepted_images=intercepted_images)
        page = context.new_page()
        page.set_default_navigation_timeout(45000)
        page.set_default_timeout(45000)
        session = requests.Session()
        session.headers.update(HEADERS)
        cookies_to_requests(session, context.cookies())
        links = collect_catalog_links(
            page,
            pages_limit=pages_limit,
            delay=delay,
            limit=limit,
            start_page=start_page,
            incremental=incremental,
            out_dir=out_dir,
        )
        print(f"目录页链接合计：{len(links)}")
        pb = tqdm(total=len(links), desc="详情解析", unit="项", dynamic_ncols=True)
        for i, link in enumerate(links, 1):
            try:
                item = parse_product_item(page, session, link, out_dir, delay, intercepted_images)
                products.append(item)
                tqdm.write(f"[{i}/{len(links)}] 已解析：{item.get('title', '')}")
                pb.update(1)
            except Exception as e:
                print(f"详情解析失败: {link} -> {e}")
        pb.close()
        # 可选图片下载（默认关闭）
        if download_images:
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
    start_page: int | str | None = None,
    incremental: bool = False,
    block_resources: bool = True,
):
    """仅收集目录页的产品链接（不解析详情），用于快速预览或链路检查。
    行为：
    - 遵循总页数上限与数值/非数值两种分页策略。
    输出：
    - 写入 `out/product_links.json`，字段：`{"count": <数量>, "links": [绝对URL...]}`。
    """
    if incremental:
        os.makedirs(out_dir, exist_ok=True)
    else:
        ensure_clean_out(out_dir, create_images_dir=False)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        if block_resources:
            enable_resource_blocking(context)
        page = context.new_page()
        page.set_default_navigation_timeout(45000)
        page.set_default_timeout(45000)
        links = collect_catalog_links(
            page,
            pages_limit=pages_limit,
            delay=delay,
            limit=limit,
            start_page=start_page,
            incremental=incremental,
            out_dir=out_dir,
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
        "--start-page",
        type=str,
        default=None,
        help="分页起始页；传整数或 latest（从检查点继续）",
    )
    ap.add_argument(
        "--incremental",
        action="store_true",
        help="启用增量模式（默认关注前几页；若需从检查点继续请配合 --start-page latest）",
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

    # 资源拦截（默认启用）
    br = ap.add_mutually_exclusive_group()
    br.add_argument("--block-resources", dest="block_resources", action="store_true", help="拦截 image/media/font/stylesheet 等重资源")
    br.add_argument("--no-block-resources", dest="block_resources", action="store_false", help="不拦截资源（对照测试）")
    ap.set_defaults(block_resources=True)

    # 图片下载（默认不下载）
    di = ap.add_mutually_exclusive_group()
    di.add_argument("--download-images", dest="download_images", action="store_true", help="下载图片到本地 images 目录")
    di.add_argument("--no-download-images", dest="download_images", action="store_false", help="不下载图片，仅输出 image_urls")
    ap.set_defaults(download_images=False)

    args = ap.parse_args()

    # 计算分页上限：`--pages` 优先；支持整数或 `all`；默认 1 页。`all` 仍受站点总页数边界约束。
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
                start_page=args.start_page,
                incremental=args.incremental,
                block_resources=args.block_resources,
            )
        else:
            crawl_catalog_with_playwright(
                limit=args.limit,
                delay=args.delay,
                out_dir=args.out,
                pages_limit=pages_limit,
                start_page=args.start_page,
                incremental=args.incremental,
                block_resources=args.block_resources,
                download_images=args.download_images,
            )
    else:
        crawl_with_playwright(
            limit=args.limit or None,
            delay=args.delay,
            out_dir=args.out,
            block_resources=args.block_resources,
            download_images=args.download_images,
        )

