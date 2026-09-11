import asyncio
import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from playwright.async_api import async_playwright

APP_VERSION = "3.1.0"

EXCLUDE = (
    "/shoppingcart", "/checkout", "/member/", "/login", "/register", "/contact",
    "/privacy", "/terms", "/education", "/blog", "/faq", "/about", "/search/",
    "/customer-service", "/returns", "/shipping", "/warranty", "/appraisal",
    "/track-order", "/order-status", "/wishlist", "/account", "/refer", "/careers",
    "/press", "/reviews", "/financing", "/appointment", "/gemologist",
)

EXCLUDE_ALT_RE = re.compile(
    r"thumbnail|certificate|picture of the author|details|logo|icon|sprite|payment|"
    r"review|packaging|loader|placeholder|flag|badge|footer|header|social",
    re.I,
)


def same_host(a, b):
    return urlparse(a).netloc.lower().replace("www.", "") == urlparse(b).netloc.lower().replace("www.", "")


def clean(base, href):
    try:
        u = urljoin(base, href or "")
        p = urlparse(u)
        if p.scheme not in ("http", "https"):
            return ""
        return urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))
    except Exception:
        return ""


def candidate(site, u):
    if not u or not same_host(site, u):
        return False
    path = urlparse(u).path.lower().rstrip("/")
    if path in ("", "/sitemap") or any(x in path for x in EXCLUDE):
        return False
    if re.search(r"\.(?:jpg|jpeg|png|webp|gif|svg|pdf|xml|txt|css|js)$", path):
        return False
    # Common loose-stone PDP pattern: descriptive slug ending in a long numeric SKU.
    if re.search(r"-\d{4,}$", path):
        return False
    return True


def size_status(w, h, min_w, min_h):
    if w <= 0 or h <= 0:
        return "ERROR", "Image dimensions unavailable"
    reasons = []
    if w < min_w:
        reasons.append(f"width {w} < {min_w}")
    if h < min_h:
        reasons.append(f"height {h} < {min_h}")
    if reasons:
        return "FAIL", "; ".join(reasons)
    if w < 1000 or h < 1000:
        return "PASS - LOW RES", "Meets minimum but is below 1000x1000 preferred"
    return "PASS", ""


def strip_transform(url):
    try:
        p = urlparse(url)
        drop = {"width", "height", "w", "h", "quality", "q", "format", "fit", "crop", "auto", "dpr"}
        query = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in drop
        ]
        return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(query), ""))
    except Exception:
        return ""


async def measure(page, url, cache):
    if not url:
        return 0, 0
    if url in cache:
        return cache[url]
    try:
        result = await page.evaluate(
            """async u => await new Promise(resolve => {
                const img = new Image();
                const timer = setTimeout(() => resolve([0, 0]), 15000);
                img.onload = () => {
                    clearTimeout(timer);
                    resolve([img.naturalWidth || 0, img.naturalHeight || 0]);
                };
                img.onerror = () => {
                    clearTimeout(timer);
                    resolve([0, 0]);
                };
                img.src = u;
            })""",
            url,
        )
        value = (int(result[0] or 0), int(result[1] or 0))
    except Exception:
        value = (0, 0)
    cache[url] = value
    return value


async def settle_and_scroll(page):
    # Product grids are lazy-rendered. Scrolling is more reliable than waiting on one selector
    # because GemsNY uses different markup for loose stones vs. jewelry landing pages.
    await page.wait_for_timeout(700)
    last_height = -1
    stable = 0
    for _ in range(30):
        try:
            height = int(await page.evaluate("document.body.scrollHeight") or 0)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(450)
            stable = stable + 1 if height == last_height else 0
            last_height = height
            if stable >= 3:
                break
        except Exception:
            break
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(400)
    except Exception:
        pass


async def discover(page, site, sitemap, max_categories, shard, shards):
    await page.goto(sitemap, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1000)
    links = await page.eval_on_selector_all(
        "a[href]",
        "els => els.map(a => ({u:a.href,t:(a.innerText||a.textContent||'').trim()}))",
    )

    found = {}
    for item in links:
        u = clean(sitemap, item.get("u"))
        if candidate(site, u):
            found[u] = item.get("t") or urlparse(u).path.strip("/")

    # Structural QA URLs are kept first so test limits exercise distinct page types.
    fallbacks = {
        "https://www.gemsny.com/sapphires/basic-search": "Loose Sapphires",
        "https://www.gemsny.com/sapphire-rings": "Sapphire Rings",
        "https://www.gemsny.com/sapphire-earrings": "Sapphire Earrings",
        "https://www.gemsny.com/emerald/basic-search": "Loose Emeralds",
    }
    for u, name in fallbacks.items():
        found.setdefault(u, name)

    priority = {u: i for i, u in enumerate(fallbacks)}
    items = sorted(found.items(), key=lambda x: (priority.get(x[0], 100), x[0]))
    if max_categories > 0:
        items = items[:max_categories]
    return [item for i, item in enumerate(items) if i % shards == shard]


async def page_meta(page):
    data = await page.evaluate(
        """() => ({
            body:(document.body?.innerText||'').slice(0,250000),
            links:[...document.querySelectorAll('a[href]')].map(a=>a.href)
        })"""
    )
    body = data["body"]
    expected = 0
    for pattern in (
        r"Result\s*\(\s*([\d,]+)\s*\)",
        r"([\d,]+)\s+results\b",
        r"([\d,]+)\s+products\b",
        r"([\d,]+)\s+items\b",
    ):
        match = re.search(pattern, body, re.I)
        if match:
            try:
                expected = int(match.group(1).replace(",", ""))
                break
            except Exception:
                pass

    query = dict(parse_qsl(urlparse(page.url).query))
    current = int(query.get("page", 1)) if str(query.get("page", 1)).isdigit() else 1

    numbered = []
    for href in data["links"]:
        u = clean(page.url, href)
        if not u or not same_host(page.url, u):
            continue
        q = dict(parse_qsl(urlparse(u).query))
        value = q.get("page")
        if str(value).isdigit():
            numbered.append((int(value), u))

    last = max([n for n, _ in numbered], default=current)
    next_url = ""
    for number, u in sorted(numbered):
        if number == current + 1:
            next_url = u
            break
    if not next_url:
        higher = [(n, u) for n, u in numbered if n > current]
        if higher:
            next_url = sorted(higher)[0][1]

    return expected, current, last, next_url, bool(numbered)


async def extract_product_images(page):
    """Return product-card imagery from both live GemsNY card families.

    Family A: loose-gem cards (.product_card_outer / .product_img_wrap)
    Family B: jewelry landing cards (/image-jewelry/ image inside a /preset/ link
              whose card says View Details). Jewelry cards often have empty alt text.
    """
    return await page.evaluate(
        r"""() => {
          const abs = u => { try { return new URL(u, location.href).href } catch(e) { return '' } };
          const out = [];
          const emitted = new Set();

          const pushImage = (img, productUrl, title, sku, cardText, kind) => {
            if (!img) return;
            const rawSrc = img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
            const declaredSrc = rawSrc ? abs(rawSrc) : '';
            const currentSrc = abs(img.currentSrc || declaredSrc);
            if (!declaredSrc && !currentSrc) return;
            const alt = (img.getAttribute('alt') || '').trim();
            const key = [kind, productUrl || '', sku || '', declaredSrc || currentSrc].join('|');
            if (emitted.has(key)) return;
            emitted.add(key);
            out.push({
              sku: sku || '',
              product_url: productUrl || '',
              title: title || '',
              card_text: (cardText || '').slice(0, 1400),
              alt,
              current_src: currentSrc,
              declared_src: declaredSrc || currentSrc,
              natural_width: img.naturalWidth || 0,
              natural_height: img.naturalHeight || 0,
              dom_width: Number(img.getAttribute('width') || 0) || 0,
              dom_height: Number(img.getAttribute('height') || 0) || 0,
              card_family: kind
            });
          };

          // A) Loose gemstone grids.
          const looseCards = [...document.querySelectorAll('.product_card_outer, [class*="product_card_outer"], .gridProductCard, [class*="gridProductCard"]')];
          const looseSeen = new Set();
          for (const node of looseCards) {
            const card = node.closest('.product_card_outer, [class*="product_card_outer"], .gridProductCard, [class*="gridProductCard"]') || node;
            if (looseSeen.has(card)) continue;
            looseSeen.add(card);

            const text = (card.innerText || '').trim();
            const itemMatch = text.match(/(?:Item#|Item\s*#)\s*:?\s*([A-Za-z0-9-]{3,})/i);
            const links = [...card.querySelectorAll('a[href]')];
            let detail = links.find(a => /View\s*Details/i.test((a.innerText || a.textContent || '').trim()));
            if (!detail) detail = links.find(a => a.href && new URL(a.href).hostname === location.hostname) || null;
            const productUrl = detail ? abs(detail.href) : '';

            const titleNode = card.querySelector('h1,h2,h3,h4,[class*="title"],[class*="name"]');
            const title = titleNode ? (titleNode.innerText || titleNode.textContent || '').trim() : '';
            const imgs = [...card.querySelectorAll('.product_img_wrap img, [class*="product_img_wrap"] img, img')];

            for (const img of imgs) {
              const alt = (img.getAttribute('alt') || '').trim();
              const hay = alt + ' ' + text;
              const skuMatch = hay.match(/(?:SKU|Item#|Item\s*#)\s*[:#-]?\s*([A-Za-z0-9-]{3,})/i);
              const sku = skuMatch ? skuMatch[1] : (itemMatch ? itemMatch[1] : '');
              const inProductWrap = !!img.closest('.product_img_wrap, [class*="product_img_wrap"]');
              const productAlt = /\bSKU\s*[A-Za-z0-9-]{3,}/i.test(alt);
              if (!inProductWrap && !productAlt) continue;
              if (/thumbnail|certificate|picture of the author|details|logo|icon/i.test(alt)) continue;
              pushImage(img, productUrl, title, sku, text, 'LOOSE_CARD');
            }
          }

          // B) Jewelry landing/preset cards. These cards commonly have an empty alt
          // and no visible SKU, so we derive the product key from /image-jewelry/<code>/.
          for (const img of [...document.images]) {
            const raw = img.getAttribute('src') || img.getAttribute('data-src') || img.currentSrc || '';
            const src = abs(raw);
            if (!/\/image-jewelry\//i.test(src)) continue;

            const anchor = img.closest('a[href*="/preset/"]');
            if (!anchor) continue;
            const cardText = (anchor.innerText || anchor.textContent || '').trim();
            if (!/View\s*Details/i.test(cardText)) continue;

            const productUrl = abs(anchor.href);
            if (!productUrl) continue;

            const assetMatch = src.match(/\/image-jewelry\/([^/]+)\//i);
            const sku = assetMatch ? assetMatch[1] : '';

            const lines = cardText.split(/\n+/).map(s => s.trim()).filter(Boolean);
            const title = lines.find(s => !/^\$[\d,.]+$/.test(s) && !/^View\s*Details$/i.test(s)) || '';
            pushImage(img, productUrl, title, sku, cardText, 'JEWELRY_CARD');
          }

          return out;
        }"""
    )


async def grab(page, cat_name, cat_url, page_no, min_w, min_h, check_original):
    await settle_and_scroll(page)
    data = await extract_product_images(page)
    cache = {}
    rows = []
    seen = set()

    for item in data:
        sku = (item.get("sku") or "").strip()
        alt = (item.get("alt") or "").strip()
        if alt and EXCLUDE_ALT_RE.search(alt):
            continue

        current_src = item.get("current_src") or ""
        declared_src = item.get("declared_src") or current_src
        if not declared_src:
            continue

        original_candidate = strip_transform(declared_src)
        dedupe_key = (sku or item.get("product_url") or "", original_candidate or declared_src)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        served_w = int(item.get("natural_width") or 0)
        served_h = int(item.get("natural_height") or 0)
        served_status, _ = size_status(served_w, served_h, min_w, min_h)

        declared_w, declared_h = await measure(page, declared_src, cache)
        declared_status, declared_reason = size_status(declared_w, declared_h, min_w, min_h)

        original_url = original_candidate if check_original else ""
        original_w = original_h = 0
        original_status = "NOT CHECKED"
        original_reason = ""
        if original_url:
            original_w, original_h = await measure(page, original_url, cache)
            original_status, original_reason = size_status(original_w, original_h, min_w, min_h)

        # The user's requirement is source/product-image quality, not the deliberately
        # responsive copy selected by the browser for a small PLP card.
        if check_original and original_status != "ERROR":
            primary_status = original_status
            primary_reason = original_reason
            basis = "ORIGINAL SOURCE"
            primary_w, primary_h = original_w, original_h
        elif declared_status != "ERROR":
            primary_status = declared_status
            primary_reason = declared_reason
            basis = "DECLARED SRC"
            primary_w, primary_h = declared_w, declared_h
        else:
            primary_status, primary_reason = size_status(served_w, served_h, min_w, min_h)
            basis = "BROWSER CURRENT SRC"
            primary_w, primary_h = served_w, served_h

        product_url = item.get("product_url") or ""
        if product_url and not same_host(cat_url, product_url):
            product_url = ""

        rows.append({
            "category_name": cat_name,
            "category_url": cat_url,
            "category_page_url": page.url,
            "page_number": page_no,
            "sku": sku,
            "product_key": sku or product_url or original_candidate or declared_src,
            "product_title": (item.get("title") or "").strip(),
            "product_url": product_url,
            "image_alt": alt,
            "card_family": item.get("card_family") or "",
            "image_url": declared_src,
            "current_src_url": current_src,
            "served_width": served_w,
            "served_height": served_h,
            "served_status": served_status,
            "declared_src_width": declared_w,
            "declared_src_height": declared_h,
            "declared_src_status": declared_status,
            "original_url": original_url,
            "original_width": original_w,
            "original_height": original_h,
            "original_status": original_status,
            "threshold_width": min_w,
            "threshold_height": min_h,
            "status": primary_status,
            "compliance_basis": basis,
            "compliance_width": primary_w,
            "compliance_height": primary_h,
            "failure_reason": primary_reason,
            "audit_mode": "PLP",
            "audited_at": datetime.now().isoformat(timespec="seconds"),
        })

    return rows


async def main(args):
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    coverage = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/151 Safari/537.36"
            ),
        )
        page = await context.new_page()

        categories = await discover(
            page,
            args.site,
            args.sitemap,
            args.max_categories,
            args.shard_index,
            args.shard_count,
        )
        print(
            f"Auditor v{APP_VERSION} — shard {args.shard_index + 1}/{args.shard_count}: "
            f"{len(categories)} categories",
            flush=True,
        )

        for index, (category_url, category_name) in enumerate(categories, 1):
            page_count = 0
            visited = set()
            category_rows = []
            coverage_status = "COMPLETE"
            note = ""
            expected = 0
            last_page = 1
            pagination = False

            try:
                await page.goto(category_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1200)

                while True:
                    current_url = page.url
                    if current_url in visited:
                        note = "Repeated pagination URL encountered"
                        break
                    visited.add(current_url)
                    page_count += 1

                    exp, current_page, last, next_url, has_pagination = await page_meta(page)
                    expected = max(expected, exp)
                    last_page = max(last_page, last)
                    pagination = pagination or has_pagination

                    found = await grab(
                        page,
                        category_name,
                        category_url,
                        page_count,
                        args.min_width,
                        args.min_height,
                        args.check_original,
                    )
                    category_rows.extend(found)
                    rows.extend(found)
                    print(
                        f"[{index}/{len(categories)}] {category_name} page {page_count}: "
                        f"{len(found)} unique product images",
                        flush=True,
                    )

                    if args.max_pages > 0 and page_count >= args.max_pages:
                        if next_url or last_page > page_count:
                            coverage_status = "PARTIAL - PAGE LIMIT"
                        break

                    if not next_url and pagination and page_count < last_page:
                        parsed = urlparse(category_url)
                        query = dict(parse_qsl(parsed.query))
                        query["page"] = str(page_count + 1)
                        next_url = urlunparse(
                            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query), "")
                        )

                    if not next_url:
                        break

                    await asyncio.sleep(args.delay)
                    await page.goto(next_url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(900)

            except Exception as exc:
                coverage_status = "ERROR"
                note = str(exc)

            product_keys = {r["product_key"] for r in category_rows if r["product_key"]}
            if coverage_status == "COMPLETE":
                if not category_rows:
                    coverage_status = "REVIEW - NO PRODUCT IMAGES"
                elif pagination and page_count < last_page:
                    coverage_status = "INCOMPLETE - PAGINATION"
                elif expected and args.max_pages == 0 and len(product_keys) < expected * 0.85:
                    coverage_status = "REVIEW - PRODUCT COUNT MISMATCH"

            coverage.append({
                "category_name": category_name,
                "category_url": category_url,
                "status": coverage_status,
                "pages_visited": page_count,
                "pagination_detected": pagination,
                "expected_last_page": last_page,
                "expected_products_if_detected": expected,
                "unique_products_detected": len(product_keys),
                "images_checked": len(category_rows),
                "failed_images": sum(1 for r in category_rows if r["status"] == "FAIL"),
                "image_errors": sum(1 for r in category_rows if r["status"] == "ERROR"),
                "note": note,
            })
            await asyncio.sleep(args.delay)

        await context.close()
        await browser.close()

    fields = [
        "category_name", "category_url", "category_page_url", "page_number",
        "sku", "product_key", "product_title", "product_url", "image_alt", "card_family",
        "image_url", "current_src_url", "served_width", "served_height", "served_status",
        "declared_src_width", "declared_src_height", "declared_src_status",
        "original_url", "original_width", "original_height", "original_status",
        "threshold_width", "threshold_height", "status", "compliance_basis",
        "compliance_width", "compliance_height", "failure_reason", "audit_mode", "audited_at",
    ]

    with open(out / "all_images.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    coverage_fields = list(coverage[0].keys()) if coverage else ["category_name", "category_url", "status"]
    with open(out / "coverage.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=coverage_fields)
        writer.writeheader()
        writer.writerows(coverage)

    with open(out / "failed_images.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows([r for r in rows if r["status"] in ("FAIL", "ERROR")])

    (out / "shard_info.json").write_text(
        json.dumps({
            "version": APP_VERSION,
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
            "categories": len(coverage),
            "images": len(rows),
            "failures": sum(1 for r in rows if r["status"] == "FAIL"),
        }, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="https://www.gemsny.com")
    parser.add_argument("--sitemap", default="https://www.gemsny.com/sitemap")
    parser.add_argument("--min-width", type=int, default=500)
    parser.add_argument("--min-height", type=int, default=500)
    parser.add_argument("--delay", type=float, default=2)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--max-categories", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--check-original", action="store_true")
    parser.add_argument("--no-check-original", dest="check_original", action="store_false")
    parser.set_defaults(check_original=True)
    asyncio.run(main(parser.parse_args()))
