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

def add_unique(out,ep,p):
    sig=(ep,json.dumps(p,sort_keys=True))
    if sig not in {(x[0],json.dumps(x[1],sort_keys=True)) for x in out}: out.append((ep,p))

def probes(row):
    fam,gem=infer(row); out=[]
    # Probe minimal contracts first. The prior version mixed many guessed fields in every
    # request, which made whitelist-validation errors impossible to interpret.
    if fam=='loose':
        for ep in ('/gemstone','/gemstone/v2','/v2/gemstone','/v3/gemstone','/gemstones','/search/gemstone','/gemstone/search'):
            bases=[{}, {'client_id':CLIENT}, {'page':1}, {'pageSize':5}, {'page':1,'pageSize':5}, {'client_id':CLIENT,'page':1,'pageSize':5}]
            for p in bases:add_unique(out,ep,p)
            for key in ('gem_type','gemType','stone_type','stoneType','category','type'):
                for val in (gem, gem.title() if gem else ''):
                    if val:
                        add_unique(out,ep,{key:val})
                        add_unique(out,ep,{'page':1,'pageSize':5,key:val})
                        add_unique(out,ep,{'client_id':CLIENT,'page':1,'pageSize':5,key:val})
    elif fam=='ring':
        for base in ({},{'page':1,'pageSize':5},{'type':'Preset Ring'},{'type':'Preset Ring','page':1,'pageSize':5}):
            add_unique(out,'/ring/v2',base)
        for skey in ('stoneType','gemType','gem_type'):
            for val in (gem,gem.title() if gem else ''):
                if val:
                    add_unique(out,'/ring/v2',{'type':'Preset Ring',skey:val})
                    add_unique(out,'/ring/v2',{'type':'Preset Ring',skey:val,'page':1,'pageSize':5})
    elif fam=='earring':
        for ep in ('/v3/preset-earring','/v3/preset-earrings','/preset-earring'):
            for base in ({},{'client_id':CLIENT},{'page':1,'pageSize':5},{'client_id':CLIENT,'page':1,'pageSize':5},{'type':'Preset Earring'}):add_unique(out,ep,base)
            for key in ('gem_type','gemType','stoneType'):
                if gem:
                    add_unique(out,ep,{key:gem})
                    add_unique(out,ep,{'client_id':CLIENT,'page':1,'pageSize':5,key:gem})
    elif fam=='pendant':
        for ep in ('/v3/preset-pendant','/preset-pendant'):
            for base in ({},{'client_id':CLIENT},{'page':1,'pageSize':5},{'client_id':CLIENT,'page':1,'pageSize':5},{'type':'Preset Pendant'}):add_unique(out,ep,base)
            for key in ('gem_type','gemType','stoneType'):
                if gem:
                    add_unique(out,ep,{key:gem})
                    add_unique(out,ep,{'client_id':CLIENT,'page':1,'pageSize':5,key:gem})
    elif fam=='bracelet':
        for ep in ('/v3/preset-bracelet','/preset-bracelet'):
            for base in ({},{'client_id':CLIENT},{'page':1,'pageSize':5},{'client_id':CLIENT,'page':1,'pageSize':5}):add_unique(out,ep,base)
            for key in ('gem_type','gemType','stoneType'):
                if gem:
                    add_unique(out,ep,{key:gem})
                    add_unique(out,ep,{'client_id':CLIENT,'page':1,'pageSize':5,key:gem})
    elif fam=='band':
        for ep in ('/v3/preset-band','/preset-band'):
            for base in ({},{'client_id':CLIENT},{'page':1,'pageSize':5},{'client_id':CLIENT,'page':1,'pageSize':5}):add_unique(out,ep,base)
            for key in ('stone_type','stoneType','gem_type','gemType'):
                if gem:
                    add_unique(out,ep,{key:gem.title()})
                    add_unique(out,ep,{'client_id':CLIENT,'page':1,'pageSize':5,key:gem.title()})
    return out

def cnt(r):
    try:return int(float((r.get('expected_products_if_detected') or '0').replace(',','')))
    except:return 0

def pick_representatives(targets):
    # Guarantee family coverage first, then add up to three high-count examples per family.
    byfam={}
    for r in targets:
        fam,_=infer(r); byfam.setdefault(fam,[]).append(r)
    picked=[]; seen=set()
    for fam,rows in sorted(byfam.items()):
        rows=sorted(rows,key=cnt,reverse=True)
        for r in rows[:3]:
            u=r['category_url']
            if u not in seen: picked.append(r);seen.add(u)
    # Add a few overall high-count cases without allowing one family to crowd out others.
    for r in sorted(targets,key=cnt,reverse=True):
        if len(picked)>=30:break
        if r['category_url'] not in seen: picked.append(r);seen.add(r['category_url'])
    return picked[:30]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline-coverage',required=True);ap.add_argument('--exclude-root',action='append',default=[]);ap.add_argument('--output',required=True);a=ap.parse_args()
    base=read_csv(a.baseline_coverage); exc=exclusions(a.exclude_root)
    targets=[r for r in base if r.get('category_url') and r.get('status')!='COMPLETE' and r['category_url'] not in exc]
    picked=pick_representatives(targets)
    sess=requests.Session();sess.headers.update({'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Origin':'https://www.gemsny.com','Referer':'https://www.gemsny.com/'})
    results=[]
    for r in picked:
        fam,gem=infer(r)
        for ep,p in probes(r):
            try:
                resp=sess.get(BASE+ep,params=p,timeout=30)
                body=resp.text[:2200]
                rec={'category_name':r.get('category_name'),'category_url':r['category_url'],'family':fam,'gem':gem,'expected':cnt(r),'endpoint':ep,'status':resp.status_code,'request_url':resp.url,'params':p,'body':body}
                if resp.status_code==200:
                    try:
                        obj=resp.json(); rec['json_keys']=list(obj)[:40] if isinstance(obj,dict) else ['<list>']; rec['json_sample']=json.dumps(obj,ensure_ascii=False)[:5000]
                    except Exception as e:rec['json_error']=repr(e)
                results.append(rec)
                print(json.dumps(rec,ensure_ascii=False),flush=True)
            except Exception as e:
                results.append({'category_name':r.get('category_name'),'category_url':r['category_url'],'family':fam,'gem':gem,'expected':cnt(r),'endpoint':ep,'status':'EXCEPTION','params':p,'body':repr(e)})
    summary={}
    for r in picked:
        fam,_=infer(r);summary[fam]=summary.get(fam,0)+1
    Path(a.output).write_text(json.dumps({'targets_total':len(targets),'sampled':len(picked),'sampled_by_family':summary,'results':results},indent=2,ensure_ascii=False),encoding='utf-8')

if __name__=='__main__': main()
