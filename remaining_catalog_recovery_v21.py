"""GemsNY unresolved-category recovery V21.

V20 proved pagination pairing alone was not enough: many unresolved endpoints
returned 400 for over-specified contracts, while /gemstone returned 200 responses
whose actual product list was not always selected by the generic recursive
flattening logic. V21 preserves all previously validated work and fixes both
failure classes without weakening validation.

Strict gates remain unchanged: a product category is accepted only when the
unique enumerated product count exactly matches the baseline expectation, every
accepted product has an original/source image, and source-image dimensions are
measured against >=500x500.
"""
import json
import re
import time

import remaining_api_recovery_v4 as v4
import remaining_catalog_recovery_v20 as v20


def _all_lists(obj):
    out=[]
    def walk(x):
        if isinstance(x,list):
            out.append(x)
            for y in x:
                if isinstance(y,(dict,list)): walk(y)
        elif isinstance(x,dict):
            for y in x.values():
                if isinstance(y,(dict,list)): walk(y)
    walk(obj)
    return out


def _product_score(rows):
    if not rows or not isinstance(rows,list): return (-1,-1,-1)
    dicts=[d for d in rows if isinstance(d,dict)]
    if not dicts: return (-1,-1,len(rows))
    ident=0; media=0; urls=0
    for d in dicts[:200]:
        if v4.key_of(d): ident+=1
        if v20.v18.robust_source_image(d): media+=1
        if v4.product_url(d): urls+=1
    # Product identity is strongest, then source media / detail URL, then length.
    return (ident*10 + media*5 + urls*3, media+urls, len(dicts))


def smart_flatten(obj):
    lists=_all_lists(obj)
    if not lists: return []
    return max(lists,key=_product_score)


v4.flatten=smart_flatten


def _dedupe(items):
    out=[]; seen=set()
    for ep,p,pk,sk in items:
        sig=(ep,json.dumps(p,sort_keys=True,separators=(',',':')),pk or '',sk or '')
        if sig not in seen:
            seen.add(sig);out.append((ep,p,pk,sk))
    return out


def _gem_values(gem):
    if not gem: return []
    raw=gem.replace('-',' ').strip()
    vals=[gem,raw,raw.title()]
    # Lab endpoints sometimes encode the material separately from lab-grown state;
    # broad candidates are safe because exact expected-count validation remains.
    if raw.startswith('lab '):
        base=raw[4:]
        vals += [base,base.title(),'Lab '+base.title()]
    return list(dict.fromkeys(v for v in vals if v))


def candidates(row):
    gem,typ,loose,style=v4.infer(row)
    out=[]
    # Keep every URL-filtered V17 candidate first.
    for ep,p,pk,sk in v20.v17.candidates(row):
        out.append((ep,p,pk,sk))
        clean={k:v for k,v in p.items() if v not in ('',None,[],{})}
        out.append((ep,clean,pk,sk))

    vals=_gem_values(gem)
    if loose:
        for ep in ('/gemstone','/gemstone/v2','/v2/gemstone','/v3/gemstone'):
            for val in vals:
                for key in ('type','gem_type','gemType','stone_type','stoneType'):
                    out.append((ep,{key:val},'page','pageSize'))
                    out.append((ep,{'client_id':v4.CLIENT,key:val},'page','pageSize'))

    preset={
        'earring':(('/v3/preset-earring','/preset-earring'),'Preset Earring'),
        'pendant':(('/v3/preset-pendant','/preset-pendant'),'Preset Pendant'),
        'bracelet':(('/v3/preset-bracelet','/preset-bracelet'),'Preset Bracelet'),
    }
    if typ in preset:
        eps,typeval=preset[typ]
        for ep in eps:
            # First test endpoint-required context with no guessed gemstone filter.
            out += [
                (ep,{},'page','pageSize'),
                (ep,{'client_id':v4.CLIENT},'page','pageSize'),
                (ep,{'type':typeval},'page','pageSize'),
                (ep,{'client_id':v4.CLIENT,'type':typeval},'page','pageSize'),
            ]
            for val in vals:
                for key in ('gem_type','gemType','stone_type','stoneType'):
                    for base in ({key:val},{'client_id':v4.CLIENT,key:val},{'type':typeval,key:val},{'client_id':v4.CLIENT,'type':typeval,key:val}):
                        out.append((ep,base,'page','pageSize'))
    if typ=='ring':
        for val in vals:
            for key in ('stoneType','gemType','gem_type','stone_type'):
                out.append(('/ring/v2',{'type':'Preset Ring',key:val},'page','pageSize'))
                if style:
                    out.append(('/ring/v2',{'type':'Preset Ring',key:val,'style':style.replace('-',' ').title()},'page','pageSize'))
    return _dedupe(out)


v4.candidates=candidates


def _usable(items):
    n=0
    for d in items[:40]:
        if not isinstance(d,dict): continue
        if (v4.key_of(d) or v4.product_url(d)) and v20.v18.robust_source_image(d): n+=1
    return n


def flexible_probe(sess,row,expected):
    diagnostics=[]
    for ep,params,pagekey,sizekey in candidates(row):
        base={k:v for k,v in params.items() if v not in ('',None,[],{})}
        variants=[base]
        # Do not force pageSize into the initial contract. Several GemsNY endpoints
        # reject unknown pagination fields with 400 even though the base contract is valid.
        for pk in (pagekey,'page','pageNo','pageNumber','currentPage'):
            if pk:
                p=dict(base);p[pk]=1;variants.append(p)
        for pk,sk in ((pagekey,sizekey),('page','pageSize'),('page','page_size'),('page','limit')):
            if pk and sk:
                for n in (20,50,91,100):
                    p=dict(base);p[pk]=1;p[sk]=n;variants.append(p)
        seen=set()
        for p in variants:
            sig=json.dumps(p,sort_keys=True,separators=(',',':'))
            if sig in seen: continue
            seen.add(sig)
            try:
                r=sess.get(v4.BASE+ep,params=p,timeout=35)
                diagnostics.append(f'{ep}:{r.status_code}')
                if r.status_code!=200: continue
                obj=r.json();items=smart_flatten(obj);cnt=v4.count_of(obj)
                if not items or _usable(items)==0:
                    diagnostics.append(f'{ep}:no-usable-products')
                    continue
                # Count equality is deliberately validated after complete enumeration;
                # rejecting here previously discarded real contracts with broad totals.
                return ep,p,pagekey,sizekey,cnt,diagnostics
            except Exception as e:
                diagnostics.append(f'{ep}:{type(e).__name__}')
    return None,None,None,None,0,diagnostics


v4.probe=flexible_probe


def flexible_enumerate(sess,sel,expected):
    ep,seed,pagekey,sizekey,cnt,_=sel
    try:
        obj,items=v20._request(sess,ep,seed)
    except Exception:
        return {},1
    seen={}
    def absorb(rows):
        new=0
        for d in rows:
            if not isinstance(d,dict): continue
            iu=v20.v18.robust_source_image(d)
            k=v20._identity(d) or (v4.product_url(d)+'|'+iu).strip('|')
            if not k or not iu: continue
            if k not in seen: seen[k]=d;new+=1
        return new
    absorb(items)
    if expected and len(seen)==expected: return seen,1

    # Strip any seed pagination fields before empirical pager discovery so they do
    # not pin all subsequent probes to page 1.
    base=dict(seed)
    pagelike=re.compile(r'^(page|page_no|pageno|pagenumber|currentpage|pageindex|offset|skip|start|from|first|position)$',re.I)
    sizelike=re.compile(r'^(pagesize|page_size|limit|perpage|per_page|size|take|length|rows|itemsperpage|items_per_page)$',re.I)
    for k in list(base):
        if pagelike.match(k) or sizelike.match(k): base.pop(k,None)

    pager=v20._discover_pager(sess,ep,base,pagekey,sizekey,items)
    if not pager: return seen,1
    mode,key,sk,stride=pager
    requests=1;step=2;repeats=0
    reported=v4.count_of(obj) or cnt or 0
    basis=max(reported or 0,expected or 0,len(seen))
    effective=max(1,stride if mode=='offset' else len(items))
    max_requests=max(25,min(100000,(basis+effective-1)//effective+20))
    while requests<max_requests:
        p=dict(base)
        if mode=='page': p[key]=step
        else: p[key]=(step-1)*stride
        if sk: p[sk]=100
        try:
            o,rows=v20._request(sess,ep,p)
        except Exception:
            break
        requests+=1
        if not rows: break
        new=absorb(rows)
        repeats=repeats+1 if new==0 else 0
        rep=v4.count_of(o) or reported
        if rep: reported=rep
        if expected and len(seen)>=expected: break
        if reported and not expected and len(seen)>=reported: break
        if repeats>=2: break
        step+=1;time.sleep(.05)
    return seen,requests


v4.enumerate_api=flexible_enumerate
v4.img_of=v20.v18.robust_source_image

if __name__=='__main__':
    v4.main()
