import argparse,csv,json,re,time,io,sys,math
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
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rr)
def exclusions(roots):
    out=set()
    for root in roots:
        for p in Path(root).rglob('coverage.csv'):
            try:
                for r in rows(p):
                    if r.get('status') in ('COMPLETE','NON_PRODUCT_PAGE') and r.get('category_url'):out.add(r['category_url'])
            except:pass
    return out

def walk_lists(o,depth=0):
    if depth>6:return []
    out=[]
    if isinstance(o,list):
        if o and any(isinstance(x,dict) for x in o):out.append(o)
        for x in o[:4]:out.extend(walk_lists(x,depth+1))
    elif isinstance(o,dict):
        for v in o.values():out.extend(walk_lists(v,depth+1))
    return out
def key(d):
    if not isinstance(d,dict):return ''
    low={str(k).lower():v for k,v in d.items()}
    for k in ('sku','main_sku','product_sku','item_number','itemnumber','item','productid','product_id','id','code'):
        v=low.get(k)
        if v not in (None,''):return str(v)
    return ''
def image_paths(d):
    out=[]
    def walk(v,depth=0):
        if depth>5:return
        if isinstance(v,str):
            if re.search(r'\.(?:jpe?g|png|webp)(?:\?|$)',v,re.I) and not re.search(r'certificate|logo|icon|badge|placeholder',v,re.I):out.append(v)
        elif isinstance(v,list):
            for x in v:walk(x,depth+1)
        elif isinstance(v,dict):
            for kk,vv in v.items():
                if re.search(r'image|media|photo|thumb|url|src',str(kk),re.I):walk(vv,depth+1)
    walk(d);return out
def productish_list(lst):
    sample=[x for x in lst[:8] if isinstance(x,dict)]
    if not sample:return False
    score=sum(bool(key(x)) for x in sample)+sum(bool(image_paths(x)) for x in sample)
    return score>=max(2,len(sample)//2)
def best_items(o):
    ls=[x for x in walk_lists(o) if productish_list(x)]
    return max(ls,key=len) if ls else []
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

def capture_candidates(page,url):
    caps=[]
    def onresp(resp):
        try:
            if resp.status!=200:return
            ct=(resp.headers.get('content-type') or '').lower()
            if 'json' not in ct and 'storebe.gemsny.com' not in resp.url:return
            o=resp.json();it=best_items(o)
            if not it:return
            req=resp.request
            caps.append({'url':resp.url,'method':req.method,'post_data':req.post_data,'items':it})
        except:pass
    page.on('response',onresp)
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=60000);page.wait_for_timeout(4500)
        for y in (0.35,0.7,1.0):page.evaluate(f'window.scrollTo(0, document.body.scrollHeight*{y})');page.wait_for_timeout(600)
    except:pass
    # dedupe by request shape
    out=[];seen=set()
    for c in sorted(caps,key=lambda x:len(x['items']),reverse=True):
        sig=(c['method'],c['url'],c.get('post_data') or '')
        if sig not in seen:seen.add(sig);out.append(c)
    return out

def request_variant(sess,cap,field,value):
    method=cap['method'].upper();headers={'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Origin':'https://www.gemsny.com','Referer':'https://www.gemsny.com/'}
    if method=='GET':
        p=urlparse(cap['url']);q=dict(parse_qsl(p.query,keep_blank_values=True));q[field]=str(value)
        u=urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''));r=sess.get(u,headers=headers,timeout=45)
    else:
        pd=cap.get('post_data') or '';body={}
        try:body=json.loads(pd) if pd else {}
        except:body=dict(parse_qsl(pd,keep_blank_values=True))
        if not isinstance(body,dict):return []
        body[field]=value
        r=sess.request(method,cap['url'],headers=headers,json=body,timeout=45)
    r.raise_for_status();return best_items(r.json())
def infer_field(sess,cap):
    first=tuple(key(x) for x in cap['items'][:10] if key(x))
    if not first:return None
    p=urlparse(cap['url']);q={k.lower():k for k,_ in parse_qsl(p.query,keep_blank_values=True)}
    body={}
    try:
        if cap.get('post_data'):body=json.loads(cap['post_data'])
    except:pass
    bkeys={str(k).lower():k for k in body} if isinstance(body,dict) else {}
    aliases=['page','pageno','page_no','pagenumber','page_number','currentpage','current_page','offset','skip','start']
    ordered=[]
    for a in aliases:
        if a in q:ordered.append(q[a])
        if a in bkeys:ordered.append(bkeys[a])
    ordered += [x for x in ('page','pageNo','pageNumber','currentPage','offset','skip','start') if x not in ordered]
    for f in ordered:
        vals=(2,24,48) if f.lower() not in ('offset','skip','start') else (24,48,72)
        for v in vals:
            try:it=request_variant(sess,cap,f,v)
            except:continue
            sig=tuple(key(x) for x in it[:10] if key(x))
            if sig and sig!=first:return f
    return None
def enumerate_api(sess,cap,expected):
    first=cap['items'];seen={}
    for d in first:
        k=key(d)
        if k:seen.setdefault(k,d)
    if len(seen)==expected:return seen,1,''
    field=infer_field(sess,cap)
    if not field:return seen,1,'no working pagination field'
    step=max(1,len({key(x) for x in first if key(x)}));pages=1;repeat=0;prev=tuple(key(x) for x in first[:10] if key(x))
    for n in range(2,math.ceil(expected/max(1,step))+8):
        value=(n-1)*step if field.lower() in ('offset','skip','start') else n
        try:it=request_variant(sess,cap,field,value)
        except Exception as e:return seen,pages,f'{type(e).__name__}: {e}'
        pages+=1
        if not it:break
        sig=tuple(key(x) for x in it[:10] if key(x));repeat=repeat+1 if sig==prev else 0;prev=sig
        for d in it:
            k=key(d)
            if k:seen.setdefault(k,d)
        if len(seen)>=expected:break
        if repeat>=2:break
    return seen,pages,'' if len(seen)==expected else f'API product count {len(seen)}/{expected} via {field}'

def dom_products(page):
    return page.evaluate(r'''() => {const abs=u=>{try{return new URL(u,location.href).href}catch(e){return ''}},out=[],seen=new Set();
    const imgs=[...document.querySelectorAll('img')];for(const img of imgs){let src=abs(img.getAttribute('data-src')||img.getAttribute('data-lazy-src')||img.getAttribute('src')||img.currentSrc||'');if(!/\.(jpe?g|png|webp)(\?|$)/i.test(src)||!/image-(jewelry|gemstone)\//i.test(src))continue;let a=img.closest('a[href]'),card=img.closest('[class*=product],[class*=Product],article,li,div'),pu=a?abs(a.href):'',m=src.match(/\/image-(?:jewelry|gemstone)\/([^/]+)\//i),txt=(card?.innerText||''),sm=txt.match(/(?:SKU|Item\s*#?)\s*[:#-]?\s*([A-Za-z0-9-]{3,})/i),k=(sm&&sm[1])||(m&&m[1])||pu;if(!k||seen.has(k))continue;seen.add(k);out.push({sku:k,product_url:pu,title:(card?.querySelector('h1,h2,h3,h4,[class*=title],[class*=name]')?.textContent||'').trim(),image:src});}return out;}''')
def page_links(page):
    try:return page.evaluate(r'''() => [...document.querySelectorAll('a[href]')].map(a=>({href:a.href,text:(a.textContent||'').trim(),rel:a.rel||'',cls:a.className||''})).filter(x=>/next|pagination|page/i.test(x.rel+' '+x.cls+' '+x.text)||/^\d+$/.test(x.text)).slice(0,300)''')
    except:return []
def pagination_pattern(links,base):
    b=urlparse(base)
    counts={}
    for x in links:
        p=urlparse(x.get('href',''))
        if p.netloc and p.netloc!=b.netloc:continue
        for k,v in parse_qsl(p.query,keep_blank_values=True):
            if str(v).isdigit():counts[k]=counts.get(k,0)+1
    return max(counts,key=counts.get) if counts else None
def enumerate_browser(page,url,expected):
    seen={};pages=0
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=60000);page.wait_for_timeout(2500)
        page.evaluate('window.scrollTo(0,document.body.scrollHeight)');page.wait_for_timeout(700)
    except Exception as e:return seen,pages,str(e)
    first=dom_products(page);pages=1
    for x in first:
        if x.get('sku'):seen.setdefault(x['sku'],x)
    links=page_links(page);param=pagination_pattern(links,url);step=max(1,len(seen));maxp=math.ceil(expected/step)+5 if step else 3
    if param:
        for n in range(2,maxp+1):
            p=urlparse(url);q=dict(parse_qsl(p.query,keep_blank_values=True));q[param]=str(n);u=urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),''))
            try:page.goto(u,wait_until='domcontentloaded',timeout=60000);page.wait_for_timeout(1200);page.evaluate('window.scrollTo(0,document.body.scrollHeight)');page.wait_for_timeout(500);it=dom_products(page)
            except:break
            pages+=1
            if not it:break
            before=len(seen)
            for x in it:
                if x.get('sku'):seen.setdefault(x['sku'],x)
            if len(seen)>=expected:break
            if len(seen)==before and n>=3:break
    return seen,pages,'' if len(seen)==expected else f'browser DOM product count {len(seen)}/{expected}; pagination_param={param}'

def build_rows(name,url,seen,dims,minw,minh,mode,endpoint=''):
    out=[]
    for k,d in seen.items():
        u=choose_image(d) if mode=='LIVE_API' else norm_image(d.get('image',''));w,h,e=dims.get(u,(0,0,'not measured'));st='ERROR' if e else ('PASS' if w>=minw and h>=minh else 'FAIL')
        out.append({'category_name':name,'category_url':url,'category_page_url':url,'page_number':mode,'sku':k,'product_key':k,'product_title':str(d.get('name') or d.get('title') or ''),'product_url':str(d.get('redirect_url') or d.get('product_url') or d.get('url') or ''),'image_url':u,'original_url':u,'original_width':w,'original_height':h,'original_status':st,'threshold_width':minw,'threshold_height':minh,'status':st,'compliance_basis':'ORIGINAL SOURCE','compliance_width':w,'compliance_height':h,'failure_reason':e if e else ('' if st=='PASS' else f'{w}x{h} below {minw}x{minh}'),'audit_mode':'REMAINING_BROWSER_V9_'+mode,'api_endpoint':endpoint,'audited_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline-coverage',required=True);ap.add_argument('--exclude-root',action='append',default=[]);ap.add_argument('--output-dir',required=True);ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--shard-count',type=int,required=True);ap.add_argument('--min-width',type=int,default=500);ap.add_argument('--min-height',type=int,default=500);a=ap.parse_args()
    od=Path(a.output_dir);od.mkdir(parents=True,exist_ok=True);base=rows(a.baseline_coverage);exc=exclusions(a.exclude_root)
    targets=[r for r in base if r.get('category_url') and r.get('status')!='COMPLETE' and r['category_url'] not in exc and num(r.get('expected_products_if_detected'))>0];mine=[r for i,r in enumerate(targets) if i%a.shard_count==a.shard_index]
    sess=requests.Session();cov=[];imgs=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-dev-shm-usage']);ctx=browser.new_context(user_agent=UA,viewport={'width':1440,'height':1100});page=ctx.new_page()
        for n,r in enumerate(mine,1):
            name=r.get('category_name') or r['category_url'];url=r['category_url'];expected=num(r.get('expected_products_if_detected'));seen={};pages=0;err='';mode='LIVE_API';endpoint=''
            caps=capture_candidates(page,url)
            for cap in caps:
                candidate,p,e=enumerate_api(sess,cap,expected)
                if len(candidate)>len(seen):seen,pages,err=candidate,p,e;endpoint=urlparse(cap['url']).path
                if len(seen)==expected:break
            if len(seen)!=expected:
                mode='BROWSER_DOM';candidate,p,e=enumerate_browser(page,url,expected)
                if len(candidate)>=len(seen):seen,pages,err=candidate,p,e;endpoint=''
            if len(seen)!=expected:
                cov.append({'category_name':name,'category_url':url,'status':'UNRESOLVED - STRICT PRODUCT COUNT','expected_products_if_detected':expected,'unique_products_detected':len(seen),'images_checked':0,'failed_images':0,'image_errors':0,'note':f'V9 exact validation failed; pages={pages}; {err}'});print(f'[{n}/{len(mine)}] unresolved {name}: {len(seen)}/{expected} {err}',flush=True);continue
            sources={choose_image(d) if mode=='LIVE_API' else norm_image(d.get('image','')) for d in seen.values()};dims={}
            with ThreadPoolExecutor(max_workers=20) as ex:
                fs={ex.submit(measure,sess,u):u for u in sources}
                for f in as_completed(fs):dims[fs[f]]=f.result()
            cat=build_rows(name,url,seen,dims,a.min_width,a.min_height,mode,endpoint);imgs.extend(cat);fail=sum(x['status']=='FAIL' for x in cat);errs=sum(x['status']=='ERROR' for x in cat)
            cov.append({'category_name':name,'category_url':url,'status':'COMPLETE','expected_products_if_detected':expected,'unique_products_detected':len(seen),'images_checked':len(cat),'failed_images':fail,'image_errors':errs,'note':f'V9 strict exact product count; pages={pages}; mode={mode}; endpoint={endpoint}'});print(f'[{n}/{len(mine)}] COMPLETE {name}: products={len(seen)} images={len(cat)} fail={fail} errors={errs}',flush=True)
        browser.close()
    cfields=['category_name','category_url','status','expected_products_if_detected','unique_products_detected','images_checked','failed_images','image_errors','note'];ifields=['category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_url','original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis','compliance_width','compliance_height','failure_reason','audit_mode','api_endpoint','audited_at']
    write(od/'coverage.csv',cfields,cov);write(od/'images.csv',ifields,imgs);(od/'summary.json').write_text(json.dumps({'shard':a.shard_index,'targets':len(mine),'complete':sum(x['status']=='COMPLETE' for x in cov),'unresolved':sum(x['status']!='COMPLETE' for x in cov),'images':len(imgs)},indent=2),encoding='utf-8')
    if any(x['status']!='COMPLETE' for x in cov):sys.exit(2)
if __name__=='__main__':main()
