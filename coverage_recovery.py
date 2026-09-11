import asyncio
import argparse
import csv
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.async_api import async_playwright
import audit

ORIGINAL_EXTRACT = audit.extract_product_images


def page_url(base, n):
    if n <= 1:
        return base
    p = urlparse(base)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q['page'] = str(n)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), ''))


async def enhanced_extract(page):
    base = await ORIGINAL_EXTRACT(page)
    extra = await page.evaluate(r'''() => {
      const abs = u => { try { return new URL(u, location.href).href } catch(e) { return '' } };
      const out=[];
      for (const card of [...document.querySelectorAll('.GridProductInner, [class*="GridProductInner"]')]) {
        const img = card.querySelector('.ProductItemImg img, [class*="ProductItemImg"] img, img');
        if (!img) continue;
        const text=(card.innerText||'').trim();
        const m=text.match(/Item#\s*([A-Za-z0-9-]+)/i);
        const sku=m?m[1]:'';
        const raw=img.getAttribute('src')||img.getAttribute('data-src')||img.currentSrc||'';
        const declared=abs(raw);
        const current=abs(img.currentSrc||declared);
        if (!declared && !current) continue;
        let anchor=card.closest('a[href]')||card.querySelector('a[href]');
        const productUrl=anchor?abs(anchor.href):'';
        out.push({
          sku,
          product_url: productUrl,
          title:(img.getAttribute('alt')||'').trim(),
          card_text:text.slice(0,1400),
          alt:(img.getAttribute('alt')||'').trim(),
          current_src:current,
          declared_src:declared||current,
          natural_width:img.naturalWidth||0,
          natural_height:img.naturalHeight||0,
          dom_width:Number(img.getAttribute('width')||0)||0,
          dom_height:Number(img.getAttribute('height')||0)||0,
          card_family:'DIAMOND_CARD'
        });
      }
      return out;
    }''')
    seen=set(); out=[]
    for item in list(base)+list(extra):
        key=(item.get('sku') or item.get('product_url') or '', item.get('declared_src') or item.get('current_src') or '')
        if key in seen: continue
        seen.add(key); out.append(item)
    return out


audit.extract_product_images = enhanced_extract


def read_targets(path):
    rows=[]
    with open(path, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            if r.get('status') != 'COMPLETE':
                rows.append(r)
    return rows


def write_csv(path, fields, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w=csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k:r.get(k,'') for k in fields})


async def recover(args):
    out=Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    targets=read_targets(args.coverage_file)
    targets=[r for i,r in enumerate(targets) if i % args.shard_count == args.shard_index]
    all_rows=[]; coverage=[]

    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(
            viewport={'width':1440,'height':1200},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
        )
        page=await ctx.new_page()

        for ti,t in enumerate(targets,1):
            name=t.get('category_name') or ''
            url=t.get('category_url') or ''
            expected=0; pagination=False; rows=[]; product_keys=set(); fingerprints=set()
            pages=0; note=''; status='COMPLETE'
            n=1
            try:
                while True:
                    u=page_url(url,n)
                    await page.goto(u, wait_until='domcontentloaded', timeout=90000)
                    await page.wait_for_timeout(1200)
                    exp,current,last,next_url,has_pagination=await audit.page_meta(page)
                    expected=max(expected, int(exp or 0))
                    pagination = pagination or bool(has_pagination)
                    found=await audit.grab(page,name,url,n,args.min_width,args.min_height,True)
                    pages += 1

                    fp=tuple(sorted((r.get('product_key') or '') for r in found if r.get('product_key')))
                    if fp and fp in fingerprints:
                        note=f'Repeated product page detected at page {n}; stopped safely'
                        break
                    if fp: fingerprints.add(fp)

                    added=0
                    existing={(r.get('product_key',''),r.get('image_url','')) for r in rows}
                    for r in found:
                        k=(r.get('product_key',''),r.get('image_url',''))
                        if k in existing: continue
                        existing.add(k); rows.append(r); added += 1
                        if r.get('product_key'): product_keys.add(r['product_key'])

                    print(f'RECOVERY shard={args.shard_index} target={ti}/{len(targets)} page={n} name={name} new_images={added} unique_products={len(product_keys)} expected={expected}', flush=True)

                    if expected and len(product_keys) >= expected:
                        note=f'Detected {len(product_keys)} products; expected {expected}'
                        break
                    if not found:
                        if n == 1 and expected == 0:
                            note='Live recheck found no product grid/count; documented as non-product hub, zero-inventory category, or retired URL'
                        else:
                            note=f'No new product images on page {n}; crawl exhausted'
                        break
                    if n == 1 and not has_pagination and expected == 0:
                        note='Single-page category rechecked completely'
                        break

                    n += 1
                    await asyncio.sleep(args.delay)

                if expected and len(product_keys) < expected * 0.95:
                    status='INCOMPLETE - PRODUCT COUNT'
                    note=(note+'; ' if note else '')+f'expected {expected}, detected {len(product_keys)}'
                elif not rows and expected > 0:
                    status='INCOMPLETE - NO PRODUCT IMAGES'
                else:
                    status='COMPLETE'
            except Exception as exc:
                status='ERROR'; note=str(exc)

            all_rows.extend(rows)
            coverage.append({
                'category_name':name,
                'category_url':url,
                'status':status,
                'pages_visited':pages,
                'pagination_detected':pagination,
                'expected_last_page':'',
                'expected_products_if_detected':expected,
                'unique_products_detected':len(product_keys),
                'images_checked':len(rows),
                'failed_images':sum(1 for r in rows if r.get('status')=='FAIL'),
                'image_errors':sum(1 for r in rows if r.get('status')=='ERROR'),
                'note':note,
            })
            await asyncio.sleep(args.delay)

        await browser.close()

    img_fields=[
        'category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_alt','card_family',
        'image_url','current_src_url','served_width','served_height','served_status','declared_src_width','declared_src_height','declared_src_status',
        'original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis',
        'compliance_width','compliance_height','failure_reason','audit_mode','audited_at'
    ]
    cov_fields=['category_name','category_url','status','pages_visited','pagination_detected','expected_last_page','expected_products_if_detected','unique_products_detected','images_checked','failed_images','image_errors','note']
    write_csv(out/'all_images.csv',img_fields,all_rows)
    write_csv(out/'coverage.csv',cov_fields,coverage)
    write_csv(out/'failed_images.csv',img_fields,[r for r in all_rows if r.get('status') in ('FAIL','ERROR')])


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--coverage-file',required=True)
    ap.add_argument('--output-dir',required=True)
    ap.add_argument('--shard-index',type=int,required=True)
    ap.add_argument('--shard-count',type=int,default=16)
    ap.add_argument('--min-width',type=int,default=500)
    ap.add_argument('--min-height',type=int,default=500)
    ap.add_argument('--delay',type=float,default=1.0)
    asyncio.run(recover(ap.parse_args()))
