
import time
import random
import csv
import json
import os
import datetime
from curl_cffi import requests as cffi_requests

BASE_URL = "https://www2.hm.com/pt_pt/senhora/last-chance/ver-tudo.html"
SOURCE_STORE = "H&M"
OUTPUT_FILE = "h&m_woman_products.csv"
PROGRESS_FILE = "h&m_woman_progress{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

HEADERS = {
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Referer": BASE_URL,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

FIELDNAMES = [
    "Title", "OriginalPrice", "OutletPrice", "SourceStore", "SourceURL",
    "Brand", "Category", "ProductImages", "Description",
    "Color", "Size", "Stock", "Status", "Gender"
]


def get_pld(html):
    marker = '__NEXT_DATA__" type="application/json">'
    start = html.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = html.find("</script>", start)
    try:
        data = json.loads(html[start:end])
        return data["props"]["pageProps"]["plpProps"]["productListingSectionProps"]["productListingData"]
    except Exception as e:
        print("خطا در parse JSON:", e)
        return None


def parse_hit(hit):
    title = hit.get("title", "")
    prices = hit.get("prices", [])
    outlet_price = ""
    original_price = ""
    for p in prices:
        if p.get("priceType") == "redPrice":
            outlet_price = str(p.get("price", ""))
        elif p.get("priceType") == "whitePrice":
            original_price = str(p.get("price", ""))
    if not original_price:
        original_price = outlet_price

    brand = hit.get("brandName", "H&M")
    color_field = hit.get("productColor", "")
    color = color_field.get("colorName", "") if isinstance(color_field, dict) else color_field

    pdp_url = hit.get("pdpUrl", "")
    if pdp_url.startswith("/"):
        pdp_url = "https://www2.hm.com" + pdp_url

    images = []
    for img_key in ["imageProductSrc", "imageModelSrc"]:
        src = hit.get(img_key)
        if src:
            images.append(src)
    for g in hit.get("galleryImages", []):
        if g.get("url"):
            images.append(g["url"])
    images = list(dict.fromkeys(images))

    sizes = hit.get("sizes", [])
    available_sizes = [s["name"] for s in sizes if s.get("stock", 0) and s.get("stock", 0) > 0]
    stock_status = "In stock" if available_sizes else "Out of stock"

    return {
        "Title": title,
        "OriginalPrice": original_price,
        "OutletPrice": outlet_price,
        "SourceStore": SOURCE_STORE,
        "SourceURL": pdp_url,
        "Brand": brand,
        "Category": hit.get("category", "Senhora"),
        "ProductImages": "|".join(images),
        "Description": hit.get("legalText", "") or hit.get("promotionalMarkerText", ""),
        "Color": color,
        "Size": ",".join(available_sizes),
        "Stock": stock_status,
        "Status": "Active",
        "Gender": "Woman"
    }


def fetch_page(page_num, max_retries=5):
    u = BASE_URL if page_num == 1 else f"{BASE_URL}?page={page_num}"
    for attempt in range(max_retries):
        try:
            r = cffi_requests.get(u, impersonate="chrome124", headers=HEADERS, timeout=40)
            if r.status_code == 200 and "__NEXT_DATA__" in r.text:
                return get_pld(r.text)
            print(f"صفحه {page_num}: status={r.status_code} (تلاش {attempt+1}/{max_retries})")
        except Exception as e:
            print(f"صفحه {page_num}: خطا {type(e).__name__}: {e} (تلاش {attempt+1}/{max_retries})")
        # تاخیر تصاعدی طولانی‌تر برای مقابله با 503 و بی‌ثباتی شبکه
        wait_time = (attempt + 1) * random.uniform(8, 15)
        print(f"   منتظر {wait_time:.1f} ثانیه قبل از تلاش بعدی...")
        time.sleep(wait_time)
    return None


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_completed_page": 0, "total_pages": None}


def save_progress(last_page, total_pages):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_completed_page": last_page, "total_pages": total_pages}, f)


def append_rows_to_csv(rows, write_header=False):
    mode = "w" if write_header else "a"
    with open(OUTPUT_FILE, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main():
    progress = load_progress()
    start_page = progress["last_completed_page"] + 1
    total_pages = progress["total_pages"]

    if start_page == 1:
        append_rows_to_csv([], write_header=True)
        print("شروع تازه: فایل CSV با هدر جدید ساخته شد.")
    else:
        print(f"ادامه از صفحه {start_page} (طبق فایل پیشرفت قبلی)...")

    page_num = start_page
    failed_pages = []

    while True:
        pld = fetch_page(page_num)

        if pld is None:
            print(f"صفحه {page_num}: پس از {5} تلاش ناموفق، به لیست تلاش مجدد اضافه شد.")
            failed_pages.append(page_num)
            page_num += 1
            if total_pages and page_num > total_pages:
                break
            time.sleep(5)
            continue

        if total_pages is None:
            total_pages = pld["pagination"].get("totalPages", 1)
            print(f"تعداد کل صفحات: {total_pages} | تعداد کل محصولات: {pld.get('totalHits')}")

        rows = [parse_hit(hit) for hit in pld.get("hits", [])]
        append_rows_to_csv(rows, write_header=False)
        save_progress(page_num, total_pages)

        print(f"صفحه {page_num}/{total_pages} ذخیره شد | تعداد این صفحه: {len(rows)}")

        if page_num >= total_pages:
            break
        page_num += 1
        time.sleep(random.uniform(2.0, 4.0))

    if failed_pages:
        print(f"\nصفحاتی که با شکست مواجه شدند: {failed_pages}")
        print("برای تلاش مجدد فقط همین صفحات، دوباره اسکریپت را اجرا کنید یا فایل hm_progress.json را دستی ویرایش کنید.")

    print(f"\nپایان اجرا. خروجی در فایل: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
