import asyncio
import csv
import re
import random
import json
import os
import sys
from urllib.parse import urljoin
from playwright.async_api import async_playwright

BASE_LIST_URL = "https://www.poloclub.com/pt-pt/collections/mulher-best-seller"
SITE_ROOT = "https://www.poloclub.com/"
SOURCE_STORE = "Polo Club"

OUTPUT_FILE = "poloclub_woman_products.csv"
PROGRESS_FILE = "poloclub_woman_progress{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

FIELDNAMES = [
    "Title", "OriginalPrice", "OutletPrice", "SourceStore", "SourceURL",
    "Brand", "Category", "ProductImages", "Description",
    "Color", "Size", "Stock", "Status", "Gender"
]

PRODUCT_LINK_RE = re.compile(r"/products/[a-z0-9\-]+")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

SIZE_TOKENS = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL"] + [str(n) for n in range(30, 50)] + ["ÚNICO", "UNICO", "TU", "ONE SIZE"]

OUT_OF_STOCK_HINTS = re.compile(
    r"esgotado|sold\s*out|indispon[ií]vel|out\s*of\s*stock|agotado",
    re.IGNORECASE
)


def csv_has_header():
    if not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0:
        return False
    with open(OUTPUT_FILE, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            return next(reader) == FIELDNAMES
        except StopIteration:
            return False


def append_rows(rows, write_header=False):
    mode = "w" if write_header else "a"
    with open(OUTPUT_FILE, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"stage": "collect_links", "product_urls": [], "last_index": 0}


def save_progress(p):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f)


async def wait_for_network_idle(page, idle_ms=800, timeout_ms=8000):
    """
    منتظر می‌ماند تا درخواست‌های شبکه (لود عکس‌ها/محصولات جدید) آرام بگیرند،
    قبل از اینکه اسکرول بعدی انجام شود. این کار به سایت فرصت کامل برای
    لود شدن محصولات جدید می‌دهد.
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass
    await page.wait_for_timeout(idle_ms)


async def collect_all_product_links(page, max_scrolls=120, stable_rounds_needed=5):
    """
    این سایت محصولات را با اسکرول (infinite scroll / lazy load) بارگذاری می‌کند.
    اسکریپت صفحه را به‌آرامی و با فاصلهٔ زمانی بیشتر به پایین اسکرول می‌کند
    (به‌جای پرش‌های بزرگ) تا به سایت زمان کافی برای لود شدن محصولات جدید بدهد.
    بعد از هر اسکرول، لینک‌های محصول جدید (الگوی /products/...) را جمع می‌کند.
    وقتی چند بار پشت‌سرهم هیچ لینک جدیدی پیدا نشد، متوقف می‌شود.
    """
    print(f"در حال بازکردن صفحهٔ لیست: {BASE_LIST_URL}")
    await page.goto(BASE_LIST_URL, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(3000)

    for sel in ["#onetrust-accept-btn-handler", "button:has-text('Aceitar')", "button:has-text('Aceptar')"]:
        try:
            await page.click(sel, timeout=3000)
            break
        except Exception:
            continue

    all_links = set()
    stable_rounds = 0

    for i in range(max_scrolls):
        links = await page.eval_on_selector_all(
            "a[href*='/products/']",
            "els => els.map(e => e.href)"
        )
        page_links = set(l for l in links if PRODUCT_LINK_RE.search(l))
        new_count = len(page_links - all_links)
        all_links |= page_links

        print(f"  اسکرول {i+1}: مجموع لینک‌ها = {len(all_links)} (جدید: {new_count})")

        if new_count == 0:
            stable_rounds += 1
            if stable_rounds >= stable_rounds_needed:
                print("  چند بار پشت‌سرهم لینک جدیدی نیامد، پایان اسکرول.")
                break
        else:
            stable_rounds = 0

        scroll_steps = 6
        step_size = 300
        for _ in range(scroll_steps):
            await page.mouse.wheel(0, step_size)
            await page.wait_for_timeout(random.uniform(350, 550))

        await wait_for_network_idle(page, idle_ms=1000, timeout_ms=8000)
        await page.wait_for_timeout(random.uniform(1800, 2800))

    return sorted(all_links)


async def safe_goto(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
    except Exception:
        pass
    try:
        await page.wait_for_selector("h1", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(1200)


def parse_all_prices(text):
    raw = re.findall(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s?€", text)
    cleaned = []
    for p in raw:
        p2 = p.replace(".", "").replace(",", ".")
        try:
            cleaned.append(float(p2))
        except ValueError:
            continue
    return sorted(set(cleaned))


async def extract_available_sizes_dom(page):
    tokens_js = SIZE_TOKENS
    try:
        result = await page.evaluate("""
        (tokens) => {
            const candidates = [];
            const all = document.querySelectorAll('button, li, span, div, a, label, input');
            all.forEach(el => {
                let txt = (el.getAttribute('data-value') || el.textContent || '').trim().toUpperCase();
                txt = txt.split(/\\s|\\n/)[0];
                if (!tokens.includes(txt)) return;
                if (el.children.length > 3) return;
                candidates.push(el);
            });

            const found = {};
            candidates.forEach(el => {
                let txt = (el.getAttribute('data-value') || el.textContent || '').trim().toUpperCase();
                txt = txt.split(/\\s|\\n/)[0];

                const style = window.getComputedStyle(el);
                const fullText = (el.textContent || '').toUpperCase();

                const isDisabled =
                    el.disabled === true ||
                    el.getAttribute('aria-disabled') === 'true' ||
                    el.getAttribute('disabled') !== null ||
                    el.getAttribute('data-disabled') === 'true' ||
                    /disabled|unavailable|soldout|sold-out|esgotado|out-of-stock|outofstock|agotado/i.test(el.className) ||
                    (el.parentElement && /disabled|unavailable|soldout|sold-out|esgotado|out-of-stock|outofstock|agotado/i.test(el.parentElement.className)) ||
                    /ESGOTADO|SOLD OUT|AGOTADO|INDISPON[IÍ]VEL/i.test(fullText) ||
                    style.pointerEvents === 'none' ||
                    style.cursor === 'not-allowed' ||
                    style.textDecorationLine.includes('line-through');

                if (!(txt in found)) {
                    found[txt] = isDisabled;
                } else {
                    found[txt] = found[txt] && isDisabled;
                }
            });
            return found;
        }
        """, tokens_js)
    except Exception:
        result = {}

    available = [size for size, disabled in result.items() if not disabled]
    order = SIZE_TOKENS
    available = sorted(set(available), key=lambda s: order.index(s) if s in order else 99)
    return available


async def extract_product_images(page):
    """
    استخراج تصاویر فقط برای محصول جاری، نه محصولات مرتبط/پیشنهادی پایین صفحه.
    اولویت با JSON-LD (schema.org Product) است چون این داده دقیقاً فقط
    تصاویر خود محصول را دارد و از آلودگی تصاویر بخش‌های دیگر صفحه مصون است.
    """
    images = []

    try:
        ld_json_blocks = await page.eval_on_selector_all(
            "script[type='application/ld+json']",
            "els => els.map(e => e.textContent)"
        )
        for block in ld_json_blocks:
            try:
                data = json.loads(block)
            except Exception:
                continue
            candidates = data if isinstance(data, list) else [data]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    is_product = "Product" in item_type
                else:
                    is_product = item_type == "Product"
                if not is_product:
                    continue
                img_field = item.get("image")
                if not img_field:
                    continue
                if isinstance(img_field, str):
                    images.append(img_field)
                elif isinstance(img_field, list):
                    for im in img_field:
                        if isinstance(im, str):
                            images.append(im)
                        elif isinstance(im, dict) and im.get("url"):
                            images.append(im["url"])
    except Exception:
        pass

    if not images:
        try:
            raw_images = await page.eval_on_selector_all(
                "[class*='product-media'] img, [class*='product-gallery'] img, "
                "[class*='media-gallery'] img, [class*='ProductMedia'] img, "
                "[class*='product__media'] img, [data-product-media] img",
                """els => els.map(e => {
                    const srcset = e.getAttribute('srcset');
                    const src = e.getAttribute('src') || e.getAttribute('data-src');
                    if (srcset) {
                        const parts = srcset.split(',').map(s => s.trim().split(' ')[0]);
                        return parts[parts.length - 1];
                    }
                    return src;
                })"""
            )
            for img in raw_images:
                if img:
                    images.append(img)
        except Exception:
            pass

    if not images:
        try:
            og_image = await page.get_attribute("meta[property='og:image']", "content")
            if og_image:
                images.append(og_image)
        except Exception:
            pass

    cleaned = []
    for img in images:
        if not img:
            continue
        if img.startswith("//"):
            img = "https:" + img
        if not img.startswith("http"):
            continue
        low = img.lower()
        if "logo" in low or "icon" in low or "payment" in low or "flag" in low:
            continue
        cleaned.append(img)
    return list(dict.fromkeys(cleaned))


async def scrape_product(page, url):
    await safe_goto(page, url)

    title = ""
    try:
        title = await page.locator("h1").first.text_content(timeout=5000)
        title = title.strip() if title else ""
    except Exception:
        pass

    try:
        body_text = await page.inner_text("body", timeout=10000)
    except Exception:
        body_text = ""
    body_text_clean = re.sub(r"\s+", " ", body_text)

    prices = parse_all_prices(body_text_clean)
    if prices:
        outlet_price = f"{prices[0]:.2f}"
        original_price = f"{prices[-1]:.2f}"
    else:
        outlet_price = ""
        original_price = ""

    color = ""
    color_match = re.search(r"(?:Cor|Color)[:\s]+([A-Za-zÀ-ÿ\- ]{2,30})", body_text_clean)
    if color_match:
        color = color_match.group(1).strip()
    if not color:
        try:
            swatch_alt = await page.eval_on_selector(
                "[class*='color'] img, [class*='swatch'] img",
                "el => el.getAttribute('alt')"
            )
            if swatch_alt:
                color = swatch_alt.strip()
        except Exception:
            pass

    all_sizes = await extract_available_sizes_dom(page)

    description = ""
    desc_match = re.search(r"(?:Descrição|Description|Detalhes)(.*?)(?:Composição|Materiais|Composition|Cuidados|Guia de tamanhos)", body_text_clean)
    if desc_match:
        description = desc_match.group(1).strip()[:500]

    images = await extract_product_images(page)

    category = ""
    try:
        breadcrumb_items = await page.eval_on_selector_all(
            "nav[aria-label*='readcrumb'] a, .breadcrumb a, [class*='breadcrumb'] a",
            "els => els.map(e => e.textContent.trim())"
        )
        if breadcrumb_items:
            category = breadcrumb_items[-1]
    except Exception:
        pass

    stock = "In stock" if all_sizes else "Limited"
    status = "Active"

    return {
        "Title": title,
        "OriginalPrice": original_price,
        "OutletPrice": outlet_price,
        "SourceStore": SOURCE_STORE,
        "SourceURL": url,
        "Brand": "Polo Club",
        "Category": category or "Homem",
        "ProductImages": "|".join(images),
        "Description": description,
        "Color": color,
        "Size": ",".join(all_sizes),
        "Stock": stock,
        "Status": status,
        "Gender": "Man"
    }


async def main():
    progress = load_progress()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="pt-PT",
            viewport={"width": 1366, "height": 900}
        )
        page = await context.new_page()

        if progress["stage"] == "collect_links":
            links = await collect_all_product_links(page)
            print(f"\nتعداد کل لینک‌های محصول یکتا: {len(links)}")
            progress["product_urls"] = links
            progress["stage"] = "detail"
            save_progress(progress)

        product_urls = progress["product_urls"]
        start_index = progress["last_index"]

        if not csv_has_header():
            append_rows([], write_header=True)
            print("فایل CSV با هدر صحیح ساخته شد.")
            if start_index > 0:
                start_index = 0
                progress["last_index"] = 0
                save_progress(progress)
        else:
            print(f"ادامه از محصول شماره {start_index + 1} از {len(product_urls)}...")

        for i in range(start_index, len(product_urls)):
            url = product_urls[i]
            try:
                row = await scrape_product(page, url)
                append_rows([row])
                n_images = len(row["ProductImages"].split("|")) if row["ProductImages"] else 0
                print(f"[{i+1}/{len(product_urls)}] OK: {row['Title']} | اصلی: {row['OriginalPrice']} | تخفیف: {row['OutletPrice']} | تصاویر: {n_images} | سایز: {row['Size']}")
            except Exception as e:
                print(f"[{i+1}/{len(product_urls)}] خطای غیرمنتظره: {type(e).__name__}: {e} -> {url}")

            progress["last_index"] = i + 1
            save_progress(progress)
            await page.wait_for_timeout(random.uniform(2500, 4000))

        await browser.close()

    print(f"\nپایان اجرا. خروجی در فایل: {OUTPUT_FILE}")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        for f in (PROGRESS_FILE, OUTPUT_FILE):
            if os.path.exists(f):
                os.remove(f)
        print("ریست کامل انجام شد.\n")
    asyncio.run(main())