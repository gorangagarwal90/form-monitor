import argparse, asyncio, csv, json, math, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from PIL import Image
from playwright.async_api import async_playwright

from audit import extract_product_images, strip_transform, EXCLUDE_ALT_RE, settle_and_scroll, size_status

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"


def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def page_url(base, n):
    p=urlparse(base); q=dict(parse_qsl(p.query,keep_blank_values=True)); q['page']=str(n)
    return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),''))


def as_int(v, default=0):
    try: return int(float(str(v).replace(',','').strip()))
    except: return default


def live_expected(body):
    for pat in (r"Result\s*\(\s*([\d,]+)\s*\)",r"([\d,]+)\s+results\b",r"([\d,]+)\s+products\b",r"([\d,]+)\s+items\b"):
        m=re.search(pat,body,re.I)
        if m:
            return int(m.group(1).replace(',',''))
    return None


def measure_source(url):
    if not url: return (0,0,'empty source URL')
    try:
        with requests.get(url,headers={'User-Agent':UA,'Referer':'https://www.gemsny.com/'},timeout=25,stream=True) as r:
            r.raise_for_status()
            with Image.open(r.raw) as im: return (int(im.width or 0),int(im.height or 0),'')
    except Exception as e: return (0,0,f'{type(e).__name__}: {e}')


def exclusions(diamond_snapshot, exception_root, server_root):
    urls=set()
    snap=json.load(open(diamond_snapshot,encoding='utf-8'))
    for t in snap.get('targets',[]):
        u=t.get('category_url') or t.get('url')
        if u: urls.add(u)
    for root in (exception_root,server_root):
        for p in Path(root).rglob('coverage.csv'):
            for r in read_csv(p):
                if r.get('status')=='COMPLETE' and r.get('category_url'): urls.add(r['category_url'])
    return urls


async def recover_one(page, row, min_w, min_h):
    name=row.get('category_name') or row['category_url']; base=row['category_url']
    await page.goto(base,wait_until='domcontentloaded',timeout=90000); await page.wait_for_timeout(1200)
    body=await page.evaluate("() => document.body?.innerText || ''")
    live=live_expected(body)
    baseline_expected=as_int(row.get('expected_products_if_detected'))
    expected=live if live is not None else baseline_expected
    baseline_pages=max(1,as_int(row.get('expected_last_page'),1))
    # Preserve the site's own detected page count. If a live count exists, also cover enough 24-card pages.
    total_pages=max(baseline_pages, math.ceil(expected/24) if expected else 1)
    products={}; page_sets=[]; pages_with_rows=0
    for n in range(1,total_pages+1):
        u=page_url(base,n)
        await page.goto(u,wait_until='domcontentloaded',timeout=90000); await page.wait_for_timeout(500)
        await settle_and_scroll(page)
        items=await extract_product_images(page)
        keys=set()
        for item in items:
            alt=(item.get('alt') or '').strip()
            if alt and EXCLUDE_ALT_RE.search(alt): continue
            sku=(item.get('sku') or '').strip(); product_url=item.get('product_url') or ''
            declared=item.get('declared_src') or item.get('current_src') or ''
            source=strip_transform(declared) or declared
            key=sku or product_url or source
            if not key or not source: continue
            keys.add(key)
            # Product+source preserves distinct source images while avoiding duplicate hydrated cards.
            pkey=(key,source)
            products.setdefault(pkey,{
                'category_name':name,'category_url':base,'category_page_url':u,'page_number':n,
                'sku':sku,'product_key':key,'product_title':(item.get('title') or '').strip(),
                'product_url':product_url,'image_alt':alt,'card_family':item.get('card_family') or '',
                'image_url':declared,'original_url':source,
            })
        if keys: pages_with_rows+=1
        page_sets.append(keys)
        if n%10==0 or n==total_pages: print(f'{name}: page {n}/{total_pages} rows={len(keys)} cumulative={len(products)}',flush=True)

    unique_product_keys={r['product_key'] for r in products.values() if r['product_key']}
    # Strict page coverage first. Product count is checked when the live/baseline count is meaningful.
    if expected>0:
        if not products: raise RuntimeError(f'No product images extracted although expected product count is {expected}')
        if len(unique_product_keys)<expected:
            raise RuntimeError(f'Product coverage shortfall expected={expected} unique_product_keys={len(unique_product_keys)} pages={total_pages}')
        # Refuse a page stream that is obviously repeating while inventory still claims multiple pages.
        nonempty=[frozenset(s) for s in page_sets if s]
        if total_pages>2 and len(set(nonempty))<min(2,len(nonempty)):
            raise RuntimeError('Pagination repeated the same product set; refusing false completeness')
    elif not products:
        # Only accept a zero-product category when the live page explicitly reports zero inventory.
        if live != 0:
            raise RuntimeError('No product images and no explicit live zero-product count')

    sources=sorted({r['original_url'] for r in products.values() if r['original_url']}); dims={}
    with ThreadPoolExecutor(max_workers=24) as ex:
        fut={ex.submit(measure_source,u):u for u in sources}
        for i,f in enumerate(as_completed(fut),1):
            u=fut[f]
            try: dims[u]=f.result()
            except Exception as e: dims[u]=(0,0,repr(e))
            if i%500==0 or i==len(sources): print(f'{name}: measured {i}/{len(sources)}',flush=True)
    out=[]
    for r in products.values():
        w,h,err=dims.get(r['original_url'],(0,0,'not measured')); status,reason=size_status(w,h,min_w,min_h)
        if err: status='ERROR'; reason=err
        x=dict(r); x.update({'original_width':w,'original_height':h,'original_status':status,'threshold_width':min_w,'threshold_height':min_h,'status':status,'compliance_basis':'ORIGINAL SOURCE','compliance_width':w,'compliance_height':h,'failure_reason':reason,'audit_mode':'REMAINING_FORCED_PAGE_RECOVERY','audited_at':datetime.now().isoformat(timespec='seconds')}); out.append(x)
    cov={'category_name':name,'category_url':base,'status':'COMPLETE','pages_visited':total_pages,'pagination_detected':total_pages>1,'expected_last_page':total_pages,'expected_products_if_detected':expected,'unique_products_detected':len(unique_product_keys),'images_checked':len(out),'failed_images':sum(r['status']=='FAIL' for r in out),'image_errors':sum(r['status']=='ERROR' for r in out),'note':'Strict forced-page recovery; all computed pages visited and product-count coverage validated.' if products else 'Strict live verification: category explicitly reports zero products.'}
    return out,cov


async def main(a):
    base=read_csv(a.baseline_coverage); done=exclusions(a.diamond_snapshot,a.exception_root,a.server_root)
    targets=[r for r in base if r.get('status')!='COMPLETE' and r.get('category_url') not in done]
    targets=sorted(targets,key=lambda r:r['category_url'])
    mine=[r for i,r in enumerate(targets) if i%a.shard_count==a.shard_index]
    print(json.dumps({'baseline_noncomplete':sum(r.get('status')!='COMPLETE' for r in base),'already_recovered':len(done),'remaining_targets':len(targets),'shard_index':a.shard_index,'shard_count':a.shard_count,'mine':len(mine)},indent=2),flush=True)
    out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True); all_rows=[]; cov=[]; failures=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True); ctx=await browser.new_context(viewport={'width':1440,'height':1200},user_agent=UA); page=await ctx.new_page()
        for i,r in enumerate(mine,1):
            try:
                rows,c=await recover_one(page,r,a.min_width,a.min_height); all_rows.extend(rows); cov.append(c)
                print(f'[{i}/{len(mine)}] COMPLETE {c["category_name"]} products={c["unique_products_detected"]} images={c["images_checked"]}',flush=True)
            except Exception as e:
                c=dict(r); c['status']='RECOVERY_FAILED'; c['note']=f'{type(e).__name__}: {e}'; cov.append(c); failures.append({'category_url':r['category_url'],'error':c['note']}); print(f'[{i}/{len(mine)}] FAILED {r["category_url"]}: {e}',flush=True)
        await ctx.close(); await browser.close()
    fields=['category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_alt','card_family','image_url','original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis','compliance_width','compliance_height','failure_reason','audit_mode','audited_at']
    with open(out/'all_images.csv','w',encoding='utf-8-sig',newline='') as f: w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(all_rows)
    cov_fields=[]
    for r in cov:
        for k in r:
            if k not in cov_fields: cov_fields.append(k)
    with open(out/'coverage.csv','w',encoding='utf-8-sig',newline='') as f: w=csv.DictWriter(f,fieldnames=cov_fields,extrasaction='ignore'); w.writeheader(); w.writerows(cov)
    (out/'shard_summary.json').write_text(json.dumps({'target_total':len(targets),'shard_index':a.shard_index,'shard_count':a.shard_count,'assigned':len(mine),'complete':sum(r.get('status')=='COMPLETE' for r in cov),'failed':failures,'images':len(all_rows)},indent=2),encoding='utf-8')
    # Keep successful rows as artifacts even when a subset fails; merger/recovery can target only failures.
    if failures: raise SystemExit(f'{len(failures)} categories failed in shard {a.shard_index}')

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--baseline-coverage',required=True); ap.add_argument('--diamond-snapshot',required=True); ap.add_argument('--exception-root',required=True); ap.add_argument('--server-root',required=True); ap.add_argument('--output-dir',required=True); ap.add_argument('--shard-index',type=int,required=True); ap.add_argument('--shard-count',type=int,required=True); ap.add_argument('--min-width',type=int,default=500); ap.add_argument('--min-height',type=int,default=500); asyncio.run(main(ap.parse_args()))
