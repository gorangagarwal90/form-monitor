import asyncio
import csv
import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

from playwright.async_api import async_playwright

APP_VERSION = "3.0.0"

EXCLUDE = (
    "/shoppingcart", "/checkout", "/member/", "/login", "/register", "/contact",
    "/privacy", "/terms", "/education", "/blog", "/faq", "/about", "/search/",
    "/customer-service", "/returns", "/shipping", "/warranty", "/appraisal",
    "/track-order", "/order-status", "/wishlist", "/account", "/refer", "/careers",
    "/press", "/reviews", "/financing", "/appointment", "/gemologist"
)

SKU_RE = re.compile(r"(?:SKU|Item#|Item\s*#)\s*[:#-]?\s*([A-Za-z0-9-]{3,})", re.I)
EXCLUDE_ALT_RE = re.compile(
    r"thumbnail|certificate|picture of the author|details|logo|icon|sprite|"
    r"payment|review|packaging|loader|placeholder|flag|badge|footer|header|social",
    re.I
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
    p = urlparse(u).path.lower().rstrip("/")
    if p in ("", "/sitemap") or any(x in p for x in EXCLUDE):
        return False
    if re.search(r"\.(?:jpg|jpeg|png|webp|gif|svg|pdf|xml|txt|css|js)$", p):
        return False
    if re.search(r"-\d{4,}$", p):
        return False
    return True


def size_status(w, h, mw, mh):
    if w <= 0 or h <= 0:
        return "ERROR", "Image dimensions unavailable"
    bad = []
    if w < mw:
        bad.append(f"width {w} < {mw}")
    if h < mh:
        bad.append(f"height {h} < {mh}")
    if bad:
        return "FAIL", "; ".join(bad)
    if w < 1000 or h < 1000:
        return "PASS - LOW RES", "Meets minimum but is below 1000x1000 preferred"
    return "PASS", ""


def strip_transform(u):
    try:
        p = urlparse(u)
        drop = {
            "width", "height", "w", "h", "quality", "q",
            "format", "fit", "crop", "auto", "dpr"
        }
        q = [
            (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in drop
        ]
        return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), ""))
    except Exception:
        return ""


async def measure(page, u, cache):
    if not u:
        return 0, 0
    if u in cache:
        return cache[u]
    try:
        r = await page.evaluate(
            """async u => await new Promise(resolve => {
                const i = new Image();
                const t = setTimeout(() => resolve([0,0]), 15000);
                i.onload = () => {
                    clearTimeout(t);
                    resolve([i.naturalWidth || 0, i.naturalHeight || 0]);
                };
                i.onerror = () => {
                    clearTimeout(t);
                    resolve([0,0]);
                };
                i.src = u;
            })""",
            u,
        )
        val = (int(r[0] or 0), int(r[1] or 0))
    except Exception:
        val = (0, 0)
    cache[u] = val
    return val


async def settle_and_scroll(page):
    try:
        await page.wait_for_selector(
            ".product_card_outer, .gridProductCard, [class*='product_card_outer']",
            timeout=15000,
        )
    except Exception:
        pass

    last = -1
    stable = 0
    for _ in range(30):
        try:
            h = int(await page.evaluate("document.body.scrollHeight") or 0)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(450)
            stable = stable + 1 if h == last else 0
            last = h
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
        "els => els.map(a => ({u:a.href,t:(a.innerText||a.textContent||'').trim()}))"
    )
    d = {}
    for x in links:
        u = clean(sitemap, x.get("u"))
        if candidate(site, u):
            d[u] = x.get("t") or urlparse(u).path.strip("/")

    fallbacks = {
        "https://www.gemsny.com/sapphires/basic-search": "Loose Sapphires",
        "https://www.gemsny.com/sapphire-rings": "Sapphire Rings",
        "https://www.gemsny.com/sapphire-earrings": "Sapphire Earrings",
        "https://www.gemsny.com/emerald/basic-search": "Loose Emeralds",
    }
    for u, n in fallbacks.items():
        d.setdefault(u, n)

    pri = {u: i for i, u in enumerate(fallbacks)}
    items = sorted(d.items(), key=lambda x: (pri.get(x[0], 100), x[0]))
    if max_categories > 0:
        items = items[:max_categories]
    return [x for i, x in enumerate(items) if i % shards == shard]


async def page_meta(page):
    d = await page.evaluate(
        """() => ({
            body:(document.body?.innerText||'').slice(0,250000),
            links:[...document.querySelectorAll('a[href]')].map(a=>a.href)
        })"""
    )
    body = d["body"]
    expected = 0
    for pat in (
        r"Result\s*\(\s*([\d,]+)\s*\)",
        r"([\d,]+)\s+results\b",
        r"([\d,]+)\s+products\b",
        r"([\d,]+)\s+items\b",
    ):
        m = re.search(pat, body, re.I)
        if m:
            try:
                expected = int(m.group(1).replace(",", ""))
                break
            except Exception:
                pass

    q = dict(parse_qsl(urlparse(page.url).query))
    current = int(q.get("page", 1)) if str(q.get("page", 1)).isdigit() else 1

    nums = []
    for href in d["links"]:
        u = clean(page.url, href)
        if not u or not same_host(page.url, u):
            continue
        qq = dict(parse_qsl(urlparse(u).query))
        v = qq.get("page")
        if str(v).isdigit():
            nums.append((int(v), u))

    last = max([n for n, _ in nums], default=current)
    nxt = ""
    for n, u in sorted(nums):
        if n == current + 1:
            nxt = u
            break
    if not nxt:
        higher = [(n, u) for n, u in nums if n > current]
        if higher:
            nxt = sorted(higher)[0][1]

    return expected, current, last, nxt, bool(nums)


async def extract_product_images(page):
    return await page.evaluate(
        r"""() => {
          const abs = u => { try { return new URL(u, location.href).href } catch(e) { return '' } };
          const cards = [...document.querySelectorAll(
            '.product_card_outer, [class*="product_card_outer"]'
          )];

          const uniqueCards = [];
          const seenCards = new Set();
          for (const card of cards) {
            const outer = card.closest('.product_card_outer, [class*="product_card_outer"]') || card;
            if (!seenCards.has(outer)) {
              seenCards.add(outer);
              uniqueCards.push(outer);
            }
          }

          const out = [];
          for (const card of uniqueCards) {
            const text = (card.innerText || '').trim();
            const itemMatch = text.match(/(?:Item#|Item\s*#)\s*:?\s*([A-Za-z0-9-]{3,})/i);
            const links = [...card.querySelectorAll('a[href]')].map(a => ({
              href: abs(a.href),
              text: (a.innerText || a.textContent || '').trim()
            }));
            let productUrl = '';
            const detail = links.find(x => /View\s*Details/i.test(x.text));
            if (detail) productUrl = detail.href;
            if (!productUrl) {
              const likely = links.find(x => x.href && new URL(x.href).hostname === location.hostname);
              if (likely) productUrl = likely.href;
            }

            const titleNode = card.querySelector('h1,h2,h3,h4,[class*="title"],[class*="name"]');
            const title = titleNode ? (titleNode.innerText || titleNode.textContent || '').trim() : '';

            const imgs = [...card.querySelectorAll(
              '.product_img_wrap img, [class*="product_img_wrap"] img, img'
            )];

            for (const img of imgs) {
              const alt = (img.getAttribute('alt') || '').trim();
              const rawSrc = img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
              const declaredSrc = rawSrc ? abs(rawSrc) : '';
              const currentSrc = img.currentSrc || declaredSrc;
              const hay = alt + ' ' + text;
              const skuMatch = hay.match(/(?:SKU|Item#|Item\s*#)\s*[:#-]?\s*([A-Za-z0-9-]{3,})/i);
              const sku = skuMatch ? skuMatch[1] : (itemMatch ? itemMatch[1] : '');

              const inProductWrap = !!img.closest('.product_img_wrap, [class*="product_img_wrap"]');
              const productAlt = /\bSKU\s*[A-Za-z0-9-]{3,}/i.test(alt);
              if (!inProductWrap && !productAlt) continue;
              if (/thumbnail|certificate|picture of the author|details|logo|icon/i.test(alt)) continue;

              out.push({
                sku,
                product_url: productUrl,
                title,
                card_text: text.slice(0,1000),
                alt,
                current_src: abs(currentSrc),
                declared_src: declaredSrc || abs(currentSrc),
                natural_width: img.naturalWidth || 0,
                natural_height: img.naturalHeight || 0,
                dom_width: Number(img.getAttribute('width') || 0) || 0,
                dom_height: Number(img.getAttribute('height') || 0) || 0
              });
            }
          }
          return out;
        }"""
    )


async def grab(page, cat_name, cat_url, page_no, mw, mh, check_original):
    await settle_and_scroll(page)
    data = await extract_product_images(page)
    cache = {}
    rows = []
    seen = set()

    for x in data:
        sku = (x.get("sku") or "").strip()
        alt = (x.get("alt") or "").strip()
        if EXCLUDE_ALT_RE.search(alt):
            continue

        current_src = x.get("current_src") or ""
        declared_src = x.get("declared_src") or current_src
        if not declared_src:
            continue

        base = strip_transform(declared_src)
        dedupe_key = (sku or x.get("product_url") or "", base or declared_src)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        served_w = int(x.get("natural_width") or 0)
        served_h = int(x.get("natural_height") or 0)
        served_status, _ = size_status(served_w, served_h, mw, mh)

        declared_w, declared_h = await measure(page, declared_src, cache)
        declared_status, declared_reason = size_status(declared_w, declared_h, mw, mh)

        original = base if check_original else ""
        original_w = original_h = 0
        original_status = "NOT CHECKED"
        original_reason = ""
        if original:
            original_w, original_h = await measure(page, original, cache)
            original_status, original_reason = size_status(original_w, original_h, mw, mh)

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
            primary_status, primary_reason = size_status(served_w, served_h, mw, mh)
            basis = "BROWSER CURRENT SRC"
            primary_w, primary_h = served_w, served_h

        pu = x.get("product_url") or ""
        if pu and not same_host(cat_url, pu):
            pu = ""

        rows.append({
            "category_name": cat_name,
            "category_url": cat_url,
            "category_page_url": page.url,
            "page_number": page_no,
            "sku": sku,
            "product_key": sku or pu or base or declared_src,
            "product_title": (x.get("title") or "").strip(),
            "product_url": pu,
            "image_alt": alt,
            "image_url": declared_src,
            "current_src_url": current_src,
            "served_width": served_w,
            "served_height": served_h,
            "served_status": served_status,
            "declared_src_width": declared_w,
            "declared_src_height": declared_h,
            "declared_src_status": declared_status,
            "original_url": original,
            "original_width": original_w,
            "original_height": original_h,
            "original_status": original_status,
            "threshold_width": mw,
            "threshold_height": mh,
            "status": primary_status,
            "compliance_basis": basis,
            "compliance_width": primary_w,
            "compliance_height": primary_h,
            "failure_reason": primary_reason,
            "audit_mode": "PLP",
            "audited_at": datetime.now().isoformat(timespec="seconds"),
        })

    return rows


async def main(a):
    out = Path(a.output_dir)
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

        cats = await discover(
            page, a.site, a.sitemap, a.max_categories, a.shard_index, a.shard_count
        )
        print(f"Auditor v{APP_VERSION} — shard {a.shard_index+1}/{a.shard_count}: {len(cats)} categories", flush=True)

        for idx, (u, n) in enumerate(cats, 1):
            page_count = 0
            visited = set()
            catrows = []
            cov_status = "COMPLETE"
            note = ""
            expected = 0
            last = 1
            pagination = False

            try:
                await page.goto(u, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1200)

                while True:
                    cur = page.url
                    if cur in visited:
                        note = "Repeated pagination URL encountered"
                        break
                    visited.add(cur)
                    page_count += 1

                    e, cp, lp, nxt, pd = await page_meta(page)
                    expected = max(expected, e)
                    last = max(last, lp)
                    pagination = pagination or pd

                    rr = await grab(
                        page, n, u, page_count, a.min_width, a.min_height, a.check_original
                    )
                    catrows.extend(rr)
                    rows.extend(rr)
                    print(
                        f"[{idx}/{len(cats)}] {n} page {page_count}: "
                        f"{len(rr)} unique product images",
                        flush=True,
                    )

                    if a.max_pages > 0 and page_count >= a.max_pages:
                        if nxt or last > page_count:
                            cov_status = "PARTIAL - PAGE LIMIT"
                        break

                    if not nxt and pagination and page_count < last:
                        p0 = urlparse(u)
                        q = dict(parse_qsl(p0.query))
                        q["page"] = str(page_count + 1)
                        nxt = urlunparse(
                            (p0.scheme, p0.netloc, p0.path, p0.params, urlencode(q), "")
                        )

                    if not nxt:
                        break

                    await asyncio.sleep(a.delay)
                    await page.goto(nxt, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(900)

            except Exception as e:
                cov_status = "ERROR"
                note = str(e)

            keys = {r["product_key"] for r in catrows if r["product_key"]}
            if cov_status == "COMPLETE":
                if not catrows:
                    cov_status = "REVIEW - NO PRODUCT IMAGES"
                elif pagination and page_count < last:
                    cov_status = "INCOMPLETE - PAGINATION"
                elif expected and a.max_pages == 0 and len(keys) < expected * 0.85:
                    cov_status = "REVIEW - PRODUCT COUNT MISMATCH"

            coverage.append({
                "category_name": n,
                "category_url": u,
                "status": cov_status,
                "pages_visited": page_count,
                "pagination_detected": pagination,
                "expected_last_page": last,
                "expected_products_if_detected": expected,
                "unique_products_detected": len(keys),
                "images_checked": len(catrows),
                "failed_images": sum(1 for r in catrows if r["status"] == "FAIL"),
                "image_errors": sum(1 for r in catrows if r["status"] == "ERROR"),
                "note": note,
            })
            await asyncio.sleep(a.delay)

        await context.close()
        await browser.close()

    fields = [
        "category_name", "category_url", "category_page_url", "page_number",
        "sku", "product_key", "product_title", "product_url", "image_alt",
        "image_url", "current_src_url",
        "served_width", "served_height", "served_status",
        "declared_src_width", "declared_src_height", "declared_src_status",
        "original_url", "original_width", "original_height", "original_status",
        "threshold_width", "threshold_height", "status", "compliance_basis",
        "compliance_width", "compliance_height", "failure_reason",
        "audit_mode", "audited_at",
    ]
    with open(out / "all_images.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    cov_fields = list(coverage[0].keys()) if coverage else [
        "category_name", "category_url", "status"
    ]
    with open(out / "coverage.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cov_fields)
        w.writeheader()
        w.writerows(coverage)

    with open(out / "failed_images.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows([r for r in rows if r["status"] in ("FAIL", "ERROR")])

    (out / "shard_info.json").write_text(
        json.dumps({
            "version": APP_VERSION,
            "shard_index": a.shard_index,
            "shard_count": a.shard_count,
            "categories": len(coverage),
            "images": len(rows),
            "failures": sum(1 for r in rows if r["status"] == "FAIL"),
        }, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="https://www.gemsny.com")
    ap.add_argument("--sitemap", default="https://www.gemsny.com/sitemap")
    ap.add_argument("--min-width", type=int, default=500)
    ap.add_argument("--min-height", type=int, default=500)
    ap.add_argument("--delay", type=float, default=2)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1)
    ap.add_argument("--max-categories", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=0)
    ap.add_argument("--check-original", action="store_true")
    ap.add_argument("--no-check-original", dest="check_original", action="store_false")
    ap.set_defaults(check_original=True)
    asyncio.run(main(ap.parse_args()))
