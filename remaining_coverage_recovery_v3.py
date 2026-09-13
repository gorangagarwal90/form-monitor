import argparse, asyncio, csv, json, math, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse, urljoin

import requests
from PIL import Image
from playwright.async_api import async_playwright

from audit import extract_product_images, strip_transform, EXCLUDE_ALT_RE, settle_and_scroll, size_status

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
NON_PRODUCT_RE = re.compile(r"/(?:accessibility|about|privacy|terms|contact|education|blog|faq|returns|shipping|warranty|appraisal|track-order|order-status|refer|careers|press|reviews|financing|appointment|gemologist|login|register)(?:/|$)", re.I)
IMAGE_KEY_RE = re.compile(r"image|img|photo|picture|thumbnail|media", re.I)
ID_KEYS = ('sku','item_number','itemNumber','product_sku','productSku','product_code','productCode','id','product_id','productId','code','stock_number','stockNumber')
TITLE_KEYS = ('title','name','product_name','productName','display_name','displayName')
URL_KEYS = ('redirect_url','redirectUrl','url','product_url','productUrl','detail_url','detailUrl','slug')


def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def page_url(base, n):
    p = urlparse(base)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q['page'] = str(n)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), ''))


def as_int(v, default=0):
    try:
        return int(float(str(v).replace(',', '').strip()))
    except Exception:
        return default


def live_expected(body):
    for pat in (r"Result\s*\(\s*([\d,]+)\s*\)", r"([\d,]+)\s+results\b", r"([\d,]+)\s+products\b", r"([\d,]+)\s+items\b"):
        m = re.search(pat, body, re.I)
        if m:
            return int(m.group(1).replace(',', ''))
    return None


def first_value(d, keys):
    if not isinstance(d, dict):
        return ''
    for k in keys:
        v = d.get(k)
        if v not in (None, '', [], {}):
            return v
    return ''


def normalize_url(v, base='https://www.gemsny.com/'):
    if not isinstance(v, str) or not v.strip():
        return ''
    v = v.strip()
    if v.startswith('//'):
        return 'https:' + v
    if v.startswith('http://') or v.startswith('https://'):
        return v
    if v.startswith('/'):
        return urljoin(base, v)
    return ''


def image_from_obj(d):
    if not isinstance(d, dict):
        return ''
    preferred = ('image','image_url','imageUrl','img','main_image','mainImage','product_image','productImage','large_image','largeImage','media_url','mediaUrl','picture')
    for k in preferred:
        v = d.get(k)
        if isinstance(v, str):
            u = normalize_url(v)
            if u:
                return u
        if isinstance(v, dict):
            u = image_from_obj(v)
            if u:
                return u
        if isinstance(v, list):
            for x in v[:5]:
                if isinstance(x, str):
                    u = normalize_url(x)
                elif isinstance(x, dict):
                    u = image_from_obj(x)
                else:
                    u = ''
                if u:
                    return u
    for k, v in d.items():
        if not IMAGE_KEY_RE.search(str(k)):
            continue
        if isinstance(v, str):
            u = normalize_url(v)
            if u and not re.search(r"logo|icon|banner|badge|sprite|placeholder", u, re.I):
                return u
        elif isinstance(v, dict):
            u = image_from_obj(v)
            if u:
                return u
        elif isinstance(v, list):
            for x in v[:5]:
                if isinstance(x, dict):
                    u = image_from_obj(x)
                    if u:
                        return u
    return ''


def product_url_from_obj(d):
    v = first_value(d, URL_KEYS)
    if not isinstance(v, str):
        return ''
    if v.startswith('http'):
        return v
    if '/' in v:
        return urljoin('https://www.gemsny.com/', v.lstrip('/'))
    return ''


def product_candidate(d):
    if not isinstance(d, dict):
        return None
    image = image_from_obj(d)
    if not image:
        return None
    sku = str(first_value(d, ID_KEYS) or '').strip()
    title = str(first_value(d, TITLE_KEYS) or '').strip()
    purl = product_url_from_obj(d)
    if not sku and not purl:
        return None
    if re.search(r"logo|icon|banner|badge|sprite|placeholder|certificate", image, re.I):
        return None
    return {
        'sku': sku,
        'product_url': purl,
        'title': title,
        'card_text': title,
        'alt': title,
        'current_src': image,
        'declared_src': image,
        'natural_width': 0,
        'natural_height': 0,
        'card_family': 'NETWORK_JSON_PRODUCT'
    }


def network_candidates(obj):
    out = {}
    stack = [obj]
    seen_nodes = 0
    while stack and seen_nodes < 200000:
        x = stack.pop()
        seen_nodes += 1
        if isinstance(x, dict):
            c = product_candidate(x)
            if c:
                key = c['sku'] or c['product_url'] or c['declared_src']
                out.setdefault((key, c['declared_src']), c)
            for v in x.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(x, list):
            stack.extend(x)
    return list(out.values())


def measure_source(url):
    if not url:
        return (0, 0, 'empty source URL')
    try:
        with requests.get(url, headers={'User-Agent': UA, 'Referer': 'https://www.gemsny.com/'}, timeout=25, stream=True) as r:
            r.raise_for_status()
            with Image.open(r.raw) as im:
                return (int(im.width or 0), int(im.height or 0), '')
    except Exception as e:
        return (0, 0, f'{type(e).__name__}: {e}')


def exclusions(diamond_snapshot, exception_root, server_root):
    urls = set()
    snap = json.load(open(diamond_snapshot, encoding='utf-8'))
    for t in snap.get('targets', []):
        u = t.get('category_url') or t.get('url')
        if u:
            urls.add(u)
    for root in (exception_root, server_root):
        for p in Path(root).rglob('coverage.csv'):
            for r in read_csv(p):
                if r.get('status') == 'COMPLETE' and r.get('category_url'):
                    urls.add(r['category_url'])
    return urls


async def generic_dom_extract(page):
    items = await extract_product_images(page)
    if items:
        return items
    return await page.evaluate(r"""() => {
      const abs = u => { try { return new URL(u, location.href).href } catch(e) { return '' } };
      const bad = /logo|icon|sprite|banner|hero|payment|review|badge|flag|loader|placeholder|footer|header|social/i;
      const out=[], seen=new Set();
      for (const img of [...document.images]) {
        const raw=img.getAttribute('src')||img.getAttribute('data-src')||img.getAttribute('data-lazy-src')||img.currentSrc||'';
        const src=abs(raw); if(!src||bad.test(src)) continue;
        const card=img.closest('article,li,[class*="product" i],[class*="card" i],[class*="item" i],div');
        const a=img.closest('a[href]')||(card?card.querySelector('a[href]'):null); if(!a) continue;
        const u=abs(a.href||a.getAttribute('href')||''); if(!u||!u.includes(location.hostname)) continue;
        const text=((card||a).innerText||(card||a).textContent||'').trim().slice(0,1800);
        if(!/\$\s*[\d,.]+|SKU|Item\s*#|View\s*Details/i.test(text) && !/image-jewelry|gemstone|diamond|product|stone/i.test(src)) continue;
        const sm=(text+' '+(img.alt||'')).match(/(?:SKU|Item\s*#)\s*[:#-]?\s*([A-Za-z0-9-]{3,})/i);
        const am=src.match(/\/image-jewelry\/([^/]+)\//i);
        const sku=sm?sm[1]:(am?am[1]:'');
        const key=[sku,u,src].join('|'); if(seen.has(key)) continue; seen.add(key);
        out.push({sku,product_url:u,title:'',card_text:text,alt:img.alt||'',current_src:abs(img.currentSrc||src),declared_src:src,natural_width:img.naturalWidth||0,natural_height:img.naturalHeight||0,card_family:'GENERIC_PRODUCT_CARD'});
      }
      return out;
    }""")


async def visit_and_extract(page, u):
    json_payloads = []
    response_urls = []

    async def on_response(resp):
        try:
            ct = (resp.headers.get('content-type') or '').lower()
            host = urlparse(resp.url).hostname or ''
            if 'json' not in ct or not (host.endswith('gemsny.com') or host.endswith('gemsny.net')):
                return
            obj = await resp.json()
            cand = network_candidates(obj)
            if cand:
                json_payloads.append(cand)
                response_urls.append((resp.url, len(cand)))
        except Exception:
            return

    page.on('response', on_response)
    try:
        await page.goto(u, wait_until='domcontentloaded', timeout=90000)
        await page.wait_for_timeout(1200)
        await settle_and_scroll(page)
        await page.wait_for_timeout(1000)
        dom = await generic_dom_extract(page)
    finally:
        try:
            page.remove_listener('response', on_response)
        except Exception:
            pass

    if dom:
        return dom, 'DOM', response_urls
    if json_payloads:
        best = max(json_payloads, key=len)
        return best, 'NETWORK_JSON', response_urls
    return [], 'NONE', response_urls


async def recover_one(page, row, min_w, min_h):
    name = row.get('category_name') or row['category_url']
    base = row['category_url']
    if NON_PRODUCT_RE.search(urlparse(base).path):
        return [], {
            'category_name': name, 'category_url': base, 'status': 'NON_PRODUCT_PAGE',
            'pages_visited': 1, 'pagination_detected': False, 'expected_last_page': 1,
            'expected_products_if_detected': 0, 'unique_products_detected': 0,
            'images_checked': 0, 'failed_images': 0, 'image_errors': 0,
            'note': 'Excluded from product/category coverage: confirmed non-product informational/service URL.'
        }

    await page.goto(base, wait_until='domcontentloaded', timeout=90000)
    await page.wait_for_timeout(800)
    body = await page.evaluate("() => document.body?.innerText || ''")
    live = live_expected(body)
    baseline_expected = as_int(row.get('expected_products_if_detected'))
    expected = live if live is not None else baseline_expected
    baseline_pages = max(1, as_int(row.get('expected_last_page'), 1))
    total_pages = max(baseline_pages, math.ceil(expected / 24) if expected else 1)

    products = {}
    page_sets = []
    modes = set()
    response_samples = []

    for n in range(1, total_pages + 1):
        u = page_url(base, n)
        items, mode, responses = await visit_and_extract(page, u)
        modes.add(mode)
        response_samples.extend(responses[:3])
        keys = set()
        for item in items:
            alt = (item.get('alt') or '').strip()
            if alt and EXCLUDE_ALT_RE.search(alt):
                continue
            sku = (item.get('sku') or '').strip()
            product_url = item.get('product_url') or ''
            declared = item.get('declared_src') or item.get('current_src') or ''
            source = strip_transform(declared) or declared
            key = sku or product_url or source
            if not key or not source:
                continue
            keys.add(key)
            pkey = (key, source)
            products.setdefault(pkey, {
                'category_name': name, 'category_url': base, 'category_page_url': u,
                'page_number': n, 'sku': sku, 'product_key': key,
                'product_title': (item.get('title') or '').strip(), 'product_url': product_url,
                'image_alt': alt, 'card_family': item.get('card_family') or mode,
                'image_url': declared, 'original_url': source,
            })
        page_sets.append(keys)
        if n <= 2 or n % 10 == 0 or n == total_pages:
            print(f'{name}: page {n}/{total_pages} mode={mode} rows={len(keys)} cumulative={len(products)} responses={responses[:2]}', flush=True)
        if expected > 0 and n >= 2 and not products:
            raise RuntimeError(f'No product records from DOM or product JSON after {n} pages; expected={expected}; response_samples={response_samples[:8]}')

    unique_product_keys = {r['product_key'] for r in products.values() if r['product_key']}
    if expected > 0:
        if len(unique_product_keys) < expected:
            raise RuntimeError(f'Product coverage shortfall expected={expected} unique={len(unique_product_keys)} pages={total_pages} modes={sorted(modes)} response_samples={response_samples[:8]}')
        nonempty = [frozenset(s) for s in page_sets if s]
        if total_pages > 2 and len(nonempty) > 1 and len(set(nonempty)) < 2:
            raise RuntimeError('Pagination repeated the same product set; refusing false completeness')
    elif not products and live != 0:
        raise RuntimeError(f'No product records and no explicit live zero-product count; modes={sorted(modes)}')

    sources = sorted({r['original_url'] for r in products.values() if r['original_url']})
    dims = {}
    with ThreadPoolExecutor(max_workers=24) as ex:
        fut = {ex.submit(measure_source, u): u for u in sources}
        for i, f in enumerate(as_completed(fut), 1):
            u = fut[f]
            try:
                dims[u] = f.result()
            except Exception as e:
                dims[u] = (0, 0, repr(e))
            if i % 500 == 0 or i == len(sources):
                print(f'{name}: measured {i}/{len(sources)}', flush=True)

    out = []
    for r in products.values():
        w, h, err = dims.get(r['original_url'], (0, 0, 'not measured'))
        status, reason = size_status(w, h, min_w, min_h)
        if err:
            status, reason = 'ERROR', err
        x = dict(r)
        x.update({'original_width': w, 'original_height': h, 'original_status': status,
                  'threshold_width': min_w, 'threshold_height': min_h, 'status': status,
                  'compliance_basis': 'ORIGINAL SOURCE', 'compliance_width': w,
                  'compliance_height': h, 'failure_reason': reason,
                  'audit_mode': 'REMAINING_NETWORK_BACKED_RECOVERY',
                  'audited_at': datetime.now().isoformat(timespec='seconds')})
        out.append(x)

    cov = {
        'category_name': name, 'category_url': base, 'status': 'COMPLETE',
        'pages_visited': total_pages, 'pagination_detected': total_pages > 1,
        'expected_last_page': total_pages, 'expected_products_if_detected': expected,
        'unique_products_detected': len(unique_product_keys), 'images_checked': len(out),
        'failed_images': sum(r['status'] == 'FAIL' for r in out),
        'image_errors': sum(r['status'] == 'ERROR' for r in out),
        'note': f'Strict forced-page recovery using {sorted(modes)}; product-count coverage validated.'
    }
    return out, cov


async def main(a):
    base = read_csv(a.baseline_coverage)
    done = exclusions(a.diamond_snapshot, a.exception_root, a.server_root)
    targets = [r for r in base if r.get('status') != 'COMPLETE' and r.get('category_url') not in done]
    targets = sorted(targets, key=lambda r: r['category_url'])
    mine = [r for i, r in enumerate(targets) if i % a.shard_count == a.shard_index]
    print(json.dumps({'baseline_noncomplete': sum(r.get('status') != 'COMPLETE' for r in base), 'already_recovered': len(done), 'remaining_targets': len(targets), 'shard_index': a.shard_index, 'shard_count': a.shard_count, 'mine': len(mine)}, indent=2), flush=True)

    out = Path(a.output_dir); out.mkdir(parents=True, exist_ok=True)
    all_rows, cov, failures = [], [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={'width': 1440, 'height': 1200}, user_agent=UA)
        page = await ctx.new_page()
        for i, r in enumerate(mine, 1):
            try:
                rows, c = await recover_one(page, r, a.min_width, a.min_height)
                all_rows.extend(rows); cov.append(c)
                print(f'[{i}/{len(mine)}] {c["status"]} {c["category_name"]} products={c["unique_products_detected"]} images={c["images_checked"]}', flush=True)
            except Exception as e:
                c = dict(r); c['status'] = 'RECOVERY_FAILED'; c['note'] = f'{type(e).__name__}: {e}'
                cov.append(c); failures.append({'category_url': r['category_url'], 'error': c['note']})
                print(f'[{i}/{len(mine)}] FAILED {r["category_url"]}: {e}', flush=True)
        await ctx.close(); await browser.close()

    fields = ['category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_alt','card_family','image_url','original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis','compliance_width','compliance_height','failure_reason','audit_mode','audited_at']
    with open(out/'all_images.csv','w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(all_rows)
    cov_fields=[]
    for r in cov:
        for k in r:
            if k not in cov_fields: cov_fields.append(k)
    with open(out/'coverage.csv','w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cov_fields,extrasaction='ignore'); w.writeheader(); w.writerows(cov)
    (out/'shard_summary.json').write_text(json.dumps({'target_total':len(targets),'shard_index':a.shard_index,'shard_count':a.shard_count,'assigned':len(mine),'complete':sum(r.get('status')=='COMPLETE' for r in cov),'non_product':sum(r.get('status')=='NON_PRODUCT_PAGE' for r in cov),'failed':failures,'images':len(all_rows)},indent=2),encoding='utf-8')
    if failures:
        raise SystemExit(f'{len(failures)} categories failed in shard {a.shard_index}')


if __name__ == '__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--baseline-coverage',required=True); ap.add_argument('--diamond-snapshot',required=True)
    ap.add_argument('--exception-root',required=True); ap.add_argument('--server-root',required=True)
    ap.add_argument('--output-dir',required=True); ap.add_argument('--shard-index',type=int,required=True)
    ap.add_argument('--shard-count',type=int,required=True); ap.add_argument('--min-width',type=int,default=500)
    ap.add_argument('--min-height',type=int,default=500)
    asyncio.run(main(ap.parse_args()))