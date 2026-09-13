import argparse,csv,math,re,time,io
from pathlib import Path
from urllib.parse import urlparse,parse_qsl,urlencode,urlunparse,urljoin
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from bs4 import BeautifulSoup
from PIL import Image
from playwright.sync_api import sync_playwright

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
NON_PRODUCT_RE=re.compile(r'/(?:accessibility|about|privacy|terms|contact|education|blog|faq|returns|shipping|warranty|appraisal|track-order|order-status|refer|careers|press|reviews|financing|appointment|gemologist|login|register)(?:/|$)',re.I)
BAD_IMG_RE=re.compile(r'thumbnail|certificate|picture of the author|logo|icon|sprite|banner|hero|payment|review|badge|flag|loader|placeholder|footer|header|social',re.I)
SKU_RE=re.compile(r'(?:Item\s*#?|SKU)\s*[:#-]?\s*([A-Za-z0-9-]{3,})',re.I)
RESULT_RE=[re.compile(r'Result\s*\(\s*([\d,]+)\s*\)',re.I),re.compile(r'([\d,]+)\s+results\b',re.I),re.compile(r'([\d,]+)\s+products\b',re.I)]

def read_csv(p):
    with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def as_int(v,d=0):
    try:return int(float(str(v).replace(',','').strip()))
    except:return d
def page_url(base,n):
    p=urlparse(base);q=dict(parse_qsl(p.query,keep_blank_values=True));q['page']=str(n)
    return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),''))
def strip_transform(u):
    try:
        p=urlparse(u);drop={'width','height','w','h','quality','q','format','fit','crop','auto','dpr'}
        q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in drop]
        return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),''))
    except:return u
def expected_from_text(t):
    for pat in RESULT_RE:
        m=pat.search(t or '')
        if m:return int(m.group(1).replace(',',''))
    return None
def img_url(img,base):
    vals=[]
    for a in ('data-src','data-lazy-src','data-original','src'):
        v=img.get(a)
        if v:vals.append(v)
    ss=img.get('srcset') or img.get('data-srcset') or ''
    if ss:vals += [x.strip().split()[0] for x in ss.split(',') if x.strip()]
    for v in reversed(vals):
        if not v or v.startswith('data:'):continue
        u=urljoin(base,v)
        if u.startswith('http') and not BAD_IMG_RE.search(u):return strip_transform(u)
    return ''
def card_for(img):
    node=img
    for _ in range(10):
        node=getattr(node,'parent',None)
        if not node or getattr(node,'name',None) in ('body','html'):break
        txt=node.get_text(' ',strip=True)
        if SKU_RE.search(txt) or ('View Details' in txt and re.search(r'\$\s*[\d,.]+',txt)) or re.search(r'\$\s*[\d,.]+',txt):return node
    return img.parent
def extract_products(html,base):
    soup=BeautifulSoup(html,'html.parser');out={}
    for img in soup.find_all('img'):
        alt=(img.get('alt') or '').strip()
        if BAD_IMG_RE.search(alt):continue
        iu=img_url(img,base)
        if not iu:continue
        card=card_for(img);txt=card.get_text(' ',strip=True) if card else ''
        m=SKU_RE.search(txt+' '+alt);sku=m.group(1) if m else ''
        if not sku:
            m2=re.search(r'/image-jewelry/([^/?]+)/',iu,re.I)
            if m2:sku=m2.group(1)
        a=None
        if card:
            for cand in card.find_all('a',href=True):
                label=cand.get_text(' ',strip=True);href=urljoin(base,cand['href'])
                if 'View Details' in label or (href.startswith('https://www.gemsny.com/') and re.search(r'-\d{4,}(?:\?|$)',href)):
                    a=cand;break
        purl=urljoin(base,a['href']) if a else ''
        if not sku and not purl:continue
        if not sku and not re.search(r'\$\s*[\d,.]+',txt):continue
        key=sku or purl
        out.setdefault(key,{'sku':sku,'product_url':purl,'title':alt,'image_url':iu,'card_text':txt[:500]})
    return out,soup.get_text(' ',strip=True)

def browser_fetch(page,u):
    last=None
    for attempt in range(3):
        try:
            resp=page.goto(u,wait_until='domcontentloaded',timeout=90000)
            if resp and resp.status>=400:raise RuntimeError(f'HTTP {resp.status}')
            page.wait_for_timeout(1800 if attempt==0 else 3000)
            return page.content()
        except Exception as e:
            last=e;time.sleep(2+attempt*2)
    raise RuntimeError(f'Browser fetch failed: {last}')
def measure(u):
    try:
        r=requests.get(u,headers={'User-Agent':UA,'Referer':'https://www.gemsny.com/','Accept':'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'},timeout=35,stream=True);r.raise_for_status()
        with Image.open(r.raw) as im:return int(im.width),int(im.height),''
    except Exception as e:return 0,0,f'{type(e).__name__}: {e}'[:250]
def exclusions(roots):
    urls=set()
    for root in roots:
        for p in Path(root).rglob('coverage*.csv'):
            try:
                for r in read_csv(p):
                    if r.get('status') in ('COMPLETE','NON_PRODUCT_PAGE') and r.get('category_url'):urls.add(r['category_url'])
            except:pass
    return urls
def write_csv(p,fields,rows):
    with open(p,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline-coverage',required=True);ap.add_argument('--exclude-root',action='append',default=[]);ap.add_argument('--output-dir',required=True);ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--shard-count',type=int,required=True);ap.add_argument('--min-width',type=int,default=500);ap.add_argument('--min-height',type=int,default=500);a=ap.parse_args()
    out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    base=read_csv(a.baseline_coverage);exc=exclusions(a.exclude_root)
    targets=sorted([r for r in base if r.get('category_url') and r.get('status')!='COMPLETE' and r['category_url'] not in exc],key=lambda r:r['category_url'])
    mine=[r for i,r in enumerate(targets) if i%a.shard_count==a.shard_index]
    rows=[];cov=[];fails=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled'])
        context=browser.new_context(user_agent=UA,locale='en-US',viewport={'width':1440,'height':1000},extra_http_headers={'Accept-Language':'en-US,en;q=0.9','Referer':'https://www.gemsny.com/'})
        page=context.new_page()
        try: browser_fetch(page,'https://www.gemsny.com/')
        except Exception: pass
        for ix,row in enumerate(mine,1):
            name=row.get('category_name') or row['category_url'];baseurl=row['category_url']
            if NON_PRODUCT_RE.search(urlparse(baseurl).path):
                cov.append({'category_name':name,'category_url':baseurl,'status':'NON_PRODUCT_PAGE','expected_products_if_detected':0,'unique_products_detected':0,'images_checked':0,'failed_images':0,'image_errors':0,'note':'Confirmed non-product URL'});continue
            try:
                html=browser_fetch(page,baseurl);first,text=extract_products(html,baseurl)
                live=expected_from_text(text);baseline=as_int(row.get('expected_products_if_detected'));expected=live if live is not None else baseline
                if expected>0 and not first:raise RuntimeError(f'No browser-rendered product cards on page 1; live_expected={live} baseline_expected={baseline} html_bytes={len(html)}')
                pages=max(1,math.ceil(expected/24)) if expected else max(1,as_int(row.get('expected_last_page'),1))
                products={};sets=[]
                for n in range(1,pages+1):
                    u=baseurl if n==1 else page_url(baseurl,n);h=html if n==1 else browser_fetch(page,u);items,_=extract_products(h,u);sets.append(frozenset(items))
                    for k,v in items.items():products.setdefault(k,v)
                    if n==1 or n%25==0 or n==pages:print(f'[{ix}/{len(mine)}] {name} page={n}/{pages} page_products={len(items)} unique={len(products)}',flush=True)
                    if expected and len(products)>=expected:break
                    if n>=3 and not any(sets[-3:]):break
                if expected and len(products)!=expected:raise RuntimeError(f'Product count mismatch expected={expected} unique={len(products)} pages={pages}')
                if not products and expected!=0:raise RuntimeError('No products and no explicit zero count')
                nonempty=[s for s in sets if s]
                if pages>2 and len(nonempty)>1 and len(set(nonempty))<2:raise RuntimeError('Repeated same product set across forced pages')
                sources=sorted({v['image_url'] for v in products.values() if v['image_url']});dims={}
                with ThreadPoolExecutor(max_workers=24) as ex:
                    fs={ex.submit(measure,u):u for u in sources}
                    for f in as_completed(fs):dims[fs[f]]=f.result()
                cat=[]
                for k,v in products.items():
                    iu=v['image_url'];w,h,err=dims.get(iu,(0,0,'not measured'));status='ERROR' if err else ('PASS' if w>=a.min_width and h>=a.min_height else 'FAIL')
                    cat.append({'category_name':name,'category_url':baseurl,'category_page_url':baseurl,'page_number':'BROWSER','sku':v['sku'],'product_key':k,'product_title':v['title'],'product_url':v['product_url'],'image_alt':v['title'],'card_family':'BROWSER_RENDERED_PRODUCT','image_url':iu,'original_url':iu,'original_width':w,'original_height':h,'original_status':status,'threshold_width':a.min_width,'threshold_height':a.min_height,'status':status,'compliance_basis':'ORIGINAL SOURCE','compliance_width':w,'compliance_height':h,'failure_reason':err if err else ('' if status=='PASS' else f'{w}x{h} below {a.min_width}x{a.min_height}'),'audit_mode':'REMAINING_BROWSER_RECOVERY_V6','audited_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
                rows.extend(cat);cov.append({'category_name':name,'category_url':baseurl,'status':'COMPLETE','expected_products_if_detected':expected,'unique_products_detected':len(products),'images_checked':len(cat),'failed_images':sum(x['status']=='FAIL' for x in cat),'image_errors':sum(x['status']=='ERROR' for x in cat),'note':f'Strict browser-rendered forced-page recovery; pages={pages}; live_expected={live}'})
            except Exception as e:
                fails.append(baseurl);cov.append({'category_name':name,'category_url':baseurl,'status':'RECOVERY_FAILED','expected_products_if_detected':as_int(row.get('expected_products_if_detected')),'unique_products_detected':0,'images_checked':0,'failed_images':0,'image_errors':0,'note':f'{type(e).__name__}: {e}'})
                print(f'[{ix}/{len(mine)}] FAILED {name}: {type(e).__name__}: {e}',flush=True)
        browser.close()
    fields=['category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_alt','card_family','image_url','original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis','compliance_width','compliance_height','failure_reason','audit_mode','audited_at']
    write_csv(out/'all_images.csv',fields,rows)
    cfields=['category_name','category_url','status','expected_products_if_detected','unique_products_detected','images_checked','failed_images','image_errors','note'];write_csv(out/'coverage.csv',cfields,cov)
    if fails:raise SystemExit(f'{len(fails)} categories failed in shard {a.shard_index}')
if __name__=='__main__':main()
