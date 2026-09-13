import argparse,csv,json,re,time,io,sys
from pathlib import Path
from urllib.parse import urlparse,parse_qsl,urlencode,urlunparse,urljoin
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from PIL import Image
from playwright.sync_api import sync_playwright

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
ASSET='https://assets.gemsny.com/'

def rows(p):
    with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def num(v):
    try:return int(float(str(v or 0).replace(',','').strip()))
    except:return 0
def write(p,fields,rr):
    with open(p,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([{k:r.get(k,'') for k in fields} for r in rr])
def exclusions(roots):
    out=set()
    for root in roots:
        if not root:continue
        for p in Path(root).rglob('coverage.csv'):
            try:
                for r in rows(p):
                    if r.get('status') in ('COMPLETE','NON_PRODUCT_PAGE') and r.get('category_url'):out.add(r['category_url'])
            except:pass
    return out

def flatten(o):
    if isinstance(o,list):return o
    if not isinstance(o,dict):return []
    for k in ('items','products','results','rows','records','data'):
        v=o.get(k)
        if isinstance(v,list) and v:return v
        if isinstance(v,dict):
            z=flatten(v)
            if z:return z
    for v in o.values():
        if isinstance(v,dict):
            z=flatten(v)
            if z:return z
    return []
def paginations(o):
    found=[]
    def walk(v,d=0):
        if d>5:return
        if isinstance(v,dict):
            keys={str(k).lower():k for k in v}
            for cand in ('totalcount','total','totalrecords','recordcount','count'):
                if cand in keys:
                    t=num(v.get(keys[cand]));
                    if t:found.append((t,v))
            for x in v.values():walk(x,d+1)
        elif isinstance(v,list):
            for x in v[:3]:walk(x,d+1)
    walk(o);return found
def key(d):
    if not isinstance(d,dict):return ''
    for k in ('sku','main_sku','product_sku','item_number','item','id','product_id'):
        v=d.get(k)
        if v not in (None,''):return str(v)
    return ''
def image_paths(d):
    out=[]
    def add(v):
        if isinstance(v,str) and re.search(r'\.(?:jpe?g|png|webp)(?:\?|$)',v,re.I) and not re.search(r'certificate|logo|icon|badge|placeholder',v,re.I):out.append(v)
    def walk(v,depth=0):
        if depth>4:return
        if isinstance(v,str):add(v)
        elif isinstance(v,list):
            for x in v:walk(x,depth+1)
        elif isinstance(v,dict):
            for kk,vv in v.items():
                if re.search(r'image|media|photo|thumb|url|src',str(kk),re.I):walk(vv,depth+1)
    walk(d);return out
def norm_image(v):
    if not v:return ''
    if v.startswith('//'):u='https:'+v
    elif v.startswith('http'):u=v
    else:u=urljoin(ASSET,v.lstrip('/'))
    try:
        p=urlparse(u);drop={'width','height','w','h','quality','q','format','fit','crop','auto','dpr'}
        q=[(k,x) for k,x in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in drop]
        return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),''))
    except:return u
def choose_image(d):
    pp=image_paths(d)
    for p in pp:
        if 'image-jewelry/' in p or 'image-gemstone/' in p:return norm_image(p)
    for p in pp:
        if 'video-' not in p and 'imposed-' not in p:return norm_image(p)
    return norm_image(pp[0]) if pp else ''
def measure(sess,u):
    if not u:return 0,0,'missing source image URL'
    try:
        r=sess.get(u,headers={'User-Agent':UA,'Referer':'https://www.gemsny.com/'},timeout=35,stream=True);r.raise_for_status()
        with Image.open(r.raw) as im:return int(im.width),int(im.height),''
    except Exception as e:return 0,0,f'{type(e).__name__}: {e}'[:250]

def capture_exact(page,url,expected):
    caps=[]
    def onresp(resp):
        try:
            if resp.status!=200:return
            ct=(resp.headers.get('content-type') or '').lower()
            if 'json' not in ct and 'storebe.gemsny.com' not in resp.url:return
            o=resp.json();it=flatten(o)
            if not it:return
            totals=paginations(o)
            if not any(t==expected for t,_ in totals):return
            req=resp.request
            caps.append({'url':resp.url,'method':req.method,'post_data':req.post_data,'items':it,'obj':o,'total':expected})
        except:pass
    page.on('response',onresp)
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=60000)
        page.wait_for_timeout(7000)
        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        page.wait_for_timeout(3000)
    except:pass
    return sorted(caps,key=lambda x:len(x['items']),reverse=True)

def req_json(sess,cap,page_no):
    method=cap['method'].upper();url=cap['url'];pd=cap.get('post_data')
    headers={'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Origin':'https://www.gemsny.com','Referer':'https://www.gemsny.com/'}
    if method=='GET':
        p=urlparse(url);q=dict(parse_qsl(p.query,keep_blank_values=True));q['page']=str(page_no)
        for k in ('pageSize','page_size','limit','perPage'):
            if k in q:
                try:q[k]=str(min(max(int(q[k]),24),100))
                except:pass
        u=urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''));r=sess.get(u,headers=headers,timeout=45)
    else:
        body={}
        if pd:
            try:body=json.loads(pd)
            except:body=dict(parse_qsl(pd,keep_blank_values=True))
        body['page']=page_no
        if method=='POST':r=sess.post(url,headers=headers,json=body,timeout=45)
        else:r=sess.request(method,url,headers=headers,json=body,timeout=45)
    r.raise_for_status();return r.json()
def enumerate_exact(sess,cap,expected):
    seen={}
    first=cap['items']
    for d in first:
        k=key(d)
        if k:seen.setdefault(k,d)
    if len(seen)==expected:return seen,1,''
    sig0=tuple(key(x) for x in first[:8] if key(x));pages=1;repeats=0
    for pn in range(2,max(3,(expected//max(1,len(first)))+8)):
        try:o=req_json(sess,cap,pn);it=flatten(o)
        except Exception as e:return seen,pages,f'{type(e).__name__}: {e}'
        pages+=1
        if not it:break
        sig=tuple(key(x) for x in it[:8] if key(x))
        if sig and sig==sig0:repeats+=1
        else:repeats=0
        for d in it:
            k=key(d)
            if k:seen.setdefault(k,d)
        if len(seen)>=expected:break
        if repeats>=2:break
        time.sleep(.03)
    return seen,pages,''

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline-coverage',required=True);ap.add_argument('--exclude-root',action='append',default=[]);ap.add_argument('--output-dir',required=True);ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--shard-count',type=int,required=True);ap.add_argument('--min-width',type=int,default=500);ap.add_argument('--min-height',type=int,default=500);a=ap.parse_args()
    od=Path(a.output_dir);od.mkdir(parents=True,exist_ok=True);base=rows(a.baseline_coverage);exc=exclusions(a.exclude_root)
    targets=[r for r in base if r.get('category_url') and r.get('status')!='COMPLETE' and r['category_url'] not in exc and num(r.get('expected_products_if_detected'))>0]
    mine=[r for i,r in enumerate(targets) if i%a.shard_count==a.shard_index]
    sess=requests.Session();cov=[];imgs=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-dev-shm-usage']);ctx=browser.new_context(user_agent=UA,viewport={'width':1440,'height':1000});page=ctx.new_page()
        for n,r in enumerate(mine,1):
            name=r.get('category_name') or r['category_url'];url=r['category_url'];expected=num(r.get('expected_products_if_detected'));caps=capture_exact(page,url,expected)
            if not caps:
                cov.append({'category_name':name,'category_url':url,'status':'UNRESOLVED - NO LIVE EXACT CONTRACT','expected_products_if_detected':expected,'unique_products_detected':0,'images_checked':0,'failed_images':0,'image_errors':0,'note':'No browser-observed JSON product response had a live total exactly matching expected coverage.'});print(f'[{n}/{len(mine)}] unresolved {name}: no exact live contract',flush=True);continue
            seen={};pages=0;err=''
            for cap in caps:
                seen,pages,err=enumerate_exact(sess,cap,expected)
                if len(seen)==expected:break
            if len(seen)!=expected:
                cov.append({'category_name':name,'category_url':url,'status':'UNRESOLVED - LIVE API PRODUCT COUNT','expected_products_if_detected':expected,'unique_products_detected':len(seen),'images_checked':0,'failed_images':0,'image_errors':0,'note':f'Browser-captured exact-total contract did not enumerate exactly; pages={pages}; {err}'});print(f'[{n}/{len(mine)}] unresolved {name}: {len(seen)}/{expected}',flush=True);continue
            sources={choose_image(d) for d in seen.values()};dims={}
            with ThreadPoolExecutor(max_workers=24) as ex:
                fs={ex.submit(measure,sess,u):u for u in sources}
                for f in as_completed(fs):dims[fs[f]]=f.result()
            cat=[]
            for k,d in seen.items():
                u=choose_image(d);w,h,e=dims.get(u,(0,0,'not measured'));st='ERROR' if e else ('PASS' if w>=a.min_width and h>=a.min_height else 'FAIL')
                cat.append({'category_name':name,'category_url':url,'category_page_url':url,'page_number':'LIVE_API','sku':k,'product_key':k,'product_title':str(d.get('name') or d.get('title') or ''),'product_url':str(d.get('redirect_url') or d.get('url') or ''),'image_url':u,'original_url':u,'original_width':w,'original_height':h,'original_status':st,'threshold_width':a.min_width,'threshold_height':a.min_height,'status':st,'compliance_basis':'ORIGINAL SOURCE','compliance_width':w,'compliance_height':h,'failure_reason':e if e else ('' if st=='PASS' else f'{w}x{h} below {a.min_width}x{a.min_height}'),'audit_mode':'REMAINING_BROWSER_API_V8','api_endpoint':urlparse(caps[0]['url']).path,'audited_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
            imgs.extend(cat);cov.append({'category_name':name,'category_url':url,'status':'COMPLETE','expected_products_if_detected':expected,'unique_products_detected':len(seen),'images_checked':len(cat),'failed_images':sum(x['status']=='FAIL' for x in cat),'image_errors':sum(x['status']=='ERROR' for x in cat),'note':f'Browser-observed live backend total matched expected exactly; pages={pages}; every expected SKU enumerated.'});print(f'[{n}/{len(mine)}] COMPLETE {name}: {len(seen)}',flush=True)
        browser.close()
    cf=['category_name','category_url','status','expected_products_if_detected','unique_products_detected','images_checked','failed_images','image_errors','note'];imf=['category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_url','original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis','compliance_width','compliance_height','failure_reason','audit_mode','api_endpoint','audited_at']
    write(od/'coverage.csv',cf,cov);write(od/'images.csv',imf,imgs);un=sum(x['status']!='COMPLETE' for x in cov);summary={'shard':a.shard_index,'targets':len(mine),'complete':len(cov)-un,'unresolved':un,'products':sum(num(x['unique_products_detected']) for x in cov if x['status']=='COMPLETE'),'images':len(imgs)};(od/'summary.json').write_text(json.dumps(summary,indent=2))
    if un:sys.exit(2)
if __name__=='__main__':main()
