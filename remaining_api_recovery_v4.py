import argparse,csv,json,math,re,time,io
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from PIL import Image

BASE='https://storebe.gemsny.com'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
CLIENT='cc4c1d93b1a0'
NON_PRODUCT_RE=re.compile(r'/(?:accessibility|about|privacy|terms|contact|education|blog|faq|returns|shipping|warranty|appraisal|track-order|order-status|refer|careers|press|reviews|financing|appointment|gemologist|login|register)(?:/|$)',re.I)
GEMS=['sapphire','ruby','emerald','alexandrite','tanzanite','aquamarine','tsavorite','morganite','peridot','tourmaline','topaz','amethyst','citrine','spinel','garnet','diamond']


def read_csv(p):
    with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def as_int(v,d=0):
    try:return int(float(str(v).replace(',','').strip()))
    except:return d

def flatten(obj):
    if isinstance(obj,list):return obj
    if not isinstance(obj,dict):return []
    for k in ('items','data','products','results','result','rows','records'):
        v=obj.get(k)
        if isinstance(v,list):return v
        if isinstance(v,dict):
            x=flatten(v)
            if x:return x
    best=[]
    for v in obj.values():
        x=flatten(v)
        if len(x)>len(best):best=x
    return best

def count_of(obj):
    if isinstance(obj,(int,float)):return int(obj)
    if not isinstance(obj,dict):return 0
    for k in ('count','total','total_count','totalCount','recordsTotal','result_count','totalRecords'):
        v=obj.get(k)
        if isinstance(v,(int,float)):return int(v)
        if isinstance(v,str) and v.replace(',','').isdigit():return int(v.replace(',',''))
    for v in obj.values():
        c=count_of(v)
        if c:return c
    return 0

def val(d,*ks):
    for k in ks:
        v=d.get(k) if isinstance(d,dict) else None
        if v not in (None,'',[],{}):return v
    return ''

def key_of(d):return str(val(d,'sku','item_number','itemNumber','product_sku','productSku','stock_number','stockNumber','id','product_id','productId','code')).strip()

def img_of(d):
    if not isinstance(d,dict):return ''
    for k in ('image','image_url','imageUrl','img','thumb','thumbnail','main_image','mainImage','product_image','productImage','media_url','mediaUrl','picture'):
        v=d.get(k)
        if isinstance(v,str) and v.startswith('http'):return v
        if isinstance(v,dict):
            for vv in v.values():
                if isinstance(vv,str) and vv.startswith('http'):return vv
    for k,v in d.items():
        if re.search(r'image|photo|picture|thumb|media',str(k),re.I) and isinstance(v,str) and v.startswith('http'):
            if not re.search(r'logo|icon|banner|badge|sprite|placeholder',v,re.I):return v
    return ''

def product_url(d):
    u=val(d,'redirect_url','redirectUrl','url','product_url','productUrl','detail_url','detailUrl','slug')
    if not u:return ''
    s=str(u)
    return s if s.startswith('http') else 'https://www.gemsny.com/'+s.lstrip('/')

def infer(row):
    url=row['category_url']; path=urlparse(url).path.lower(); name=(row.get('category_name') or '').lower()
    gem=''
    for g in GEMS:
        if g in path or g in name:
            gem=g;break
    if 'swiss-topaz' in path or 'swiss topaz' in name:gem='swiss-topaz'
    if 'london-topaz' in path or 'london topaz' in name:gem='london-topaz'
    if 'pink-tourmaline' in path or 'pink tourmaline' in name:gem='pink-tourmaline'
    if 'lab-diamond' in path or 'lab diamond' in name:gem='lab-diamond'
    if 'lab-sapphire' in path or 'lab sapphire' in name:gem='lab-sapphire'
    if 'lab-emerald' in path or 'lab emerald' in name:gem='lab-emerald'
    if 'lab-ruby' in path or 'lab ruby' in name:gem='lab-ruby'
    typ=''
    for t in ('ring','earring','pendant','bracelet'):
        if t in path or t in name:typ=t
    if 'wedding' in path or 'wedding' in name or 'band' in name:typ='band'
    loose=('basic-search' in path and not typ) or ('loose' in name and not typ)
    style=''
    for s in ('halo','solitaire','sidestone','side-stone','three-stone','vintage','dangle','hoop','two-stone','half-eternity','three-fourth-eternity'):
        if s in path or s.replace('-',' ') in name:style=s
    return gem,typ,loose,style

def candidates(row):
    gem,typ,loose,style=infer(row)
    c=[]
    def add(ep,params,page='page',size='page_size'):
        c.append((ep,params,page,size))
    if typ=='ring':
        for st in (gem,gem.replace('-',' ').title() if gem else '',gem.title() if gem else ''):
            add('/ring/v2',{'type':'Preset Ring','style':style.replace('-',' ').title() if style else '','metal':'','centerStoneShape':'','price':'','width':'','sideStoneShape':'','centerStoneSetting':'','sideStoneSetting':'','collection':'','sortBy':'featured','stoneType':st},'page','pageSize')
            add('/ring/v2',{'type':'Preset Ring','style':style.replace('-',' ').title() if style else '','metal':'','centerStoneShape':'','price':'','width':'','sideStoneShape':'','centerStoneSetting':'','sideStoneSetting':'','collection':'','sortBy':'featured','gemType':st},'page','pageSize')
    if typ=='earring':
        for gt in (gem,gem.replace('-',' ') if gem else ''):
            add('/v3/preset-earring',{'client_id':CLIENT,'type':'Preset Earring','gem_type':gt,'shape':'','style':style,'metal':'','price':'','gemstone_quality_grade':'','diamond_quality_grade':'','carat':'','ready_to_ship':'false','sort_by':'featured'})
            add('/v3/preset-earrings',{'client_id':CLIENT,'type':'Preset Earring','gem_type':gt,'shape':'','style':style,'metal':'','price':'','sort_by':'featured'})
    if typ=='pendant':
        add('/v3/preset-pendant',{'client_id':CLIENT,'type':'Preset Pendant','gem_type':gem,'shape':'','style':style,'metal':'','price':'','gemstone_quality_grade':'','diamond_quality_grade':'','carat':'','ready_to_ship':'false','sort_by':'featured'})
    if typ=='bracelet':
        add('/v3/preset-bracelet',{'client_id':CLIENT,'gem_type':gem,'color':'','category':style,'metal':'','shape':'','price':'','sortBy':'featured','sortByShipping':''},'page','pageSize')
    if typ=='band':
        add('/v3/preset-band',{'client_id':CLIENT,'stone_type':gem.replace('-',' ').title() if gem else '','shape':'','category':style.replace('-',' ').title() if style else '','metal':'','total_weight':'','price':'','band_width':'','setting_type':'','band_type':'','sort_by':'featured'})
    if loose:
        for ep in ('/gemstone/v2','/gemstone','/v3/gemstone','/gemstones','/v2/gemstone'):
            for gkey in ('gem_type','gemType','stone_type','stoneType','category'):
                add(ep,{'client_id':CLIENT,gkey:gem,'shape':'','color':'','origin':'','treatment':'','price':'','carat':'','sort_by':'featured'})
    # de-duplicate exact candidates
    seen=set();out=[]
    for x in c:
        sig=(x[0],json.dumps(x[1],sort_keys=True),x[2],x[3])
        if sig not in seen:seen.add(sig);out.append(x)
    return out

def probe(sess,row,expected):
    diagnostics=[]
    for ep,params,pagekey,sizekey in candidates(row):
        p=dict(params);p[pagekey]=1;p[sizekey]=100
        try:
            r=sess.get(BASE+ep,params=p,timeout=35)
            diagnostics.append(f'{ep}:{r.status_code}')
            if r.status_code!=200:continue
            obj=r.json();items=flatten(obj);cnt=count_of(obj)
            if not items:continue
            usable=sum(1 for d in items[:20] if key_of(d) and img_of(d))
            if usable==0:continue
            # strict selection: reported count must match expected when available
            if expected and cnt and cnt!=expected:continue
            if expected and not cnt and len(items)>=100: pass
            return ep,params,pagekey,sizekey,cnt,diagnostics
        except Exception as e:
            diagnostics.append(f'{ep}:{type(e).__name__}')
    return None,None,None,None,0,diagnostics

def enumerate_api(sess,sel,expected):
    ep,params,pagekey,sizekey,cnt,_=sel
    seen={};page=1;repeats=0
    while page<=10000:
        p=dict(params);p[pagekey]=page;p[sizekey]=100
        r=sess.get(BASE+ep,params=p,timeout=60);r.raise_for_status();obj=r.json();items=flatten(obj)
        if not items:break
        new=0
        for d in items:
            k=key_of(d)
            if not k:k=(product_url(d)+'|'+img_of(d)).strip('|')
            if not k or not img_of(d):continue
            if k not in seen:seen[k]=d;new+=1
        if new==0:repeats+=1
        else:repeats=0
        if expected and len(seen)>=expected:break
        if cnt and len(seen)>=cnt:break
        if len(items)<100:break
        if repeats>=2:break
        page+=1;time.sleep(.08)
    return seen,page

def measure(sess,u):
    try:
        r=sess.get(u,headers={'User-Agent':UA,'Referer':'https://www.gemsny.com/'},timeout=30,stream=True);r.raise_for_status()
        with Image.open(r.raw) as im:return int(im.width),int(im.height),''
    except Exception as e:return 0,0,f'{type(e).__name__}: {e}'[:250]

def exclusions(roots):
    urls=set()
    for root in roots:
        if not root:continue
        for p in Path(root).rglob('coverage*.csv'):
            try:
                for r in read_csv(p):
                    if r.get('status') in ('COMPLETE','NON_PRODUCT_PAGE') and r.get('category_url'):urls.add(r['category_url'])
            except:pass
    return urls

def write_csv(p,fields,rows):
    with open(p,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([{k:r.get(k,'') for k in fields} for r in rows])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline-coverage',required=True);ap.add_argument('--exclude-root',action='append',default=[]);ap.add_argument('--output-dir',required=True);ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--shard-count',type=int,required=True);ap.add_argument('--min-width',type=int,default=500);ap.add_argument('--min-height',type=int,default=500);a=ap.parse_args()
    out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    base=read_csv(a.baseline_coverage);exc=exclusions(a.exclude_root)
    targets=[r for r in base if r.get('category_url') and r.get('status')!='COMPLETE' and r['category_url'] not in exc]
    mine=[r for i,r in enumerate(targets) if i%a.shard_count==a.shard_index]
    sess=requests.Session();sess.headers.update({'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Origin':'https://www.gemsny.com','Referer':'https://www.gemsny.com/'})
    imgs=[];cov=[];fail=0
    for idx,row in enumerate(mine,1):
        name=row.get('category_name') or row['category_url'];url=row['category_url'];expected=as_int(row.get('expected_products_if_detected'))
        if NON_PRODUCT_RE.search(urlparse(url).path):
            cov.append({'category_name':name,'category_url':url,'status':'NON_PRODUCT_PAGE','expected_products_if_detected':0,'unique_products_detected':0,'images_checked':0,'failed_images':0,'image_errors':0,'note':'Confirmed non-product URL'});continue
        ep,params,pk,sk,cnt,diag=probe(sess,row,expected)
        if not ep:
            fail+=1;cov.append({'category_name':name,'category_url':url,'status':'INCOMPLETE - API NOT DISCOVERED','expected_products_if_detected':expected,'unique_products_detected':0,'images_checked':0,'failed_images':0,'image_errors':0,'note':'probes='+','.join(diag[:20])});print(f'[{idx}/{len(mine)}] FAIL API {name} expected={expected} probes={diag[:8]}',flush=True);continue
        try:
            seen,pages=enumerate_api(sess,(ep,params,pk,sk,cnt,diag),expected)
        except Exception as e:
            fail+=1;cov.append({'category_name':name,'category_url':url,'status':'INCOMPLETE - API ERROR','expected_products_if_detected':expected,'unique_products_detected':0,'images_checked':0,'failed_images':0,'image_errors':0,'note':f'{ep} {type(e).__name__}: {e}'});continue
        if expected and len(seen)!=expected:
            fail+=1;cov.append({'category_name':name,'category_url':url,'status':'INCOMPLETE - PRODUCT COUNT','expected_products_if_detected':expected,'unique_products_detected':len(seen),'images_checked':0,'failed_images':0,'image_errors':0,'note':f'{ep} pages={pages} api_count={cnt}'});print(f'[{idx}/{len(mine)}] FAIL COUNT {name} {len(seen)}/{expected} via {ep}',flush=True);continue
        if not seen:
            fail+=1;cov.append({'category_name':name,'category_url':url,'status':'INCOMPLETE - NO PRODUCTS','expected_products_if_detected':expected,'unique_products_detected':0,'images_checked':0,'failed_images':0,'image_errors':0,'note':f'{ep}'});continue
        sources={img_of(d) for d in seen.values() if img_of(d)};dims={}
        with ThreadPoolExecutor(max_workers=24) as ex:
            fs={ex.submit(measure,sess,u):u for u in sources}
            for f in as_completed(fs):dims[fs[f]]=f.result()
        cat=[]
        for k,d in seen.items():
            iu=img_of(d);w,h,err=dims.get(iu,(0,0,'not measured'));status='ERROR' if err else ('PASS' if w>=a.min_width and h>=a.min_height else 'FAIL')
            cat.append({'category_name':name,'category_url':url,'category_page_url':url,'page_number':'API','sku':key_of(d),'product_key':k,'product_title':str(val(d,'title','name','product_name','productName')),'product_url':product_url(d),'image_alt':str(val(d,'title','name')),'card_family':'API_DISCOVERY_RECOVERY','image_url':iu,'original_url':iu,'original_width':w,'original_height':h,'original_status':status,'threshold_width':a.min_width,'threshold_height':a.min_height,'status':status,'compliance_basis':'ORIGINAL SOURCE','compliance_width':w,'compliance_height':h,'failure_reason':err if err else ('' if status=='PASS' else f'{w}x{h} below {a.min_width}x{a.min_height}'),'audit_mode':'REMAINING_API_RECOVERY_V4','api_endpoint':ep,'audited_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
        imgs.extend(cat);cov.append({'category_name':name,'category_url':url,'status':'COMPLETE','expected_products_if_detected':expected or len(seen),'unique_products_detected':len(seen),'images_checked':len(cat),'failed_images':sum(x['status']=='FAIL' for x in cat),'image_errors':sum(x['status']=='ERROR' for x in cat),'note':f'{ep} pages={pages} api_count={cnt}'});print(f'[{idx}/{len(mine)}] COMPLETE {name} products={len(seen)} images={len(cat)} endpoint={ep}',flush=True)
    img_fields=['category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_alt','card_family','image_url','original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis','compliance_width','compliance_height','failure_reason','audit_mode','api_endpoint','audited_at']
    cov_fields=['category_name','category_url','status','expected_products_if_detected','unique_products_detected','images_checked','failed_images','image_errors','note']
    write_csv(out/'all_images.csv',img_fields,imgs);write_csv(out/'failed_images.csv',img_fields,[x for x in imgs if x['status'] in ('FAIL','ERROR')]);write_csv(out/'coverage.csv',cov_fields,cov)
    with open(out/'summary.json','w') as f:json.dump({'targets':len(mine),'complete':sum(x['status']=='COMPLETE' for x in cov),'non_product':sum(x['status']=='NON_PRODUCT_PAGE' for x in cov),'images':len(imgs),'fails':sum(x['status']=='FAIL' for x in imgs),'errors':sum(x['status']=='ERROR' for x in imgs),'category_failures':fail},f,indent=2)
    if fail:raise SystemExit(f'{fail} categories remain unresolved in shard {a.shard_index}')
if __name__=='__main__':main()
