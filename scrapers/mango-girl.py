import asyncio
import csv
import re
import random
from playwright.async_api import async_playwright

BASE_URL = "https://www.mangooutlet.com/pt/pt/c/teen/teena/descontos-especiais/0f4060d7"
SOURCE_STORE = "Mango Outlet"
import datetime

OUTPUT_FILE = f"mango_girl_products_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

FIELDNAMES = [
    "Title", "OriginalPrice", "OutletPrice", "SourceStore", "SourceURL",
    "Brand", "Category", "ProductImages", "Description",
    "Color", "Size", "Stock", "Status", "Gender"
]

SIZE_TOKENS = ["XXS", "XS", "S", "M", "L", "XL", "XXL"] + [str(n) for n in range(30, 47)] + ["ÚNICO"]

async def get_product_links(page, url):
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)

    prev_count = 0
    stable_rounds = 0
    max_rounds = 60

    for _ in range(max_rounds):
        await page.mouse.wheel(0, 800)
        await page.wait_for_timeout(1200)

        links = await page.eval_on_selector_all(
            "a[href*='/pt/pt/p/']",
            "els => els.map(e => e.href)"
        )
        current_count = len(set(links))

        if current_count == prev_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
        prev_count = current_count

        if stable_rounds >= 5:
            break

    links = await page.eval_on_selector_all(
        "a[href*='/pt/pt/p/']",
        "els => els.map(e => e.href)"
    )
    return list(set(links))

async def safe_goto(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
    except Exception:
        pass
    try:
        await page.wait_for_selector("h1", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)

async def extract_available_sizes_dom(page):
    """
    بررسی مستقیم عناصر DOM سایز:
    - اگر متن عنصر دقیقا برابر یکی از توکن‌های سایز باشد
    - و آن عنصر disabled باشد یا aria-disabled=true باشد
      یا استایل محاسبه‌شده‌اش text-decoration-line شامل line-through باشد
      یا کلاس/والدش شامل کلمات disabled/unavailable/soldout باشد
    آن سایز را ناموجود (خط‌خطی) در نظر می‌گیریم و از خروجی حذف می‌کنیم.
    """
    tokens_js = SIZE_TOKENS

    result = await page.evaluate("""
    (tokens) => {
        const found = {};
        const all = document.querySelectorAll('button, li, span, div, a');
        all.forEach(el => {
            const txt = (el.textContent || '').trim();
            if (!tokens.includes(txt)) return;
            if (el.children.length > 2) return; // جلوگیری از گرفتن کانتینر بزرگ

            const style = window.getComputedStyle(el);
            const parentStyle = el.parentElement ? window.getComputedStyle(el.parentElement) : null;

            const isDisabled =
                el.disabled === true ||
                el.getAttribute('aria-disabled') === 'true' ||
                el.getAttribute('disabled') !== null ||
                style.textDecorationLine.includes('line-through') ||
                (parentStyle && parentStyle.textDecorationLine.includes('line-through')) ||
                /disabled|unavailable|soldout|sold-out|not-available/i.test(el.className) ||
                (el.querySelector('svg') !== null && /disabled|unavailable|soldout|notify|bell/i.test(el.className));

            if (!(txt in found)) {
                found[txt] = isDisabled;
            } else {
                // اگر حتی یکی از عناصر تکراری این سایز فعال بود، آن را فعال در نظر بگیر
                found[txt] = found[txt] && isDisabled;
            }
        });
        return found;
    }
    """, tokens_js)

    available = [size for size, disabled in result.items() if not disabled]
    order = SIZE_TOKENS
    available = sorted(set(available), key=lambda s: order.index(s) if s in order else 99)
    return available

async def scrape_product(page, url):
    await safe_goto(page, url)

    title = ""
    try:
        title = await page.locator("h1").first.text_content()
        title = title.strip() if title else ""
    except Exception:
        pass
    if not title:
        meta_title = await page.get_attribute("meta[property='og:title']", "content")
        title = (meta_title or "").split("|")[0].strip()

    body_text = await page.inner_text("body")
    body_text = re.sub(r"\s+", " ", body_text)

    prices = re.findall(r"(\d{1,3}(?:[.,]\d{2}))\s?€", body_text)
    prices = [p.replace(",", ".") for p in prices]
    original_price = prices[0] if prices else ""
    outlet_price = prices[-1] if prices else ""

    colors = re.findall(r"Selecione uma cor\s*([A-Za-zÀ-ÿ\- ]+?)(?:XXS|XS|S |M |L |XL|XXL|\d)", body_text)
    color = colors[0].strip() if colors else ""

    all_sizes = await extract_available_sizes_dom(page)

    desc_match = re.search(r"Adicionar(.*?)Pormenores, composição", body_text)
    description = desc_match.group(1).strip() if desc_match else ""

    raw_images = await page.eval_on_selector_all(
        "img, source",
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
    images = [img for img in raw_images if img and img.startswith("http") and "logo" not in img.lower() and "icon" not in img.lower()]
    images = list(dict.fromkeys(images))

    brand_meta = await page.get_attribute("meta[property='og:site_name']", "content")
    brand = brand_meta or "Mango"

    stock = "In stock" if all_sizes else "Limited"
    status = "Active"

    return {
        "Title": title,
        "OriginalPrice": original_price,
        "OutletPrice": outlet_price,
        "SourceStore": SOURCE_STORE,
        "SourceURL": url,
        "Brand": brand,
        "Category": "Mulher",
        "ProductImages": "|".join(images),
        "Description": description,
        "Color": color,
        "Size": ",".join(all_sizes),
        "Stock": stock,
        "Status": status,
        "Gender": "Woman"
    }

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            locale="pt-PT"
        )
        page = await context.new_page()

        links = await get_product_links(page, BASE_URL)
        print(f"تعداد لینک‌های یافت‌شده: {len(links)}")

        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for i, link in enumerate(links, start=1):
                try:
                    row = await scrape_product(page, link)
                    writer.writerow(row)
                    n_images = len(row["ProductImages"].split("|")) if row["ProductImages"] else 0
                    print(f"[{i}/{len(links)}] OK: {row['Title']} | تصاویر: {n_images} | سایز موجود: {row['Size']}")
                except Exception as e:
                    print("خطا در", link, e)

                # کاهش سرعت باز کردن محصولات برای جلوگیری از بلاک شدن IP
                delay = random.uniform(3.5, 6.5)
                await page.wait_for_timeout(delay * 1000)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())