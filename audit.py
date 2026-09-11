import asyncio,csv,json,re,argparse
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin,urlparse,parse_qsl,urlencode,urlunparse
from playwright.async_api import async_playwright

EXCLUDE=("/shoppingcart","/checkout","/member/","/login","/register","/contact","/privacy","/terms","/education","/blog","/faq","/about","/search/","/customer-service","/returns","/shipping","/warranty","/appraisal","/track-order","/order-status","/wishlist","/account","/refer","/careers","/press","/reviews","/financing","/appointment","/gemologist")
UI_RE=re.compile(r"logo|icon|sprite|payment|affirm|splitit|review|certificate|appraisal|packaging|loader|placeholder|flag|badge|footer|header|social|instagram|facebook|pinterest|youtube|thumbnail",re.I)
SKU_RE=re.compile(r"(?:SKU|Item#|Item\s*#)\s*[:#-]?\s*([A-Za-z0-9-]{3,})",re.I)


def same_host(a,b):
    return urlparse(a).netloc.lower().replace("www.","")==urlparse(b).netloc.lower().replace("www.","")

def clean(base,href):
    try:
        u=urljoin(base,href or ""); p=urlparse(u)
        if p.scheme not in ("http","https"): return ""
        return urlunparse((p.scheme,p.netloc,p.path,p.params,p.query,""))
    except: return ""

def candidate(site,u):
    if not u or not same_host(site,u): return False
    p=urlparse(u).path.lower().rstrip("/")
    if p in ("","/sitemap") or any(x in p for x in EXCLUDE): return False
    if re.search(r"\.(?:jpg|jpeg|png|webp|gif|svg|pdf|xml|txt|css|js)$",p): return False
    if re.search(r"-\d{4,}$",p): return False
    return True

def status(w,h,mw,mh):
    if w<=0 or h<=0: return "ERROR","Image did not load or dimensions unavailable"
    bad=[]
    if w<mw: bad.append(f"width {w} < {mw}")
    if h<mh: bad.append(f"height {h} < {mh}")
    if bad: return "FAIL","; ".join(bad)
    if w<1000 or h<1000: return "PASS - LOW RES","Meets minimum but below 1000x1000 preferred"
    return "PASS",""

def original_url(u):
    try:
        p=urlparse(u); drop={"width","height","w","h","quality","q","format","fit","crop","auto","dpr"}
        q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in drop]
        return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),""))
    except:return ""

async def measure(page,u):
    try:
        r=await page.evaluate("""async u=>await new Promise(res=>{let i=new Image(),t=setTimeout(()=>res([0,0]),10000);i.onload=()=>{clearTimeout(t);res([i.naturalWidth||0,i.naturalHeight||0])};i.onerror=()=>{clearTimeout(t);res([0,0])};i.src=u})""",u)
        return int(r[0] or 0),int(r[1] or 0)
    except:return 0,0

async def scroll(page):
    last=-1; stable=0
    for _ in range(35):
        try:
            h=int(await page.evaluate("document.body.scrollHeight") or 0)
            await page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
            await page.wait_for_timeout(500)
            stable=stable+1 if h==last else 0; last=h
            if stable>=3: break
        except: break
    try: await page.evaluate("window.scrollTo(0,0)")
    except: pass

async def discover(page,site,sitemap,max_categories,shard,shards):
    await page.goto(sitemap,wait_until="domcontentloaded",timeout=60000); await page.wait_for_timeout(1000)
    links=await page.eval_on_selector_all("a[href]","els=>els.map(a=>({u:a.href,t:(a.innerText||a.textContent||'').trim()}))")
    d={}
    for x in links:
        u=clean(sitemap,x.get("u"))
        if candidate(site,u): d[u]=x.get("t") or urlparse(u).path.strip("/")
    for u,n in {"https://www.gemsny.com/sapphires/basic-search":"Loose Sapphires","https://www.gemsny.com/emerald/basic-search":"Loose Emeralds","https://www.gemsny.com/sapphire-rings":"Sapphire Rings","https://www.gemsny.com/sapphire-earrings":"Sapphire Earrings"}.items(): d.setdefault(u,n)
    pri={"https://www.gemsny.com/sapphires/basic-search":0,"https://www.gemsny.com/sapphire-rings":1,"https://www.gemsny.com/sapphire-earrings":2,"https://www.gemsny.com/emerald/basic-search":3}
    items=sorted(d.items(),key=lambda x:(pri.get(x[0],100),x[0]))
    if max_categories>0: items=items[:max_categories]
    return [x for i,x in enumerate(items) if i%shards==shard]

async def meta(page):
    d=await page.evaluate("""()=>({body:(document.body?.innerText||'').slice(0,200000),links:[...document.querySelectorAll('a[href]')].map(a=>({t:(a.innerText||a.textContent||'').trim(),u:a.href}))})""")
    body=d["body"]; exp=0
    for pat in (r"([\d,]+)\s+results\b",r"([\d,]+)\s+products\b",r"([\d,]+)\s+items\b"):
        m=re.search(pat,body,re.I)
        if m:
            try: exp=int(m.group(1).replace(",","")); break
            except: pass
    q=dict(parse_qsl(urlparse(page.url).query)); cur=int(q.get("page",1)) if str(q.get("page",1)).isdigit() else 1
    nums=[]
    for x in d["links"]:
        u=clean(page.url,x.get("u"))
        if not u or not same_host(page.url,u): continue
        qq=dict(parse_qsl(urlparse(u).query)); v=qq.get("page")
        if str(v).isdigit(): nums.append((int(v),u))
    last=max([n for n,_ in nums],default=cur); nxt=""
    for n,u in sorted(nums):
        if n==cur+1: nxt=u; break
    if not nxt:
        higher=[(n,u) for n,u in nums if n>cur]
        if higher: nxt=sorted(higher)[0][1]
    return exp,cur,last,nxt,bool(nums)

async def grab(page,cat_name,cat_url,page_no,mw,mh,check_original):
    await scroll(page)
    data=await page.evaluate(r"""()=>{const abs=u=>{try{return new URL(u,location.href).href}catch(e){return''}};let out=[];for(const img of [...document.images]){const src=img.currentSrc||img.src||img.getAttribute('data-src')||img.getAttribute('data-lazy-src')||'';if(!src)continue;let n=img,ctx='',card=null;for(let i=0;i<9&&n;i++,n=n.parentElement){const tag=(n.tagName||'').toUpperCase();if(['BODY','HTML','MAIN'].includes(tag))break;const t=(n.innerText||'').trim();if(t&&t.length<5000&&/Item\s*#|SKU\s*[:#-]?\s*[A-Za-z0-9-]+|View\s*Details|\$\s*[\d,]+/i.test(t)){card=n;ctx=t;break}}const scope=card||img.parentElement;let a=img.closest('a[href]');if(!a&&scope){const as=[...scope.querySelectorAll('a[href]')];a=as.find(x=>/View\s*Details/i.test((x.innerText||x.textContent||'').trim()))||(as.length===1?as[0]:null)}const alt=(img.getAttribute('alt')||'').trim();const hay=alt+' '+ctx;const m=hay.match(/(?:SKU|Item#|Item\s*#)\s*[:#-]?\s*([A-Za-z0-9-]{3,})/i);const productish=!!m||/Item\s*#|View\s*Details|\$\s*[\d,]+/i.test(ctx)||/SKU\s*[:#-]?/i.test(alt);if(!productish)continue;out.push({src:abs(src),alt,w:img.naturalWidth||0,h:img.naturalHeight||0,sku:m?m[1]:'',pu:a&&a.href?abs(a.href):'',ctx:ctx.slice(0,1000)})}return out}""")
    rows=[]; seen=set()
    for x in data:
        src=x["src"]; alt=x["alt"]
        if not src or UI_RE.search(alt): continue
        w,h=int(x["w"] or 0),int(x["h"] or 0)
        if w and h and max(w,h)<120: continue
        sku=x["sku"] or ((SKU_RE.search((alt+" "+x["ctx"])) or [None,""])[1] if SKU_RE.search((alt+" "+x["ctx"])) else "")
        pu=x["pu"] if x["pu"] and same_host(cat_url,x["pu"]) else ""
        key=sku or pu or src
        st,reason=status(w,h,mw,mh)
        ou=original_url(src) if check_original else ""; ow=oh=0; ost="NOT CHECKED"
        if ou:
            if ou==src: ow,oh=w,h
            else: ow,oh=await measure(page,ou)
            ost,_=status(ow,oh,mw,mh)
        k=(key,src)
        if k in seen: continue
        seen.add(k)
        rows.append({"category_name":cat_name,"category_url":cat_url,"category_page_url":page.url,"page_number":page_no,"sku":sku,"product_key":key,"product_url":pu,"image_alt":alt,"image_url":src,"delivered_width":w,"delivered_height":h,"threshold_width":mw,"threshold_height":mh,"status":st,"failure_reason":reason,"original_url":ou,"original_width":ow,"original_height":oh,"original_status":ost,"audit_mode":"PLP","audited_at":datetime.now().isoformat(timespec="seconds")})
    return rows

async def main(a):
    out=Path(a.output_dir); out.mkdir(parents=True,exist_ok=True)
    rows=[]; cov=[]
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True); c=await b.new_context(viewport={"width":1440,"height":1200}); page=await c.new_page()
        cats=await discover(page,a.site,a.sitemap,a.max_categories,a.shard_index,a.shard_count)
        print(f"Shard {a.shard_index+1}/{a.shard_count}: {len(cats)} categories",flush=True)
        for idx,(u,n) in enumerate(cats,1):
            pc=0; visited=set(); catrows=[]; st="COMPLETE"; note=""; exp=0; last=1; pag=False
            try:
                await page.goto(u,wait_until="domcontentloaded",timeout=60000); await page.wait_for_timeout(1000)
                while True:
                    cur=page.url
                    if cur in visited: break
                    visited.add(cur); pc+=1
                    e,cp,lp,nxt,pd=await meta(page); exp=max(exp,e); last=max(last,lp); pag=pag or pd
                    rr=await grab(page,n,u,pc,a.min_width,a.min_height,a.check_original); catrows+=rr; rows+=rr
                    print(f"[{idx}/{len(cats)}] {n} page {pc}: {len(rr)} images",flush=True)
                    if a.max_pages>0 and pc>=a.max_pages:
                        if nxt or last>pc: st="PARTIAL - PAGE LIMIT"
                        break
                    if not nxt and pag and pc<last:
                        p0=urlparse(u); q=dict(parse_qsl(p0.query)); q["page"]=str(pc+1); nxt=urlunparse((p0.scheme,p0.netloc,p0.path,p0.params,urlencode(q),""))
                    if not nxt: break
                    await asyncio.sleep(a.delay); await page.goto(nxt,wait_until="domcontentloaded",timeout=60000); await page.wait_for_timeout(800)
            except Exception as e:
                st="ERROR"; note=str(e)
            keys={r['product_key'] for r in catrows if r['product_key']}
            if st=="COMPLETE":
                if not catrows: st="REVIEW - NO PRODUCT IMAGES"
                elif pag and pc<last: st="INCOMPLETE - PAGINATION"
                elif exp and keys and len(keys)<exp*0.85: st="REVIEW - PRODUCT COUNT MISMATCH"
            cov.append({"category_name":n,"category_url":u,"status":st,"pages_visited":pc,"pagination_detected":pag,"expected_last_page":last,"expected_products_if_detected":exp,"unique_products_detected":len(keys),"images_checked":len(catrows),"failed_images":sum(1 for r in catrows if r['status']=='FAIL'),"image_errors":sum(1 for r in catrows if r['status']=='ERROR'),"note":note})
            await asyncio.sleep(a.delay)
        await c.close(); await b.close()
    fields=["category_name","category_url","category_page_url","page_number","sku","product_key","product_url","image_alt","image_url","delivered_width","delivered_height","threshold_width","threshold_height","status","failure_reason","original_url","original_width","original_height","original_status","audit_mode","audited_at"]
    with open(out/'all_images.csv','w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    cf=list(cov[0].keys()) if cov else ["category_name","category_url","status"]
    with open(out/'coverage.csv','w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=cf); w.writeheader(); w.writerows(cov)
    (out/'shard_info.json').write_text(json.dumps({"shard_index":a.shard_index,"shard_count":a.shard_count,"categories":len(cov),"images":len(rows)},indent=2),encoding='utf-8')

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--site',default='https://www.gemsny.com'); ap.add_argument('--sitemap',default='https://www.gemsny.com/sitemap'); ap.add_argument('--min-width',type=int,default=500); ap.add_argument('--min-height',type=int,default=500); ap.add_argument('--delay',type=float,default=2); ap.add_argument('--output-dir',required=True); ap.add_argument('--shard-index',type=int,default=0); ap.add_argument('--shard-count',type=int,default=1); ap.add_argument('--max-categories',type=int,default=0); ap.add_argument('--max-pages',type=int,default=0); ap.add_argument('--check-original',action='store_true'); ap.add_argument('--no-check-original',dest='check_original',action='store_false'); ap.set_defaults(check_original=True)
    asyncio.run(main(ap.parse_args()))
