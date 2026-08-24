import sys
import time
import random
import re
import csv
import json
import os
import math
from curl_cffi import requests as cffi_requests

BASE_URL = "https://pt.tommy.com/mens-sale"
SOURCE_STORE = "Tommy Hilfiger"
OUTPUT_FILE = "tommy_Man_products{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
PROGRESS_FILE = "tommy_Man_progress{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
SCENE7 = "https://tommy-europe.scene7.com/is/image/TommyEurope/"

HEADERS = {
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

FIELDNAMES = [
    "Title", "OriginalPrice", "OutletPrice", "SourceStore", "SourceURL",
    "Brand", "Category", "ProductImages", "Description",
    "Color", "Size", "Stock", "Status", "Gender"
]

NEXT_MARKER = '__NEXT_DATA__" type="application/json">'


def get_next_data(html):
    start = html.find(NEXT_MARKER)
    if start == -1:
        return None
    start += len(NEXT_MARKER)
    end = html.find("</script>", start)
    try:
        return json.loads(html[start:end])
    except Exception as e:
        print("خطا در parse JSON:", e)
        return None


def fetch_url(url, max_retries=5):
    for attempt in range(max_retries):
        try:
            r = cffi_requests.get(url, impersonate="chrome124", headers=HEADERS, timeout=40)
            if r.status_code == 200 and "__NEXT_DATA__" in r.text:
                return r.text
            print(f"  status={r.status_code} (تلاش {attempt+1}/{max_retries}) -> {url}")
        except Exception as e:
            print(f"  خطا {type(e).__name__}: {e} (تلاش {attempt+1}/{max_retries}) -> {url}")
        wait_time = (attempt + 1) * random.uniform(6, 12)
        time.sleep(wait_time)
    return None


def get_plp_page(page_num):
    url = BASE_URL if page_num == 1 else f"{BASE_URL}?page={page_num}"
    html = fetch_url(url)
    if html is None:
        return None
    data = get_next_data(html)
    if data is None:
        return None
    try:
        queries = data["props"]["pageProps"]["initialState"]["api"]["queries"]
        key = [k for k in queries.keys() if k.startswith("getPLPProducts")][0]
        return queries[key]["data"]
    except Exception as e:
        print("خطا در استخراج PLP:", e)
        return None


def build_images(image_template, views):
    urls = []
    for v in views:
        img = image_template.replace("{view}", v)
        img = re.sub(r"\{size\}", "", img)
        urls.append(SCENE7 + img)
    return list(dict.fromkeys(urls))


def get_pdp_data(pdp_url):
    html = fetch_url(pdp_url)
    if html is None:
        return None
    data = get_next_data(html)
    if data is None:
        return None
    try:
        queries = data["props"]["pageProps"]["initialState"]["api"]["queries"]
        key = [k for k in queries.keys() if k.startswith("getProduct(")][0]
        return queries[key]["data"]["data"]
    except Exception as e:
        print("خطا در استخراج PDP:", e)
        return None


def get_size_label(v):
    """
    برخی محصولات (مثل جین‌ها) به‌جای فیلد ساده \'size\'،
    دو فیلد جدا \'width\' (کمر) و \'length\' (طول پا) دارند.
    برخی دیگر (مثل کیف‌ها) هیچ سایزی ندارند و فقط \'One Size\' هستند.
    """
    if v.get("size"):
        return v["size"]
    if "width" in v and "length" in v:
        return f"W{v['width']}L{v['length']}"
    if "width" in v:
        return f"W{v['width']}"
    return "One Size"


def parse_product(pdp):
    title = pdp.get("name", "")
    price_info = pdp.get("price", {})
    outlet_price = str(price_info.get("price", ""))
    original_price = str(price_info.get("wasPrice", "")) or outlet_price

    brand = pdp.get("brandName", "Tommy Hilfiger")
    color = pdp.get("translatedColour", "") or pdp.get("colour", "")
    category = pdp.get("productGroup", "") or pdp.get("division", "")

    url = "https://pt.tommy.com" + pdp.get("url", "")
    images = build_images(pdp.get("image", ""), pdp.get("views", []))

    variants = pdp.get("variants", [])
    available_sizes = [get_size_label(v) for v in variants if v.get("stockAvailability")]
    stock_status = "In stock" if available_sizes else "Out of stock"

    description = re.sub(r"<[^>]+>", " ", pdp.get("description", "") or "")
    description = re.sub(r"\s+", " ", description).strip()

    return {
        "Title": title,
        "OriginalPrice": original_price,
        "OutletPrice": outlet_price,
        "SourceStore": SOURCE_STORE,
        "SourceURL": url,
        "Brand": brand,
        "Category": category,
        "ProductImages": "|".join(images),
        "Description": description,
        "Color": color,
        "Size": ",".join(available_sizes),
        "Stock": stock_status,
        "Status": "Active",
        "Gender": "Woman"
    }


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"stage": "plp", "last_plp_page": 0, "product_urls": [], "last_pdp_index": 0}


def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f)


def csv_has_header():
    """
    بررسی می‌کند که آیا فایل CSV از قبل وجود دارد، خالی نیست
    و ردیف اول آن دقیقاً هدر مورد انتظار است.
    """
    if not os.path.exists(OUTPUT_FILE):
        return False
    if os.path.getsize(OUTPUT_FILE) == 0:
        return False
    with open(OUTPUT_FILE, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            first_row = next(reader)
        except StopIteration:
            return False
        return first_row == FIELDNAMES


def append_rows_to_csv(rows, write_header=False):
    mode = "w" if write_header else "a"
    with open(OUTPUT_FILE, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def reset_all():
    """حذف فایل پیشرفت و فایل CSV برای شروع کاملاً از صفر."""
    for path in (PROGRESS_FILE, OUTPUT_FILE):
        if os.path.exists(path):
            os.remove(path)
            print(f"حذف شد: {path}")
    print("ریست کامل انجام شد. اجرا از صفحهٔ اول شروع می‌شود.\n")


def main():
    if "--reset" in sys.argv:
        reset_all()

    progress = load_progress()

    if progress["stage"] == "plp":
        page_num = progress["last_plp_page"] + 1
        product_urls = set(progress["product_urls"])
        total_pages = None

        while True:
            print(f"در حال دریافت صفحهٔ لیست {page_num}...")
            pld = get_plp_page(page_num)
            if pld is None:
                print(f"صفحهٔ {page_num} شکست خورد، رد شد.")
                page_num += 1
                if total_pages and page_num > total_pages:
                    break
                continue

            meta = pld["meta"]["page"]
            if total_pages is None:
                total_pages = math.ceil(meta["total"] / meta["limit"])
                print(f"تعداد کل محصولات: {meta['total']} | تعداد کل صفحات: {total_pages}")

            for item in pld["data"]:
                full_url = "https://pt.tommy.com" + item["url"]
                product_urls.add(full_url)

            progress["last_plp_page"] = page_num
            progress["product_urls"] = list(product_urls)
            save_progress(progress)

            print(f"صفحهٔ {page_num}/{total_pages} | لینک‌های یکتا تا الان: {len(product_urls)}")

            if page_num >= total_pages:
                break
            page_num += 1
            time.sleep(random.uniform(1.5, 3.0))

        progress["stage"] = "pdp"
        progress["product_urls"] = list(product_urls)
        save_progress(progress)

    product_urls = progress["product_urls"]
    start_index = progress["last_pdp_index"]

    # هدر فقط زمانی نوشته می‌شود که واقعاً لازم باشد:
    # وقتی فایل وجود ندارد، خالی است، یا هدر صحیح را ندارد.
    # اگر فایل از قبل هدر درست دارد و --reset هم نزده باشید، از همان‌جا ادامه می‌دهد.
    if not csv_has_header():
        append_rows_to_csv([], write_header=True)
        print("فایل CSV با هدر صحیح ساخته/بازسازی شد.")
        if start_index > 0:
            # چون CSV از نو ساخته شد ولی شمارندهٔ پیشرفت صفر نیست،
            # باید شمارنده هم صفر شود تا داده‌ها از دست نروند یا خالی نمانند.
            start_index = 0
            progress["last_pdp_index"] = 0
            save_progress(progress)
            print("شمارندهٔ پیشرفت هم صفر شد تا با فایل CSV هماهنگ باشد؛ اجرا از محصول اول شروع می‌شود.")
    else:
        print(f"ادامه از محصول شماره {start_index + 1} از {len(product_urls)}...")

    for i in range(start_index, len(product_urls)):
        pdp_url = product_urls[i]
        try:
            pdp_data = get_pdp_data(pdp_url)
            if pdp_data is None:
                print(f"[{i+1}/{len(product_urls)}] خطا در دریافت: {pdp_url}")
            else:
                row = parse_product(pdp_data)
                append_rows_to_csv([row], write_header=False)
                print(f"[{i+1}/{len(product_urls)}] OK: {row['Title']} | سایز: {row['Size']}")
        except Exception as e:
            print(f"[{i+1}/{len(product_urls)}] خطای غیرمنتظره: {type(e).__name__}: {e} -> {pdp_url}")

        progress["last_pdp_index"] = i + 1
        save_progress(progress)
        time.sleep(random.uniform(1.0, 2.0))

    print(f"\nپایان اجرا. خروجی در فایل: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
