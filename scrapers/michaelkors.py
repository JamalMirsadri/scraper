import asyncio
import csv
import re
import random
import datetime
from urllib.parse import urlparse
from playwright.async_api import async_playwright

BASE_URL = "https://www.michaelkors.eu/pt/pt/saldos/?start=0&sz=144"
SOURCE_STORE = "Michael Kors"
SITE_HOST = "michaelkors.eu"

OUTPUT_FILE = f"michaelkors_products_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

FIELDNAMES = [
    "Title", "OriginalPrice", "OutletPrice", "SourceStore", "SourceURL",
    "Brand", "Category", "ProductImages", "Description",
    "Color", "Size", "Stock", "Status", "Gender"
]

SIZE_TOKENS = (
    ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL", "ONE SIZE", "TU"]
    + [f"EU {n}" for n in [str(x) for x in range(33, 46)]]
    + [f"EU {n}.5" for n in range(33, 46)]
    + [str(n) for n in range(28, 48)]
)

PRODUCT_LINK_RE = re.compile(r"/([A-Z0-9]{6,12})\.html", re.IGNORECASE)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def normalize_product_url(url):
    """کوئری‌استرینگ‌های ردیابی (astc, dwvar رنگ، mm_rf و غیره) و هش حذف می‌شوند
    تا لینک‌های تکراری یک محصول با رنگ‌های مختلف، یکتا شمرده نشوند در مرحلهٔ
    جمع‌آوری لینک (فقط کد محصول اصلی حفظ می‌شود)."""
    base = url.split("#")[0]
    return base.split("?")[0]


async def get_meta_content(page, prop):
    try:
        return await page.evaluate(
            """(prop) => {
                const m = document.querySelector(`meta[property="${prop}"]`);
                return m ? m.getAttribute('content') : null;
            }""",
            prop
        )
    except Exception:
        return None


async def accept_cookies(page):
    for sel in [
        "#onetrust-accept-btn-handler",
        "button:has-text('Aceitar tudo')",
        "button:has-text('Aceitar')",
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
    ]:
        try:
            await page.click(sel, timeout=4000)
            break
        except Exception:
            continue
    await page.wait_for_timeout(800)


async def click_load_more_until_done(page, max_button_clicks=10):
    """
    محصولات با اسکرول + دکمهٔ 'Carregar mais' لود می‌شوند.

    منطق:
    1) اسکرول به‌صورت خیلی آهسته و با گام‌های کوچک انجام می‌شود تا سایت
       فرصت کافی برای لیزی-لود محصولات و ظاهر کردن دکمه داشته باشد.
    2) فقط و فقط روی دکمهٔ دقیق 'Carregar mais' کلیک می‌شود.
    3) نکتهٔ مهم: در این سایت، بعد از هر بار لود محصولات جدید، دکمهٔ
       'Carregar mais' به‌طور موقت از DOM حذف و دوباره با ویژگی‌های
       data-showloadmore/data-isfirstload جدید بازسازی می‌شود (یعنی
       المان detach و attach می‌شود). به همین دلیل کلیک مستقیم گاهی با
       خطای 'element is not visible' یا 'element was detached from the
       DOM' مواجه می‌شد. حالا به‌جای اینکه با یک بار تلاش ناموفق کل
       فرایند متوقف شود، هر کلیک تا 3 بار با صبر و رفرش کردن locator
       دوباره تلاش می‌شود، و فقط اگر بعد از این تلاش‌ها هم دکمه واقعاً
       دیده/فعال نبود، لود محصولات متوقف می‌شود.
    4) حداکثر 10 بار روی دکمهٔ 'Carregar mais' کلیک می‌شود. به‌محض
       رسیدن به 10 کلیک موفق، تابع بازمی‌گردد تا اسکریپت وارد مرحلهٔ
       استخراج محصولات شود.
    """
    click_count = 0
    max_retries_per_click = 3

    while click_count < max_button_clicks:
        for _ in range(10):
            await page.mouse.wheel(0, 350)
            await page.wait_for_timeout(random.uniform(1000, 1500))

        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(random.uniform(1000, 1500))

        click_succeeded = False

        for attempt in range(1, max_retries_per_click + 1):
            button = page.locator("button:has-text('Carregar mais')").first

            try:
                is_visible = await button.is_visible(timeout=2000)
            except Exception:
                is_visible = False

            if not is_visible:
                if attempt < max_retries_per_click:
                    await page.wait_for_timeout(random.uniform(1200, 1800))
                    continue
                print("  دکمهٔ 'Carregar mais' دیگر دیده نمی‌شود؛ پایان مرحلهٔ لود محصولات.")
                break

            try:
                is_disabled = await button.is_disabled(timeout=2000)
            except Exception:
                is_disabled = False

            if is_disabled:
                if attempt < max_retries_per_click:
                    await page.wait_for_timeout(random.uniform(1200, 1800))
                    continue
                print("  دکمهٔ 'Carregar mais' غیرفعال شده است؛ پایان مرحلهٔ لود محصولات.")
                break

            try:
                await button.scroll_into_view_if_needed(timeout=3000)
                await page.wait_for_timeout(random.uniform(700, 1100))
                await button.wait_for(state="visible", timeout=5000)
                await button.click(timeout=8000)
                click_count += 1
                click_succeeded = True
                print(f"  کلیک {click_count}/{max_button_clicks} روی 'Carregar mais' انجام شد.")
                await page.wait_for_timeout(random.uniform(3000, 4000))
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
                break
            except Exception as e:
                err_str = str(e)
                is_transient = "not visible" in err_str or "detached from the DOM" in err_str
                if is_transient and attempt < max_retries_per_click:
                    print(f"  تلاش {attempt}/{max_retries_per_click} برای کلیک روی 'Carregar mais' با خطای موقت مواجه شد؛ صبر و تلاش دوباره...")
                    await page.wait_for_timeout(random.uniform(1500, 2200))
                    continue
                print(f"  کلیک روی 'Carregar mais' ناموفق بود ({e})؛ پایان مرحلهٔ لود محصولات.")
                break

        if not click_succeeded:
            break

    if click_count >= max_button_clicks:
        print(f"  به سقف {max_button_clicks} کلیک روی 'Carregar mais' رسیدیم؛ وارد مرحلهٔ استخراج محصولات می‌شویم.")

    links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    final_count = len(set(
        normalize_product_url(l) for l in links
        if SITE_HOST in l and PRODUCT_LINK_RE.search(l)
    ))
    return final_count


async def get_product_links(page, url):
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    await accept_cookies(page)
    await page.wait_for_timeout(1000)

    await click_load_more_until_done(page)

    links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    product_links = sorted(set(
        normalize_product_url(l) for l in links
        if SITE_HOST in l and PRODUCT_LINK_RE.search(l)
    ))

    if len(product_links) == 0:
        try:
            await page.screenshot(path="debug_screenshot.png", full_page=True)
            html = await page.content()
            with open("debug_page.html", "w", encoding="utf-8") as fdbg:
                fdbg.write(html)
        except Exception:
            pass

    return product_links


async def safe_goto(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    try:
        await page.wait_for_selector("h1", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)


async def extract_available_sizes_dom(page):
    """
    استخراج سایزهای موجود روی صفحهٔ محصول Michael Kors.

    منطق تشخیص ناموجود بودن یک سایز (کافی است یکی از این سیگنال‌ها صادق باشد):
    1) ویژگی‌های HTML: disabled, aria-disabled="true", data-available="false"
    2) کلاس‌های معنایی رایج در سایت‌های Demandware/SFCC (که این سایت روی آن ساخته
       شده): unselectable, unavailable, disabled, out-of-stock, sold-out, notify-me
    3) استایل محاسبه‌شده (computed style):
       - opacity به‌طور محسوس کمتر از بیشترین opacity در همان گروه سایزها
       - رنگ متن روشن‌تر/کم‌کنتراست‌تر از تیره‌ترین رنگ متن در همان گروه سایزها
         (دقیقاً همان الگویی که در سایت باعث کمرنگ دیده‌شدن سایزهای ناموجود می‌شود)
       - cursor: not-allowed
       - pointer-events: none
    4) فقط اولین المان واقعاً قابل‌مشاهده برای هر سایز ملاک تصمیم‌گیری است تا
       المان‌های تکراری/مخفی در DOM باعث نتیجهٔ غلط نشوند.
    5) اگر هیچ سیگنالی صادق نبود، سایز «موجود» در نظر گرفته می‌شود.
    """
    tokens_js = SIZE_TOKENS

    try:
        result = await page.evaluate("""
        (tokens) => {
            const isVisible = (el) => {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };

            const norm = (s) => (s || '').trim().replace(/\\s+/g, ' ').toUpperCase();
            const tokenSet = new Set(tokens.map(norm));

            const all = document.querySelectorAll('button, li, span, div, a');
            const candidatesByText = {};

            all.forEach(el => {
                const txt = norm(el.textContent);
                if (!tokenSet.has(txt)) return;
                if (el.children.length > 3) return;
                if (!isVisible(el)) return;
                if (!candidatesByText[txt]) candidatesByText[txt] = [];
                candidatesByText[txt].push(el);
            });

            const parseRgb = (colorStr) => {
                const m = (colorStr || '').match(/rgba?\\(([^)]+)\\)/);
                if (!m) return null;
                const parts = m[1].split(',').map(s => parseFloat(s.trim()));
                if (parts.length < 3) return null;
                return parts;
            };
            const luminance = (rgb) => {
                if (!rgb) return null;
                const [r, g, b] = rgb;
                return 0.2126 * r + 0.7152 * g + 0.0722 * b;
            };

            const allOpacities = [];
            const allLuminances = [];

            Object.values(candidatesByText).forEach(elList => {
                const el = elList[0];
                const style = window.getComputedStyle(el);
                const opacity = parseFloat(style.opacity);
                if (!isNaN(opacity)) allOpacities.push(opacity);
                const lum = luminance(parseRgb(style.color));
                if (lum !== null) allLuminances.push(lum);
            });

            const maxOpacity = allOpacities.length ? Math.max(...allOpacities) : 1;
            const minLuminance = allLuminances.length ? Math.min(...allLuminances) : null;

            const found = {};

            Object.entries(candidatesByText).forEach(([txt, elList]) => {
                const el = elList[0];
                const parent = el.parentElement;
                const style = window.getComputedStyle(el);
                const parentStyle = parent ? window.getComputedStyle(parent) : null;

                const opacity = parseFloat(style.opacity);
                const isFadedOpacity = !isNaN(opacity) && maxOpacity > 0 && (opacity < maxOpacity * 0.85);

                const lum = luminance(parseRgb(style.color));
                const isFadedColor = (lum !== null && minLuminance !== null)
                    ? (lum > minLuminance + 40)
                    : false;

                const classAttr = (el.className && el.className.baseVal !== undefined)
                    ? el.className.baseVal
                    : (el.className || '');
                const parentClassAttr = parent
                    ? ((parent.className && parent.className.baseVal !== undefined) ? parent.className.baseVal : (parent.className || ''))
                    : '';

                const disabledClassRe = /unselectable|unavailable|disabled|out-of-stock|outofstock|sold-out|soldout|not-available|notify/i;

                const isDisabled =
                    el.disabled === true ||
                    el.getAttribute('aria-disabled') === 'true' ||
                    el.getAttribute('disabled') !== null ||
                    el.getAttribute('data-available') === 'false' ||
                    style.textDecorationLine.includes('line-through') ||
                    (parentStyle && parentStyle.textDecorationLine.includes('line-through')) ||
                    style.cursor === 'not-allowed' ||
                    style.pointerEvents === 'none' ||
                    isFadedOpacity ||
                    isFadedColor ||
                    disabledClassRe.test(classAttr) ||
                    disabledClassRe.test(parentClassAttr);

                found[txt] = isDisabled;
            });

            return found;
        }
        """, tokens_js)
    except Exception:
        result = {}

    order = SIZE_TOKENS
    available = [size for size, disabled in result.items() if not disabled]
    available = sorted(set(available), key=lambda s: order.index(s) if s in order else 99)
    return available


def parse_prices(price_strs):
    """
    price_strs: لیست رشته‌های قیمت خام (مثل '175', '51')
    خروجی: (original_price, outlet_price) به صورت رشته.
    منطق: عدد بزرگ‌تر = OriginalPrice، عدد کوچک‌تر = OutletPrice،
    مستقل از ترتیب ظاهر شدن در متن.
    """
    if not price_strs:
        return "", ""
    try:
        numeric = [(float(p.replace(",", ".")), p) for p in price_strs]
    except ValueError:
        return price_strs[0], price_strs[-1]

    numeric.sort(key=lambda x: x[0])
    lowest = numeric[0][1]
    highest = numeric[-1][1]

    if lowest == highest:
        return lowest, lowest

    return highest, lowest


async def extract_gallery_images(page):
    """
    فقط عکس‌های واقعی گالری محصول را برمی‌گرداند؛ عکس‌های نامرتبط
    (پیشنهادها، محصولات مشابه/cross-sell، هدر/فوتر، لوگو، آیکون، بنر)
    حذف می‌شوند.

    منطق:
    1) ابتدا فقط از داخل کانتینر گالری اصلی محصول (سلکتورهای رایج
       سایت‌های SFCC/Demandware مثل .primary-images، .product-images،
       .pdp-carousel و غیره) عکس گرفته می‌شود.
    2) اگر چنین کانتینری پیدا نشد، به‌عنوان fallback از کل صفحه عکس
       گرفته می‌شود اما با فیلتر سخت‌گیرانه برای حذف بخش‌های نامرتبط
       و فقط با نگه‌داشتن عکس‌هایی که از CDN تصاویر محصول (مسیر معمول
       images/.../products یا مشابه) هستند.
    """
    gallery_selectors = [
        "[class*='primary-images']",
        "[class*='product-images']",
        "[class*='pdp-carousel']",
        "[class*='product-detail'] [class*='image']",
        "[class*='image-carousel']",
        "[class*='pdp-gallery']",
        "[class*='gallery']",
        "#product-images",
    ]

    raw_images = []
    for sel in gallery_selectors:
        try:
            count = await page.locator(sel).count()
        except Exception:
            count = 0
        if count > 0:
            try:
                raw_images = await page.eval_on_selector_all(
                    f"{sel} img, {sel} source",
                    """els => els.map(e => {
                        const srcset = e.getAttribute('srcset') || e.getAttribute('data-srcset');
                        const src = e.getAttribute('src') || e.getAttribute('data-src');
                        if (srcset) {
                            const parts = srcset.split(',').map(s => s.trim().split(' ')[0]);
                            return parts[parts.length - 1];
                        }
                        return src;
                    })"""
                )
            except Exception:
                raw_images = []
            if raw_images:
                break

    used_fallback = False
    if not raw_images:
        used_fallback = True
        try:
            raw_images = await page.eval_on_selector_all(
                "img, source",
                """els => els.map(e => {
                    const srcset = e.getAttribute('srcset') || e.getAttribute('data-srcset');
                    const src = e.getAttribute('src') || e.getAttribute('data-src');
                    if (srcset) {
                        const parts = srcset.split(',').map(s => s.trim().split(' ')[0]);
                        return parts[parts.length - 1];
                    }
                    return src;
                })"""
            )
        except Exception:
            raw_images = []

    exclude_hints = [
        "logo", "icon", "sprite", "placeholder", "avatar", "payment",
        "flag", "recommend", "related", "cross-sell", "crosssell", "upsell",
        "you-may-like", "you-might-like", "header", "footer", "nav-", "menu",
        "banner", "badge", "social", "pixel", "tracking", "newsletter",
        "chatbot", "assistant", "klarna", "bing",
    ]

    images = []
    for img in raw_images:
        if not img:
            continue
        if img.startswith("//"):
            img = "https:" + img
        if not img.startswith("http"):
            continue
        low = img.lower()
        if any(hint in low for hint in exclude_hints):
            continue
        images.append(img)

    if used_fallback:
        product_photo_re = re.compile(r"/images?/|/products?/|/dw/image/", re.IGNORECASE)
        filtered = [img for img in images if product_photo_re.search(img)]
        if filtered:
            images = filtered

    def strip_query(u):
        parsed = urlparse(u)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    seen = set()
    deduped = []
    for img in images:
        base = strip_query(img)
        if base in seen:
            continue
        seen.add(base)
        deduped.append(img)

    return deduped


async def scrape_product(page, url):
    await safe_goto(page, url)

    title = ""
    try:
        title = await page.locator("h1").first.text_content(timeout=5000)
        title = title.strip() if title else ""
    except Exception:
        pass
    if not title:
        meta_title = await get_meta_content(page, "og:title")
        title = (meta_title or "").split("|")[0].strip()

    try:
        body_text = await page.inner_text("body", timeout=10000)
    except Exception:
        body_text = ""
    body_text = re.sub(r"\s+", " ", body_text)

    raw_prices = re.findall(r"(\d{1,4}(?:[.,]\d{2})?)\s?€", body_text)
    original_price, outlet_price = parse_prices(raw_prices)

    color = ""
    color_match = re.search(r"COR\s+([A-Za-zÀ-ÿ0-9\- ]{2,30}?)(?:\s*TAMANHO|\s*Guia)", body_text, re.IGNORECASE)
    if color_match:
        color = color_match.group(1).strip()

    all_sizes = await extract_available_sizes_dom(page)

    description = ""
    desc_match = re.search(r"(?:DETALHES DO PRODUTO|Descrição|Description)(.*?)(?:ENVIO E DEVOLUÇÕES|Composição|Materials|Materiais)", body_text, re.IGNORECASE)
    if desc_match:
        description = desc_match.group(1).strip()[:500]

    images = await extract_gallery_images(page)

    brand_meta = await get_meta_content(page, "og:site_name")
    brand = brand_meta or "Michael Kors"

    stock = "In stock" if all_sizes else "Limited"
    status = "Active"

    return {
        "Title": title,
        "OriginalPrice": original_price,
        "OutletPrice": outlet_price,
        "SourceStore": SOURCE_STORE,
        "SourceURL": url,
        "Brand": brand,
        "Category": "",
        "ProductImages": "|".join(images),
        "Description": description,
        "Color": color,
        "Size": ",".join(all_sizes),
        "Stock": stock,
        "Status": status,
        "Gender": ""
    }


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="pt-PT",
            viewport={"width": 1366, "height": 900}
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
                    print(f"[{i}/{len(links)}] OK: {row['Title']} | اصلی: {row['OriginalPrice']} | تخفیف: {row['OutletPrice']} | تصاویر: {n_images} | سایز موجود: {row['Size']}")
                except Exception as e:
                    print("خطا در", link, e)

                delay = random.uniform(3.5, 6.5)
                await page.wait_for_timeout(delay * 1000)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
