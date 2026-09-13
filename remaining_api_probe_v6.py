import argparse,csv,json,re
from pathlib import Path
from urllib.parse import urlparse
import requests

BASE='https://storebe.gemsny.com'
CLIENT='cc4c1d93b1a0'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
GEMS=('sapphire','ruby','emerald','alexandrite','tanzanite','aquamarine','tsavorite','morganite','peridot','tourmaline','topaz','amethyst','citrine','spinel','garnet','diamond')

def read_csv(p):
    with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def as_int(v):
    try:return int(float(str(v or 0).replace(',','').strip()))
    except:return 0
def exclusions(roots):
    out=set()
    for root in roots:
        for p in Path(root).rglob('coverage*.csv'):
            try:
                for r in read_csv(p):
                    if r.get('status') in ('COMPLETE','NON_PRODUCT_PAGE') and r.get('category_url'):out.add(r['category_url'])
            except:pass
    return out
def infer(r):
    s=(urlparse(r['category_url']).path+' '+(r.get('category_name') or '')).lower()
    if 'basic-search' in s or 'loose ' in s:f='loose'
    elif 'earring' in s:f='earring'
    elif 'pendant' in s:f='pendant'
    elif 'bracelet' in s:f='bracelet'
    elif 'wedding' in s or 'band' in s:f='band'
    elif 'ring' in s:f='ring'
    else:f='other'
    g=next((x for x in GEMS if x in s),'')
    if 'swiss topaz' in s or 'swiss-topaz' in s:g='swiss-topaz'
    if 'london topaz' in s or 'london-topaz' in s:g='london-topaz'
    return f,g
def pick(rows):
    by={}
    for r in rows:by.setdefault(infer(r)[0],[]).append(r)
    out=[];seen=set()
    for fam,rr in sorted(by.items()):
        for r in sorted(rr,key=lambda x:as_int(x.get('expected_products_if_detected')),reverse=True)[:4]:
            if r['category_url'] not in seen:out.append(r);seen.add(r['category_url'])
    return out
def uniq(xs):
    out=[];seen=set()
    for ep,p in xs:
        k=(ep,json.dumps(p,sort_keys=True))
        if k not in seen:seen.add(k);out.append((ep,p))
    return out
def probes(r):
    fam,gem=infer(r); xs=[]
    if fam=='ring':
        ep='/ring/v2';xs += [(ep,{}),(ep,{'page':1,'pageSize':5}),(ep,{'type':'Preset Ring'}),(ep,{'type':'Preset Ring','page':1,'pageSize':5})]
        for k in ('stoneType','gemType','gem_type'):
            for v in filter(None,(gem,gem.replace('-',' ').title())):
                xs += [(ep,{'type':'Preset Ring',k:v}),(ep,{'type':'Preset Ring',k:v,'page':1,'pageSize':5})]
    elif fam=='earring':
        for ep in ('/v3/preset-earring','/preset-earring'):
            xs += [(ep,{}),(ep,{'client_id':CLIENT}),(ep,{'type':'Preset Earring'}),(ep,{'page':1,'pageSize':5})]
            for k in ('gem_type','gemType','stoneType'):
                if gem:xs += [(ep,{k:gem}),(ep,{'client_id':CLIENT,k:gem}),(ep,{'client_id':CLIENT,k:gem,'page':1,'pageSize':5})]
    elif fam=='pendant':
        for ep in ('/v3/preset-pendant','/preset-pendant'):
            xs += [(ep,{}),(ep,{'client_id':CLIENT}),(ep,{'type':'Preset Pendant'}),(ep,{'page':1,'pageSize':5})]
            for k in ('gem_type','gemType','stoneType'):
                if gem:xs += [(ep,{k:gem}),(ep,{'client_id':CLIENT,k:gem}),(ep,{'client_id':CLIENT,k:gem,'page':1,'pageSize':5})]
    elif fam=='bracelet':
        for ep in ('/v3/preset-bracelet','/preset-bracelet'):
            xs += [(ep,{}),(ep,{'client_id':CLIENT}),(ep,{'page':1,'pageSize':5})]
            for k in ('gem_type','gemType','stoneType'):
                if gem:xs += [(ep,{k:gem}),(ep,{'client_id':CLIENT,k:gem}),(ep,{'client_id':CLIENT,k:gem,'page':1,'pageSize':5})]
    elif fam=='band':
        for ep in ('/v3/preset-band','/preset-band'):
            xs += [(ep,{}),(ep,{'client_id':CLIENT}),(ep,{'page':1,'pageSize':5})]
            for k in ('stone_type','stoneType','gem_type','gemType'):
                if gem:xs += [(ep,{k:gem.replace('-',' ').title()}),(ep,{'client_id':CLIENT,k:gem.replace('-',' ').title()})]
    elif fam=='loose':
        for ep in ('/gemstone','/gemstone/v2'):
            xs += [(ep,{}),(ep,{'client_id':CLIENT}),(ep,{'page':1,'pageSize':5})]
            for k in ('gem_type','gemType','stone_type','stoneType','category','type'):
                for v in filter(None,(gem,gem.replace('-',' ').title())):
                    xs += [(ep,{k:v}),(ep,{'page':1,'pageSize':5,k:v}),(ep,{'client_id':CLIENT,'page':1,'pageSize':5,k:v})]
    return uniq(xs)
def flatten(o):
    if isinstance(o,list):return o
    if not isinstance(o,dict):return []
    for k in ('items','products','results','rows','records','data'):
        v=o.get(k)
        if isinstance(v,list):return v
        if isinstance(v,dict):
            z=flatten(v)
            if z:return z
    return []
def image_candidates(item):
    out=[]
    def add(v):
        if isinstance(v,str) and re.search(r'\.(?:jpe?g|png|webp)(?:\?|$)',v,re.I):out.append(v)
    if isinstance(item,dict):
        for k,v in item.items():
            if isinstance(v,str) and re.search(r'image|photo|thumb|url',k,re.I):add(v)
            elif isinstance(v,list) and re.search(r'image|media',k,re.I):
                for x in v:
                    if isinstance(x,dict):
                        for vv in x.values():add(vv)
            elif isinstance(v,dict) and re.search(r'image|media',k,re.I):
                for vv in v.values():add(vv)
    return out[:8]
def pagination(o):
    if not isinstance(o,dict):return None
    p=o.get('pagination')
    if isinstance(p,dict):return p
    for k,v in o.items():
        if isinstance(v,dict):
            z=pagination(v)
            if z:return z
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline-coverage',required=True);ap.add_argument('--exclude-root',action='append',default=[]);ap.add_argument('--output',required=True);a=ap.parse_args()
    base=read_csv(a.baseline_coverage); exc=exclusions(a.exclude_root)
    targets=[r for r in base if r.get('category_url') and r.get('status')!='COMPLETE' and r['category_url'] not in exc]
    sample=pick(targets); sess=requests.Session();sess.headers.update({'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Origin':'https://www.gemsny.com','Referer':'https://www.gemsny.com/'})
    results=[]
    for r in sample:
        fam,gem=infer(r)
        for ep,p in probes(r):
            rec={'category_name':r.get('category_name'),'category_url':r['category_url'],'family':fam,'gem':gem,'expected':as_int(r.get('expected_products_if_detected')),'endpoint':ep,'params':p}
            try:
                resp=sess.get(BASE+ep,params=p,timeout=30);rec['status']=resp.status_code;rec['request_url']=resp.url
                if resp.status_code==200:
                    obj=resp.json();items=flatten(obj);rec['item_count_page']=len(items);rec['pagination']=pagination(obj);rec['top_keys']=list(obj)[:30] if isinstance(obj,dict) else ['<list>']
                    if items:
                        x=items[0];rec['first_item_keys']=list(x)[:50] if isinstance(x,dict) else [];rec['first_item_sku']=str(x.get('sku') or x.get('item') or x.get('id') or '') if isinstance(x,dict) else '';rec['image_candidates']=image_candidates(x)
                else:rec['body']=resp.text[:1200]
            except Exception as e:rec['status']='EXCEPTION';rec['body']=repr(e)
            results.append(rec);print(json.dumps(rec,ensure_ascii=False),flush=True)
    Path(a.output).write_text(json.dumps({'targets_total':len(targets),'sampled':len(sample),'results':results},indent=2,ensure_ascii=False),encoding='utf-8')
if __name__=='__main__':main()
