import argparse, asyncio, csv, hashlib, io, json, math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from PIL import Image
from playwright.async_api import async_playwright

IMG_FIELDS = [
    'category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_alt','card_family',
    'image_url','current_src_url','served_width','served_height','served_status','declared_src_width','declared_src_height','declared_src_status',
    'original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis',
    'compliance_width','compliance_height','failure_reason','audit_mode','audited_at'
]
COV_FIELDS = ['category_name','category_url','status','pages_visited','pagination_detected','expected_last_page','expected_products_if_detected','unique_products_detected','images_checked','failed_images','image_errors','note']


def read_targets(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def normalize_api(url):
    p=urlparse(url)
    q=[(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k not in {'page','pageSize'}]
    q.sort()
    return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),''))


def with_page(base, page, page_size):
    p=urlparse(base); q=dict(parse_qsl(p.query, keep_blank_values=True)); q['page']=str(page); q['pageSize']=str(page_size)
    return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),'') )


def count_url(base):
    p=urlparse(base); q=[(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k not in {'page','pageSize'}]
    path=p.path.replace('/diamond/diamonds','/diamond/diamonds/count')
    return urlunparse((p.scheme,p.netloc,path,p.params,urlencode(q),'') )


async def capture_api(page, url):
    found=[]
    def on_req(req):
        u=req.url
        if 'storebe.gemsny.com/diamond/diamonds?' in u and '/count?' not in u:
            found.append(u)
    page.on('request', on_req)
    await page.goto(url, wait_until='domcontentloaded', timeout=90000)
    await page.wait_for_timeout(5000)
    try:
        perf=await page.evaluate("() => performance.getEntriesByType('resource').map(x=>x.name)")
        for u in perf:
            if 'storebe.gemsny.com/diamond/diamonds?' in u and '/count?' not in u:
                found.append(u)
    except Exception:
        pass
    try: page.remove_listener('request', on_req)
    except Exception: pass
    if not found:
        return ''
    for u in reversed(found):
        if 'pageSize=' in u:
            return normalize_api(u)
    return normalize_api(found[-1])


async def api_json(request, url, retries=4):
    last=''
    for n in range(retries):
        try:
            r=await request.get(url, timeout=90000)
            if r.status == 200:
                return await r.json()
            last=f'HTTP {r.status}'
        except Exception as e:
            last=str(e)
        await asyncio.sleep(1.5*(n+1))
    raise RuntimeError(f'API failed: {url}: {last}')


def compact_item(item):
    d=item.get('details') or {}
    iid=str(item.get('id') or d.get('sku') or '')
    if not iid:
        return '', None
    return iid, {
        'id':iid,
        'title':item.get('title') or '',
        'redirect_url':item.get('redirect_url') or d.get('url') or '',
        'productImage':d.get('productImage') or item.get('productImage') or '',
        'stillImage':d.get('stillImage') or item.get('stillImage') or '',
    }


async def prep(args):
    out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    targets=read_targets(args.targets)
    snapshot={'created_at':datetime.now(timezone.utc).isoformat(),'targets':[],'groups':{}}
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(viewport={'width':1440,'height':1200}, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36')
        page=await ctx.new_page()
        group_targets=defaultdict(list)
        for i,t in enumerate(targets,1):
            api=await capture_api(page,t['category_url'])
            if not api:
                raise RuntimeError(f'No diamond API request captured for {t["category_url"]}')
            key=hashlib.sha1(api.encode()).hexdigest()[:16]
            group_targets[key].append(t)
            snapshot['targets'].append({**t,'group_key':key,'api_base':api})
            print(f'CAPTURE {i}/{len(targets)} group={key} {t["category_url"]}', flush=True)

        for gi,(key,members) in enumerate(group_targets.items(),1):
            api=next(x['api_base'] for x in snapshot['targets'] if x['group_key']==key)
            page_size=args.page_size
            item_map={}
            observed_counts=[]
            reconciled=False
            max_passes=4

            # The GemsNY diamond inventory is live and can change while hundreds of API
            # pages are being enumerated. A single pass can therefore contain a few
            # duplicate boundary items even when every request succeeds. Reconcile with
            # repeated full passes and union product IDs until the ending live count is
            # fully represented. This prevents a moving inventory from creating a false
            # incomplete audit while still refusing unexplained missing products.
            for pass_no in range(1,max_passes+1):
                c=await api_json(ctx.request,count_url(api))
                start_count=int(c.get('totalCount') or c.get('count') or 0)
                if start_count <= 0:
                    raise RuntimeError(f'Zero/invalid API count for group {key}: {api}: {c}')
                observed_counts.append(start_count)
                pages=math.ceil(start_count/page_size)
                before=len(item_map)

                for n in range(1,pages+1):
                    data=await api_json(ctx.request,with_page(api,n,page_size))
                    batch=data.get('items') or []
                    if not batch:
                        raise RuntimeError(f'Empty API batch group={key} pass={pass_no} page={n}/{pages}, count={start_count}')
                    for item in batch:
                        iid, compact=compact_item(item)
                        if iid:
                            item_map[iid]=compact
                    if n==1 or n%25==0 or n==pages:
                        print(f'SNAPSHOT group={gi}/{len(group_targets)} key={key} pass={pass_no}/{max_passes} page={n}/{pages} unique={len(item_map)} start_count={start_count}',flush=True)

                c2=await api_json(ctx.request,count_url(api))
                end_count=int(c2.get('totalCount') or c2.get('count') or 0)
                observed_counts.append(end_count)
                gained=len(item_map)-before
                print(f'RECONCILE group={key} pass={pass_no} start_count={start_count} end_count={end_count} unique={len(item_map)} gained={gained}',flush=True)

                if end_count > 0 and len(item_map) >= end_count:
                    reconciled=True
                    break

            if not reconciled:
                final_count=observed_counts[-1] if observed_counts else 0
                raise RuntimeError(
                    f'Snapshot count mismatch after {max_passes} reconciliation passes group={key}: '
                    f'latest API count={final_count}, unique items={len(item_map)}, observed_counts={observed_counts}'
                )

            items=list(item_map.values())
            # Snapshot count is intentionally the exact immutable SKU union captured and
            # audited from this point onward. It may exceed the final live count by a tiny
            # amount if products were removed during enumeration, but it cannot omit any
            # SKU required by the final count once reconciliation succeeds.
            snapshot_count=len(items)
            snapshot['groups'][key]={
                'api_base':api,
                'count':snapshot_count,
                'items':items,
                'target_urls':[m['category_url'] for m in members],
                'observed_api_counts':observed_counts,
                'inventory_drift':snapshot_count-(observed_counts[-1] if observed_counts else snapshot_count),
            }
        await browser.close()
    (out/'diamond_snapshot.json').write_text(json.dumps(snapshot,separators=(',',':')),encoding='utf-8')
    summary={'targets':len(snapshot['targets']),'distinct_inventory_groups':len(snapshot['groups']),'group_counts':{k:v['count'] for k,v in snapshot['groups'].items()},'total_group_items':sum(v['count'] for v in snapshot['groups'].values()),'inventory_drift':{k:v.get('inventory_drift',0) for k,v in snapshot['groups'].items()}}
    (out/'snapshot_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)


def status_for(w,h,min_w,min_h):
    if not w or not h: return 'ERROR','Image dimensions unavailable'
    bad=[]
    if w<min_w: bad.append(f'width {w} < {min_w}')
    if h<min_h: bad.append(f'height {h} < {min_h}')
    if bad:return 'FAIL','; '.join(bad)
    if w<1000 or h<1000:return 'PASS - LOW RES','Meets minimum but is below 1000x1000 preferred'
    return 'PASS',''


async def image_size(client,url,sem):
    if not url:return 0,0,'missing image URL'
    async with sem:
        last=''
        for n in range(3):
            try:
                r=await client.get(url,follow_redirects=True)
                if r.status_code==200:
                    try:
                        im=Image.open(io.BytesIO(r.content)); return int(im.width),int(im.height),''
                    except Exception as e:
                        return 0,0,f'decode: {e}'
                last=f'HTTP {r.status_code}'
            except Exception as e:last=str(e)
            await asyncio.sleep(0.8*(n+1))
        return 0,0,last


def shard_of(value,n):
    return int(hashlib.sha1(value.encode()).hexdigest()[:12],16)%n

async def audit_shard(args):
    out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    snap=json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    group_to_targets=defaultdict(list)
    for t in snap['targets']:group_to_targets[t['group_key']].append(t)
    rows=[]; partial=[]; cache={}; sem=asyncio.Semaphore(args.concurrency)
    limits=httpx.Limits(max_connections=args.concurrency,max_keepalive_connections=args.concurrency)
    timeout=httpx.Timeout(35.0,connect=20.0)
    async with httpx.AsyncClient(limits=limits,timeout=timeout,headers={'User-Agent':'Mozilla/5.0'}) as client:
        for gi,(key,g) in enumerate(snap['groups'].items(),1):
            selected=[it for it in g['items'] if shard_of(it['id'],args.shard_count)==args.shard_index]
            urls=[]
            for it in selected:
                u=it.get('productImage') or it.get('stillImage') or ''
                if u and u not in cache: urls.append(u)
            unique_urls=list(dict.fromkeys(urls))
            for start in range(0,len(unique_urls),args.concurrency*4):
                batch=unique_urls[start:start+args.concurrency*4]
                vals=await asyncio.gather(*(image_size(client,u,sem) for u in batch))
                cache.update(dict(zip(batch,vals)))
                if start==0 or (start//max(1,args.concurrency*4))%10==0:
                    print(f'IMAGE shard={args.shard_index}/{args.shard_count} group={gi}/{len(snap["groups"])} checked={min(start+len(batch),len(unique_urls))}/{len(unique_urls)}',flush=True)
            now=datetime.now().isoformat(timespec='seconds')
            for t in group_to_targets[key]:
                count=0; failures=0; errors=0
                for it in selected:
                    img=it.get('productImage') or it.get('stillImage') or ''
                    w,h,err=cache.get(img,(0,0,'missing'))
                    st,reason=status_for(w,h,args.min_width,args.min_height)
                    if err and st=='ERROR': reason=(reason+'; '+err).strip('; ')
                    if st=='FAIL': failures+=1
                    if st=='ERROR': errors+=1
                    product_url=it.get('redirect_url') or ''
                    if product_url and not product_url.startswith('http'): product_url='https://www.gemsny.com/'+product_url.lstrip('/')
                    rows.append({
                        'category_name':t['category_name'],'category_url':t['category_url'],'category_page_url':t['category_url'],'page_number':'API',
                        'sku':it['id'],'product_key':it['id'],'product_title':it.get('title',''),'product_url':product_url,'image_alt':'','card_family':'DIAMOND_API',
                        'image_url':img,'current_src_url':'','served_width':'','served_height':'','served_status':'NOT APPLICABLE',
                        'declared_src_width':w,'declared_src_height':h,'declared_src_status':st,'original_url':img,'original_width':w,'original_height':h,'original_status':st,
                        'threshold_width':args.min_width,'threshold_height':args.min_height,'status':st,'compliance_basis':'API PRODUCT SOURCE',
                        'compliance_width':w,'compliance_height':h,'failure_reason':reason,'audit_mode':'DIAMOND_API','audited_at':now
                    }); count+=1
                partial.append({'category_name':t['category_name'],'category_url':t['category_url'],'status':'PARTIAL - HASH SHARD','pages_visited':'API','pagination_detected':'API','expected_last_page':'API','expected_products_if_detected':g['count'],'unique_products_detected':count,'images_checked':count,'failed_images':failures,'image_errors':errors,'note':f'Inventory snapshot group {key}; hash shard {args.shard_index}/{args.shard_count}'})
            print(f'GROUP DONE shard={args.shard_index} key={key} selected={len(selected)} source_cache={len(cache)}',flush=True)
    write_csv(out/'all_images.csv',IMG_FIELDS,rows)
    write_csv(out/'coverage.csv',COV_FIELDS,partial)
    write_csv(out/'failed_images.csv',IMG_FIELDS,[r for r in rows if r['status'] in ('FAIL','ERROR')])
    (out/'shard_info.json').write_text(json.dumps({'shard_index':args.shard_index,'shard_count':args.shard_count,'rows':len(rows),'unique_sources_measured':len(cache),'failures':sum(r['status']=='FAIL' for r in rows),'errors':sum(r['status']=='ERROR' for r in rows)},indent=2),encoding='utf-8')


def write_csv(path,fields,rows):
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows([{k:r.get(k,'') for k in fields} for r in rows])


def merge(args):
    root=Path(args.input_dir); out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    snap=json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    img=[]; shard_ids=[]
    for p in root.rglob('shard_info.json'):
        d=json.loads(p.read_text()); shard_ids.append(int(d['shard_index']))
    missing=sorted(set(range(args.shard_count))-set(shard_ids))
    if missing: raise SystemExit(f'Missing diamond recovery shards: {missing}')
    for p in root.rglob('all_images.csv'):
        with open(p,encoding='utf-8-sig',newline='') as f: img.extend(csv.DictReader(f))
    ded={}
    for r in img: ded[(r['category_url'],r['product_key'])]=r
    img=list(ded.values())
    bycat=defaultdict(list)
    for r in img: bycat[r['category_url']].append(r)
    cov=[]; unresolved=[]
    for t in snap['targets']:
        expected=int(snap['groups'][t['group_key']]['count'])
        rr=bycat.get(t['category_url'],[]); unique={r['product_key'] for r in rr if r['product_key']}
        status='COMPLETE' if len(unique)==expected else 'INCOMPLETE - PRODUCT COUNT'
        if status!='COMPLETE': unresolved.append({'url':t['category_url'],'expected':expected,'detected':len(unique)})
        cov.append({'category_name':t['category_name'],'category_url':t['category_url'],'status':status,'pages_visited':'API SNAPSHOT','pagination_detected':'API','expected_last_page':'API','expected_products_if_detected':expected,'unique_products_detected':len(unique),'images_checked':len(rr),'failed_images':sum(r['status']=='FAIL' for r in rr),'image_errors':sum(r['status']=='ERROR' for r in rr),'note':f'Exact backend inventory snapshot; group={t["group_key"]}'})
    write_csv(out/'all_images.csv',IMG_FIELDS,img)
    write_csv(out/'coverage.csv',COV_FIELDS,cov)
    write_csv(out/'failed_images.csv',IMG_FIELDS,[r for r in img if r['status'] in ('FAIL','ERROR')])
    summary={'expected_shards':args.shard_count,'completed_shards':sorted(shard_ids),'targets':len(snap['targets']),'complete_categories':sum(r['status']=='COMPLETE' for r in cov),'unresolved':unresolved,'image_rows':len(img),'unique_skus':len({r['product_key'] for r in img}),'unique_source_urls':len({r['original_url'] for r in img if r['original_url']}),'failures':sum(r['status']=='FAIL' for r in img),'errors':sum(r['status']=='ERROR' for r in img)}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)
    if unresolved: raise SystemExit(f'Diamond API recovery incomplete for {len(unresolved)} categories')


def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='mode',required=True)
    p=sub.add_parser('prep'); p.add_argument('--targets',required=True); p.add_argument('--output-dir',required=True); p.add_argument('--page-size',type=int,default=500)
    a=sub.add_parser('audit'); a.add_argument('--snapshot',required=True); a.add_argument('--output-dir',required=True); a.add_argument('--shard-index',type=int,required=True); a.add_argument('--shard-count',type=int,default=16); a.add_argument('--concurrency',type=int,default=24); a.add_argument('--min-width',type=int,default=500); a.add_argument('--min-height',type=int,default=500)
    m=sub.add_parser('merge'); m.add_argument('--snapshot',required=True); m.add_argument('--input-dir',required=True); m.add_argument('--output-dir',required=True); m.add_argument('--shard-count',type=int,default=16)
    args=ap.parse_args()
    if args.mode=='prep': asyncio.run(prep(args))
    elif args.mode=='audit': asyncio.run(audit_shard(args))
    else: merge(args)
if __name__=='__main__': main()