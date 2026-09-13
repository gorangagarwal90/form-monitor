import argparse,csv,json,re
from pathlib import Path
from urllib.parse import urlparse
import requests

BASE='https://storebe.gemsny.com'
CLIENT='cc4c1d93b1a0'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'


def read_csv(p):
    with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def exclusions(roots):
    urls=set()
    for root in roots:
        if not root: continue
        for p in Path(root).rglob('coverage*.csv'):
            try:
                for r in read_csv(p):
                    if r.get('status') in ('COMPLETE','NON_PRODUCT_PAGE') and r.get('category_url'):
                        urls.add(r['category_url'])
            except Exception: pass
    return urls

def infer(row):
    u=urlparse(row['category_url']).path.lower(); n=(row.get('category_name') or '').lower(); s=u+' '+n
    if 'basic-search' in u:
        fam='loose'
    elif 'earring' in s:
        fam='earring'
    elif 'pendant' in s:
        fam='pendant'
    elif 'bracelet' in s:
        fam='bracelet'
    elif 'wedding' in s or 'band' in s:
        fam='band'
    elif 'ring' in s:
        fam='ring'
    else:
        fam='other'
    gem=''
    for g in ('sapphire','ruby','emerald','alexandrite','tanzanite','aquamarine','tsavorite','morganite','peridot','tourmaline','topaz','amethyst','citrine','spinel','garnet','diamond'):
        if g in s: gem=g;break
    return fam,gem

def probes(row):
    fam,gem=infer(row); name=row.get('category_name',''); out=[]
    def add(ep,p): out.append((ep,p))
    common={'client_id':CLIENT,'page':1,'page_size':5,'pageSize':5}
    if fam=='loose':
        for ep in ('/gemstone','/gemstone/v2','/v2/gemstone','/v3/gemstone','/gemstones','/search/gemstone','/gemstone/search'):
            for key in ('gem_type','gemType','stone_type','stoneType','category','type'):
                p=dict(common);p.update({key:gem,'shape':'','color':'','origin':'','treatment':'','price':'','carat':'','sort_by':'featured','sortBy':'featured'})
                add(ep,p)
    elif fam=='ring':
        for typ in ('Preset Ring','Myo Ring','Ring',''):
            for skey in ('stoneType','gemType','gem_type'):
                p={'type':typ,'style':'','metal':'','centerStoneShape':'','price':'','width':'','sideStoneShape':'','centerStoneSetting':'','sideStoneSetting':'','collection':'','sortBy':'featured','page':1,'pageSize':5,skey:gem.title() if gem else ''}
                add('/ring/v2',p)
    elif fam=='earring':
        for ep in ('/v3/preset-earring','/v3/preset-earrings','/preset-earring'):
            p=dict(common);p.update({'type':'Preset Earring','gem_type':gem,'shape':'','style':'','metal':'','price':'','gemstone_quality_grade':'','diamond_quality_grade':'','carat':'','ready_to_ship':'false','sort_by':'featured'})
            add(ep,p)
    elif fam=='pendant':
        for ep in ('/v3/preset-pendant','/preset-pendant'):
            p=dict(common);p.update({'type':'Preset Pendant','gem_type':gem,'shape':'','style':'','metal':'','price':'','gemstone_quality_grade':'','diamond_quality_grade':'','carat':'','ready_to_ship':'false','sort_by':'featured'})
            add(ep,p)
    elif fam=='bracelet':
        for ep in ('/v3/preset-bracelet','/preset-bracelet'):
            p=dict(common);p.update({'gem_type':gem,'color':'','category':'','metal':'','shape':'','price':'','sortBy':'featured','sortByShipping':''})
            add(ep,p)
    elif fam=='band':
        for ep in ('/v3/preset-band','/preset-band'):
            p=dict(common);p.update({'stone_type':gem.title() if gem else '','shape':'','category':'','metal':'','total_weight':'','price':'','band_width':'','setting_type':'','band_type':'','sort_by':'featured'})
            add(ep,p)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline-coverage',required=True);ap.add_argument('--exclude-root',action='append',default=[]);ap.add_argument('--output',required=True);a=ap.parse_args()
    base=read_csv(a.baseline_coverage); exc=exclusions(a.exclude_root)
    targets=[r for r in base if r.get('category_url') and r.get('status')!='COMPLETE' and r['category_url'] not in exc]
    # representative categories per family + any high-count target
    picked=[]; seenfam=set()
    def cnt(r):
        try:return int(float((r.get('expected_products_if_detected') or '0').replace(',','')))
        except:return 0
    for r in sorted(targets,key=cnt,reverse=True):
        fam,_=infer(r)
        if fam not in seenfam or cnt(r)>=1000:
            picked.append(r); seenfam.add(fam)
        if len(picked)>=20: break
    sess=requests.Session();sess.headers.update({'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Origin':'https://www.gemsny.com','Referer':'https://www.gemsny.com/'})
    results=[]
    for r in picked:
        fam,gem=infer(r)
        for ep,p in probes(r):
            try:
                resp=sess.get(BASE+ep,params=p,timeout=30)
                body=resp.text[:1200]
                rec={'category_name':r.get('category_name'),'category_url':r['category_url'],'family':fam,'gem':gem,'expected':cnt(r),'endpoint':ep,'status':resp.status_code,'request_url':resp.url,'body':body}
                if resp.status_code==200:
                    try:
                        obj=resp.json(); rec['json_keys']=list(obj)[:30] if isinstance(obj,dict) else ['<list>']; rec['json_sample']=json.dumps(obj,ensure_ascii=False)[:3000]
                    except Exception: pass
                results.append(rec)
                print(json.dumps(rec,ensure_ascii=False),flush=True)
            except Exception as e:
                results.append({'category_name':r.get('category_name'),'category_url':r['category_url'],'family':fam,'gem':gem,'expected':cnt(r),'endpoint':ep,'status':'EXCEPTION','body':repr(e)})
    Path(a.output).write_text(json.dumps({'targets_total':len(targets),'sampled':len(picked),'results':results},indent=2,ensure_ascii=False),encoding='utf-8')

if __name__=='__main__': main()
