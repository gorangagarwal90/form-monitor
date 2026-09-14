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
    if depth>9:return []
    out=[]
    if isinstance(o,list):
        if o and any(isinstance(x,dict) for x in o):out.append(o)
        for x in o[:10]:out.extend(walk_lists(x,depth+1))
    elif isinstance(o,dict):
        for v in o.values():out.extend(walk_lists(v,depth+1))
    return out

def key(d):
    if not isinstance(d,dict):return ''
    low={str(k).lower().replace('-','_'):v for k,v in d.items()}
    for k in ('sku','main_sku','product_sku','stock_number','stocknumber','stock_no','stockno','item_number','itemnumber','item','productid','product_id','jewelry_id','jewelryid','stone_id','stoneid','setting_id','settingid','id','code'):
        v=low.get(k)
        if v not in (None,'',0):return str(v)
    return ''

def image_paths(d):
    out=[]
    def add(s):
        if not isinstance(s,str) or len(s)<5:return
        if re.search(r'certificate|logo|icon|badge|sprite|placeholder|loader',s,re.I):return
        if ('assets.gemsny.com' in s or 'image-jewelry/' in s or 'image-gemstone/' in s or re.search(r'\.(?:jpe?g|png|webp)(?:[?#]|$)',s,re.I)):out.append(s)
    def walk(v,depth=0):
        if depth>7:return
        if isinstance(v,str):add(v)
        elif isinstance(v,list):
            for x in v[:50]:walk(x,depth+1)
        elif isinstance(v,dict):
            for kk,vv in v.items():
                if re.search(r'image|media|photo|thumb|picture|url|src|file',str(kk),re.I):walk(vv,depth+1)
    walk(d);return out

def productish_list(lst):
    sample=[x for x in lst[:12] if isinstance(x,dict)]
    if not sample:return False
    keyed=sum(bool(key(x)) for x in sample); imaged=sum(bool(image_paths(x)) for x in sample)
    return keyed>=1 and imaged>=1 and (keyed+imaged)>=max(3,len(sample)//2)
def best_items(o):
    ls=[x for x in walk_lists(o) if productish_list(x)]
    return max(ls,key=lambda z:(sum(bool(key(x)) for x in z),len(z))) if ls else []
def norm_image(v):
    if not v:return ''
    v=str(v).strip().split()[0]
    if v.startswith('//'):u='https:'+v
    elif v.startswith('http'):u=v
    else:u=urljoin(ASSET,v.lstrip('/'))
    try:
        p=urlparse(u);drop={'width','height','w','h','quality','q','format','fit','crop','auto','dpr','resize'}
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
            if not any(x in ct for x in ('json','javascript','text/plain')) and 'storebe.gemsny.com' not in resp.url:return
            try:o=resp.json()
            except:
                t=resp.text()
                if not t or t[:1] not in '[{':return
                o=json.loads(t)
            it=best_items(o)
            if not it:return
            req=resp.request
            try:h=req.all_headers()
            except:h={}
            caps.append({'url':resp.url,'method':req.method,'post_data':req.post_data,'headers':h,'items':it})
        except:pass
    page.on('response',onresp)
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=60000);page.wait_for_timeout(5000)
        for i in range(8):
            page.evaluate(f'window.scrollTo(0, document.body.scrollHeight*{(i+1)/8})');page.wait_for_timeout(650)
        page.wait_for_timeout(1500)
    except:pass
    out=[];seen=set()
    for c in sorted(caps,key=lambda x:len(x['items']),reverse=True):
        sig=(c['method'],c['url'],c.get('post_data') or '')
        if sig not in seen:seen.add(sig);out.append(c)
    return out

def replay(api,cap,field,value):
    method=cap['method'].upper();headers={k:v for k,v in (cap.get('headers') or {}).items() if k.lower() not in ('host','content-length','connection','accept-encoding','cookie')}
    if method=='GET':
        p=urlparse(cap['url']);pairs=parse_qsl(p.query,keep_blank_values=True);done=False;new=[]
        for k,v in pairs:
            if k==field:new.append((k,str(value)));done=True
            else:new.append((k,v))
        if not done:new.append((field,str(value)))
        u=urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(new,doseq=True),''));resp=api.get(u,headers=headers,timeout=45000)
    else:
        pd=cap.get('post_data') or '';body={};is_json=False
        try:body=json.loads(pd) if pd else {};is_json=isinstance(body,dict)
        except:body=dict(parse_qsl(pd,keep_blank_values=True))
        if not isinstance(body,dict):return []
        body[field]=value
        if is_json:
            headers['content-type']='application/json';data=json.dumps(body)
        else:
            headers['content-type']='application/x-www-form-urlencoded';data=urlencode(body)
        resp=api.fetch(cap['url'],method=method,headers=headers,data=data,timeout=45000)
    if not resp.ok:return []
    try:o=resp.json()
    except:return []
    return best_items(o)

def infer_field(api,cap):
    first=tuple(key(x) for x in cap['items'][:12] if key(x))
    if not first:return None,None
    p=urlparse(cap['url']);q={k.lower():k for k,_ in parse_qsl(p.query,keep_blank_values=True)}
    body={}
    try:
        if cap.get('post_data'):body=json.loads(cap['post_data'])
    except:pass
    bkeys={str(k).lower():k for k in body} if isinstance(body,dict) else {}
    aliases=['page','pageno','page_no','pagenumber','page_number','currentpage','current_page','offset','skip','start','from','pageindex','page_index']
    ordered=[]
    for a in aliases:
        if a in q:ordered.append(q[a])
        if a in bkeys:ordered.append(bkeys[a])
    ordered += [x for x in ('page','pageNo','pageNumber','currentPage','pageIndex','offset','skip','start','from') if x not in ordered]
    for f in ordered:
        isoff=f.lower() in ('offset','skip','start','from')
        vals=(24,48,50,100,2) if isoff else (2,3,1)
        for v in vals:
            try:it=replay(api,cap,f,v)
            except:continue
            sig=tuple(key(x) for x in it[:12] if key(x))
            if sig and sig!=first:return f,isoff
    return None,None

def enumerate_api(api,cap,expected):
    seen={}
    for d in cap['items']:
        k=key(d)
        if k:seen.setdefault(k,d)
    if len(seen)==expected:return seen,1,''
    field,isoff=infer_field(api,cap)
    if not field:return seen,1,'no working pagination field'
    step=max(1,len({key(x) for x in cap['items'] if key(x)}));pages=1;repeat=0;prev=tuple(key(x) for x in cap['items'][:12] if key(x))
    for n in range(2,math.ceil(expected/max(1,step))+12):
        value=(n-1)*step if isoff else n
        try:it=replay(api,cap,field,value)
        except Exception as e:return seen,pages,f'{type(e).__name__}: {e}'
        pages+=1
        if not it:break
        sig=tuple(key(x) for x in it[:12] if key(x));repeat=repeat+1 if sig==prev else 0;prev=sig
        before=len(seen)
        for d in it:
            k=key(d)
            if k:seen.setdefault(k,d)
        if len(seen)>=expected:break
        if repeat>=2 or (len(seen)==before and pages>=4):break
    return seen,pages,'' if len(seen)==expected else f'API product count {len(seen)}/{expected} via {field}'

def dom_products(page):
    try:return page.evaluate(r'''() => {
      const abs=u=>{try{return new URL(u,location.href).href}catch(e){return ''}},out=[],seen=new Set();
      const imgOf=(el)=>{if(!el)return '';let im=el.querySelector?.('img');let vals=[];if(im){for(const a of ['data-original','data-src','data-lazy-src','data-image','src','srcset']){let v=im.getAttribute(a);if(v)vals.push(v.split(',')[0].trim().split(/\s+/)[0])}}let so=el.querySelector?.('source[srcset]');if(so)vals.push((so.getAttribute('srcset')||'').split(',')[0].trim().split(/\s+/)[0]);for(const n of [el,...(el.querySelectorAll?.('[style*="background"]')||[])]){let m=(n.getAttribute?.('style')||'').match(/url\(["']?([^"')]+)/i);if(m)vals.push(m[1])}return vals.map(abs).find(x=>x&&(/assets\.gemsny\.com/i.test(x)||/image-(jewelry|gemstone)\//i.test(x)))||vals.map(abs).find(Boolean)||''};
      const nodes=[...document.querySelectorAll('img,a[href],[class*=product],[class*=Product],article,li')];
      for(const node of nodes){let card=node.closest?.('[class*=product],[class*=Product],article,li')||node.parentElement||node;let img=node.tagName==='IMG'?(node.getAttribute('data-original')||node.getAttribute('data-src')||node.getAttribute('data-lazy-src')||node.getAttribute('src')||node.currentSrc||''):imgOf(card);img=abs((img||'').split(',')[0].trim().split(/\s+/)[0]);if(!img||!/assets\.gemsny\.com|image-(jewelry|gemstone)\//i.test(img))continue;let a=(node.tagName==='A'?node:node.closest?.('a[href]')||card.querySelector?.('a[href]'));let pu=a?abs(a.href):'';let txt=(card.innerText||'').trim();let sm=txt.match(/(?:SKU|Item|Stock)\s*(?:#|No\.?|Number)?\s*[:#-]?\s*([A-Za-z0-9_-]{3,})/i);let mm=img.match(/\/image-(?:jewelry|gemstone)\/([^/?#]+)/i);let hm=(pu||'').match(/(?:sku|stock|item)[=\/-]([A-Za-z0-9_-]{3,})/i);let k=(sm&&sm[1])||(mm&&mm[1])||(hm&&hm[1])||pu||img;if(!k||seen.has(k))continue;seen.add(k);out.push({sku:k,product_url:pu,title:(card.querySelector?.('h1,h2,h3,h4,[class*=title],[class*=name]')?.textContent||'').trim(),image:img})}return out;
    }''')
    except:return []

def load_dom(page,u,wait=1800):
    try:
        page.goto(u,wait_until='domcontentloaded',timeout=60000);page.wait_for_timeout(wait)
        last=-1
        for i in range(8):
            page.evaluate('window.scrollTo(0,document.body.scrollHeight)');page.wait_for_timeout(450)
            n=len(dom_products(page))
            if n==last and i>=3:break
            last=n
        return dom_products(page)
    except:return []

def mutate_url(url,param,val):
    p=urlparse(url);q=list(parse_qsl(p.query,keep_blank_values=True));q=[(k,v) for k,v in q if k!=param];q.append((param,str(val)));return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))
def enumerate_browser(page,url,expected):
    first=load_dom(page,url,2600);seen={x['sku']:x for x in first if x.get('sku')};pages=1
    if len(seen)==expected:return seen,pages,''
    if not seen:return seen,pages,f'browser DOM product count 0/{expected}'
    firstsig=tuple(seen)[:12];chosen=None
    for param in ('page','pageno','pageNo','pageNumber','pg','p'):
        it=load_dom(page,mutate_url(url,param,2),1200);sig=tuple(x.get('sku') for x in it[:12] if x.get('sku'))
        if sig and sig!=firstsig:chosen=param;break
    if not chosen:return seen,pages,f'browser DOM product count {len(seen)}/{expected}; no query pagination'
    step=max(1,len(seen));maxp=math.ceil(expected/step)+8;repeat=0;prev=firstsig
    for n in range(2,maxp+1):
        it=load_dom(page,mutate_url(url,chosen,n),1000);pages+=1
        if not it:break
        sig=tuple(x.get('sku') for x in it[:12] if x.get('sku'));repeat=repeat+1 if sig==prev else 0;prev=sig
        before=len(seen)
        for x in it:
            if x.get('sku'):seen.setdefault(x['sku'],x)
        if len(seen)>=expected:break
        if repeat>=2 or (len(seen)==before and n>=3):break
    return seen,pages,'' if len(seen)==expected else f'browser DOM product count {len(seen)}/{expected}; pagination_param={chosen}'

def build_rows(name,url,seen,dims,minw,minh,mode,endpoint=''):
    out=[]
    for k,d in seen.items():
        u=choose_image(d) if mode=='LIVE_API' else norm_image(d.get('image',''));w,h,e=dims.get(u,(0,0,'not measured'));st='ERROR' if e else ('PASS' if w>=minw and h>=minh else 'FAIL')
        out.append({'category_name':name,'category_url':url,'category_page_url':url,'page_number':mode,'sku':k,'product_key':k,'product_title':str(d.get('name') or d.get('title') or ''),'product_url':str(d.get('redirect_url') or d.get('product_url') or d.get('url') or ''),'image_url':u,'original_url':u,'original_width':w,'original_height':h,'original_status':st,'threshold_width':minw,'threshold_height':minh,'status':st,'compliance_basis':'ORIGINAL SOURCE','compliance_width':w,'compliance_height':h,'failure_reason':e if e else ('' if st=='PASS' else f'{w}x{h} below {minw}x{minh}'),'audit_mode':'REMAINING_BROWSER_V10_'+mode,'api_endpoint':endpoint,'audited_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline-coverage',required=True);ap.add_argument('--exclude-root',action='append',default=[]);ap.add_argument('--output-dir',required=True);ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--shard-count',type=int,required=True);ap.add_argument('--min-width',type=int,default=500);ap.add_argument('--min-height',type=int,default=500);a=ap.parse_args()
    od=Path(a.output_dir);od.mkdir(parents=True,exist_ok=True);base=rows(a.baseline_coverage);exc=exclusions(a.exclude_root)
    targets=[r for r in base if r.get('category_url') and r.get('status')!='COMPLETE' and r['category_url'] not in exc and num(r.get('expected_products_if_detected'))>0];mine=[r for i,r in enumerate(targets) if i%a.shard_count==a.shard_index]
    sess=requests.Session();cov=[];imgs=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-dev-shm-usage','--no-sandbox']);ctx=browser.new_context(user_agent=UA,viewport={'width':1440,'height':1100},locale='en-US');page=ctx.new_page();api=ctx.request
        for n,r in enumerate(mine,1):
            name=r.get('category_name') or r['category_url'];url=r['category_url'];expected=num(r.get('expected_products_if_detected'));seen={};pages=0;err='';mode='LIVE_API';endpoint=''
            caps=capture_candidates(page,url)
            for cap in caps:
                candidate,p,e=enumerate_api(api,cap,expected)
                if len(candidate)>len(seen):seen,pages,err=candidate,p,e;endpoint=urlparse(cap['url']).path
                if len(seen)==expected:break
            if len(seen)!=expected:
                mode='BROWSER_DOM';candidate,p,e=enumerate_browser(page,url,expected)
                if len(candidate)>=len(seen):seen,pages,err=candidate,p,e;endpoint=''
            if len(seen)!=expected:
                cov.append({'category_name':name,'category_url':url,'status':'UNRESOLVED - STRICT PRODUCT COUNT','expected_products_if_detected':expected,'unique_products_detected':len(seen),'images_checked':0,'failed_images':0,'image_errors':0,'note':f'V10 exact validation failed; pages={pages}; caps={len(caps)}; {err}'});print(f'[{n}/{len(mine)}] unresolved {name}: {len(seen)}/{expected} caps={len(caps)} {err}',flush=True);continue
            sources={choose_image(d) if mode=='LIVE_API' else norm_image(d.get('image','')) for d in seen.values()};dims={}
            with ThreadPoolExecutor(max_workers=20) as ex:
                fs={ex.submit(measure,sess,u):u for u in sources}
                for f in as_completed(fs):dims[fs[f]]=f.result()
            cat=build_rows(name,url,seen,dims,a.min_width,a.min_height,mode,endpoint);imgs.extend(cat);fail=sum(x['status']=='FAIL' for x in cat);errs=sum(x['status']=='ERROR' for x in cat)
            cov.append({'category_name':name,'category_url':url,'status':'COMPLETE','expected_products_if_detected':expected,'unique_products_detected':len(seen),'images_checked':len(cat),'failed_images':fail,'image_errors':errs,'note':f'V10 strict exact product count; pages={pages}; mode={mode}; endpoint={endpoint}'});print(f'[{n}/{len(mine)}] COMPLETE {name}: products={len(seen)} images={len(cat)} fail={fail} errors={errs}',flush=True)
        browser.close()
    cfields=['category_name','category_url','status','expected_products_if_detected','unique_products_detected','images_checked','failed_images','image_errors','note'];ifields=['category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_url','original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis','compliance_width','compliance_height','failure_reason','audit_mode','api_endpoint','audited_at']
    write(od/'coverage.csv',cfields,cov);write(od/'images.csv',ifields,imgs);(od/'summary.json').write_text(json.dumps({'shard':a.shard_index,'targets':len(mine),'complete':sum(x['status']=='COMPLETE' for x in cov),'unresolved':sum(x['status']!='COMPLETE' for x in cov),'images':len(imgs)},indent=2),encoding='utf-8')
    if any(x['status']!='COMPLETE' for x in cov):sys.exit(2)
if __name__=='__main__':main()
