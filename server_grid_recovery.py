import argparse, asyncio, csv, math, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from PIL import Image
from playwright.async_api import async_playwright

from audit import extract_product_images, strip_transform, EXCLUDE_ALT_RE, size_status

TARGETS = [
    ("African Ruby Loose Gemstones", "https://www.gemsny.com/ruby/basic-search/african"),
    ("Blue Sapphire MYO Engagement Ring Gemstones", "https://www.gemsny.com/sapphires/basic-search/blue-sapphires?myo=engagement-rings"),
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"


def page_url(base, n):
    p = urlparse(base)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q["page"] = str(n)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), ""))


def measure_source(url):
    if not url:
        return (0, 0, "empty source URL")
    try:
        with requests.get(url, headers={"User-Agent": UA, "Referer": "https://www.gemsny.com/"}, timeout=25, stream=True) as r:
            r.raise_for_status()
            # PIL normally obtains dimensions from the image header without decoding the full image.
            with Image.open(r.raw) as im:
                return (int(im.width or 0), int(im.height or 0), "")
    except Exception as e:
        return (0, 0, f"{type(e).__name__}: {e}")


async def expected_count(page):
    body = await page.evaluate("() => document.body?.innerText || ''")
    m = re.search(r"Result\s*\(\s*([\d,]+)\s*\)", body, re.I)
    return int(m.group(1).replace(',', '')) if m else 0


async def recover(target_index, output_dir, min_width, min_height):
    name, base = TARGETS[target_index]
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    products = {}
    expected = 0
    page_size = 24

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1200}, user_agent=UA)
        page = await context.new_page()

        await page.goto(base, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(1800)
        expected = await expected_count(page)
        if expected <= 0:
            raise RuntimeError(f"Could not determine expected product count for {base}")
        total_pages = math.ceil(expected / page_size)
        print(f"{name}: expected={expected} pages={total_pages}", flush=True)

        repeated_pages = 0
        previous_keys = None
        for n in range(1, total_pages + 1):
            u = page_url(base, n)
            await page.goto(u, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(700)
            # A short scroll is enough to hydrate the 24 server-rendered cards and lazy image attributes.
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(450)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(250)

            items = await extract_product_images(page)
            page_keys = set()
            for item in items:
                alt = (item.get("alt") or "").strip()
                if alt and EXCLUDE_ALT_RE.search(alt):
                    continue
                sku = (item.get("sku") or "").strip()
                product_url = item.get("product_url") or ""
                declared = item.get("declared_src") or item.get("current_src") or ""
                source = strip_transform(declared) or declared
                key = sku or product_url
                if not key or not source:
                    continue
                page_keys.add(key)
                products.setdefault(key, {
                    "category_name": name,
                    "category_url": base,
                    "category_page_url": u,
                    "page_number": n,
                    "sku": sku,
                    "product_key": key,
                    "product_title": (item.get("title") or "").strip(),
                    "product_url": product_url,
                    "image_alt": alt,
                    "card_family": item.get("card_family") or "LOOSE_CARD",
                    "image_url": declared,
                    "original_url": source,
                })

            if previous_keys is not None and page_keys == previous_keys:
                repeated_pages += 1
            else:
                repeated_pages = 0
            if repeated_pages >= 2:
                raise RuntimeError(f"Pagination repeated the same product set through page {n}; refusing false completeness")
            previous_keys = page_keys
            print(f"{name}: page {n}/{total_pages} cards={len(page_keys)} cumulative={len(products)}", flush=True)

        await context.close()
        await browser.close()

    if len(products) != expected:
        raise RuntimeError(f"STRICT COVERAGE FAILURE for {name}: expected {expected}, got {len(products)} unique products")

    unique_sources = sorted({r["original_url"] for r in products.values() if r["original_url"]})
    dims = {}
    with ThreadPoolExecutor(max_workers=24) as ex:
        futures = {ex.submit(measure_source, u): u for u in unique_sources}
        done = 0
        for f in as_completed(futures):
            u = futures[f]
            try:
                dims[u] = f.result()
            except Exception as e:
                dims[u] = (0, 0, repr(e))
            done += 1
            if done % 250 == 0 or done == len(unique_sources):
                print(f"{name}: measured {done}/{len(unique_sources)} source images", flush=True)

    rows = []
    for r in products.values():
        w, h, err = dims.get(r["original_url"], (0, 0, "not measured"))
        status, reason = size_status(w, h, min_width, min_height)
        if err:
            status = "ERROR"
            reason = err
        row = dict(r)
        row.update({
            "original_width": w,
            "original_height": h,
            "original_status": status,
            "threshold_width": min_width,
            "threshold_height": min_height,
            "status": status,
            "compliance_basis": "ORIGINAL SOURCE",
            "compliance_width": w,
            "compliance_height": h,
            "failure_reason": reason,
            "audit_mode": "FORCED_SERVER_GRID_RECOVERY",
            "audited_at": datetime.now().isoformat(timespec="seconds"),
        })
        rows.append(row)

    fields = [
        "category_name","category_url","category_page_url","page_number","sku","product_key",
        "product_title","product_url","image_alt","card_family","image_url","original_url",
        "original_width","original_height","original_status","threshold_width","threshold_height",
        "status","compliance_basis","compliance_width","compliance_height","failure_reason",
        "audit_mode","audited_at",
    ]
    with open(out / "all_images.csv", "w", newline="", encoding="utf-8-sig") as f:
        wri = csv.DictWriter(f, fieldnames=fields); wri.writeheader(); wri.writerows(rows)
    with open(out / "failed_images.csv", "w", newline="", encoding="utf-8-sig") as f:
        wri = csv.DictWriter(f, fieldnames=fields); wri.writeheader(); wri.writerows([r for r in rows if r["status"] in ("FAIL","ERROR")])

    coverage = {
        "category_name": name,
        "category_url": base,
        "status": "COMPLETE",
        "pages_visited": total_pages,
        "expected_products_if_detected": expected,
        "unique_products_detected": len(products),
        "images_checked": len(rows),
        "failed_images": sum(r["status"] == "FAIL" for r in rows),
        "image_errors": sum(r["status"] == "ERROR" for r in rows),
        "note": "Strict forced-page server-rendered recovery; unique product count exactly matches live Result count.",
    }
    with open(out / "coverage.csv", "w", newline="", encoding="utf-8-sig") as f:
        wri = csv.DictWriter(f, fieldnames=list(coverage)); wri.writeheader(); wri.writerow(coverage)
    print("FINAL", coverage, flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-index", type=int, required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--min-width", type=int, default=500)
    ap.add_argument("--min-height", type=int, default=500)
    args = ap.parse_args()
    asyncio.run(recover(args.target_index, args.output_dir, args.min_width, args.min_height))
