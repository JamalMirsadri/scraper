import asyncio
import csv
import re
import random
import json
import os
import sys
from urllib.parse import urljoin
from playwright.async_api import async_playwright

BASE_LIST_URL = "https://www.lojazaraportugal.com/top-vestido-c-1_32.html"
LIST_PARAMS = "gender=1&sort=20a"
SITE_ROOT = "https://www.lojazaraportugal.com/"
SOURCE_STORE = "Zara"

OUTPUT_FILE = "zara_woman_products{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
PROGRESS_FILE = "zara_woman_progress{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

FIELDNAMES = [
    "Title", "OriginalPrice", "OutletPrice", "SourceStore", "SourceURL",
    "Brand", "Category", "ProductImages", "Description",
    "Color", "Size", "Stock", "Status", "Gender"
]

PRODUCT_LINK_RE = re.compile(r"-p-\d+\.html")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

OUT_OF_STOCK_HINTS = re.compile(
    r"esgotado|fora de stock|sem stock|out of stock|sold out|indispon[ií]vel",
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


def list_page_url(page_num):
    if page_num == 1:
        return f"{BASE_LIST_URL}?{LIST_PARAMS}"
    return f"{BASE_LIST_URL}?page={page_num}&{LIST_PARAMS}"


async def collect_all_product_links(page, max_pages=200):
    """
    صفحه‌به‌صفحه لیست محصولات را باز می‌کند (?page=1,2,3,...) و لینک‌های محصول
    (الگوی -p-<عدد>.html) را جمع می‌کند. وقتی صفحه‌ای هیچ لینکی نداشت یا
    دقیقاً همان لینک‌های صفحهٔ قبل را داشت (یعنی به آخر لیست رسیدیم)، متوقف می‌شود.
    """
    all_links = set()
    prev_page_links = None

    for page_num in range(1, max_pages + 1):
        url = list_page_url(page_num)
        print(f"در حال بازکردن صفحهٔ لیست {page_num}: {url}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"  خطا در بازکردن صفحه {page_num}: {e}")
            break

        await page.wait_for_timeout(2000)

        for _ in range(4):
            await page.mouse.wheel(0, 1500)
            await page.wait_for_timeout(random.uniform(500, 900))

        links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        page_links = set(l for l in links if PRODUCT_LINK_RE.search(l))

        print(f"  صفحهٔ {page_num}: {len(page_links)} لینک محصول پیدا شد")

        if len(page_links) == 0:
            print("  این صفحه هیچ محصولی نداشت، پایان صفحه‌بندی.")
            break

        if prev_page_links is not None and page_links == prev_page_links:
            print("  این صفحه دقیقاً همان محصولات صفحهٔ قبل را دارد؛ یعنی به آخر لیست رسیدیم.")
            break

        prev_page_links = page_links
        new_count = len(page_links - all_links)
        all_links |= page_links
        print(f"  لینک‌های جدید: {new_count} | مجموع تا الان: {len(all_links)}")

        await page.wait_for_timeout(random.uniform(800, 1600))

    return sorted(all_links)


async def safe_goto(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
    except Exception:
        pass
    try:
        await page.wait_for_selector("#productName", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(800)


def clean_price(txt):
    if not txt:
        return ""
    m = re.search(r"(\d+[.,]\d{2})", txt)
    return m.group(1).replace(",", ".") if m else ""


async def scrape_product(page, url):
    await safe_goto(page, url)

    try:
        title = await page.locator("#productName").first.text_content(timeout=5000)
        title = (title or "").strip()
    except Exception:
        title = ""

    original_price = ""
    outlet_price = ""
    try:
        normal_txt = await page.locator(".normalprice").first.text_content(timeout=3000)
        original_price = clean_price(normal_txt)
    except Exception:
        pass
    try:
        special_txt = await page.locator(".productSpecialPrice").first.text_content(timeout=3000)
        outlet_price = clean_price(special_txt)
    except Exception:
        pass

    if not outlet_price and original_price:
        outlet_price = original_price
    if not original_price and outlet_price:
        original_price = outlet_price
    if not original_price and not outlet_price:
        try:
            price_block_txt = await page.locator("#productPrices").first.text_content(timeout=3000)
            prices = re.findall(r"(\d+[.,]\d{2})", price_block_txt or "")
            prices = [p.replace(",", ".") for p in prices]
            if prices:
                nums = sorted(set(float(p) for p in prices))
                if len(nums) == 1:
                    original_price = outlet_price = f"{nums[0]:.2f}"
                else:
                    original_price = f"{max(nums):.2f}"
                    outlet_price = f"{min(nums):.2f}"
        except Exception:
            pass

    reference = ""
    try:
        model_txt = await page.locator("#model").first.text_content(timeout=3000)
        ref_match = re.search(r"(ZW[-\w]*)", model_txt or "", re.IGNORECASE)
        reference = ref_match.group(1) if ref_match else (model_txt or "").strip()
    except Exception:
        pass

    color = ""
    try:
        keywords = await page.evaluate(
            "() => { const m = document.querySelector(\"meta[name='keywords']\"); return m ? m.getAttribute('content') : null; }"
        )
        if keywords:
            parts = [p.strip() for p in keywords.split(",") if p.strip()]
            if parts:
                last_part = parts[-1]
                color_match = re.search(r"(?:Mulher|Homem|Feminino|Masculino)\s+([A-Za-zÀ-ÿ]+)$", last_part)
                color = color_match.group(1) if color_match else last_part.split()[-1]
    except Exception:
        pass
    if not color and title:
        color_match = re.search(r"(?:Feminino|Masculino)\s+([A-Za-zÀ-ÿ]+)", title)
        if color_match:
            color = color_match.group(1)

    category = ""
    try:
        breadcrumb_links = await page.eval_on_selector_all(
            "#u5omamehXC a", "els => els.map(e => e.textContent.trim())"
        )
        if breadcrumb_links:
            category = breadcrumb_links[-1]
    except Exception:
        pass

    all_sizes = []
    try:
        options = await page.eval_on_selector_all(
            "select[name^='id['] option",
            """els => els.map(e => ({
                text: (e.textContent || '').trim(),
                value: e.getAttribute('value'),
                disabled: e.disabled === true || e.hasAttribute('disabled')
            }))"""
        )
        for opt in options:
            txt = opt.get("text", "")
            val = opt.get("value", "")
            if not txt or not val:
                continue
            if opt.get("disabled"):
                continue
            if OUT_OF_STOCK_HINTS.search(txt):
                continue
            all_sizes.append(re.sub(r"\s*\(.*?\)\s*", "", txt).strip())
    except Exception:
        pass

    description = ""
    try:
        info_texts = await page.eval_on_selector_all(
            ".SdnW4GHMLp", "els => els.map(e => e.innerText || '')"
        )
        for block in info_texts:
            if "DESCRIPTIONS" in block.upper():
                cleaned = re.sub(r"\s+", " ", block).strip()
                m = re.search(r"DESCRIPTIONS\s*(.*?)(?:DETAILS|$)", cleaned, re.IGNORECASE)
                description = m.group(1).strip() if m else cleaned
                break
    except Exception:
        pass

    images = []
    try:
        raw_images = await page.eval_on_selector_all(
            "#home_slider img",
            "els => els.map(e => e.getAttribute('src'))"
        )
        for src in raw_images:
            if not src:
                continue
            full = urljoin(SITE_ROOT, src)
            images.append(full)
        images = list(dict.fromkeys(images))
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
        "Brand": "Zara",
        "Category": category or "Vestidos",
        "ProductImages": "|".join(images),
        "Description": description,
        "Color": color,
        "Size": ",".join(all_sizes),
        "Stock": stock,
        "Status": status,
        "Gender": "Woman"
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
            await page.wait_for_timeout(random.uniform(2000, 3500))

        await browser.close()

    print(f"\nپایان اجرا. خروجی در فایل: {OUTPUT_FILE}")


if __name__ == "__main__":
    if "--reset" in sys.argv:
        for f in (PROGRESS_FILE, OUTPUT_FILE):
            if os.path.exists(f):
                os.remove(f)
        print("ریست کامل انجام شد.\n")
    asyncio.run(main())
