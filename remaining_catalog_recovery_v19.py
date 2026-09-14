"""GemsNY unresolved-category recovery V19.

Builds on V18 but fixes a structural pagination-contract problem: several GemsNY
endpoints return a capped first page (commonly 91 rows) while ignoring the
assumed `page` parameter. V19 empirically discovers a working pagination control
for each selected endpoint by comparing page/offset variants against page 1.

It preserves V18's robust original/source image resolver, exact post-enumeration
category-count gate, and >=500x500 source-image validation. It never accepts a
partial category merely because an API reported a total.
"""
import math,time

import remaining_api_recovery_v4 as v4
import remaining_catalog_recovery_v14 as v14
import remaining_catalog_recovery_v17 as v17
import remaining_catalog_recovery_v18 as v18

# Keep V18 source-image discovery and V17 URL-filtered contract candidates.
v14.source_image=v18.robust_source_image
v4.img_of=v18.robust_source_image
v4.candidates=v17.candidates
v4.probe=v17.v15.strict_probe


def _item_keys(items):
    out=[]
    for d in items:
        iu=v14.source_image(d)
        k=v4.key_of(d) or (v4.product_url(d)+'|'+iu).strip('|')
        if k and iu:
            out.append(k)
    return out


def _request(sess,ep,params,timeout=60):
    r=sess.get(v4.BASE+ep,params=params,timeout=timeout)
    r.raise_for_status()
    obj=r.json()
    return obj,v4.flatten(obj)


def _discover_pager(sess,ep,base,pagekey,sizekey,first_items):
    """Return (mode,key,size_key,stride) for a request that advances results.

    mode=page uses ordinal pages; mode=offset uses zero-based row offsets.
    Existing contract keys are tested first, followed by common backend names.
    The winner is the candidate yielding the most product keys not seen on page 1.
    """
    first=set(_item_keys(first_items))
    if not first:
        return ('page',pagekey,sizekey,max(1,len(first_items)))
    page_keys=[]
    for k in (pagekey,'page','pageNo','page_no','pageNumber','page_number','currentPage','current_page'):
        if k and k not in page_keys: page_keys.append(k)
    size_keys=[]
    for k in (sizekey,'pageSize','page_size','limit','perPage','per_page','size'):
        if k and k not in size_keys: size_keys.append(k)
    offset_keys=['offset','skip','start','from','startIndex','start_index']
    observed=max(1,len(first_items))
    best=(0,None)

    # Keep the probe bounded: one sensible size key per page key first, then
    # offset controls. Many endpoints tolerate unknown query params but ignore them.
    sk=size_keys[0] if size_keys else sizekey
    for pk in page_keys:
        p=dict(base); p[pk]=2
        if sk: p[sk]=100
        try:
            _,items=_request(sess,ep,p,35)
            new=len(set(_item_keys(items))-first)
            if new>best[0]: best=(new,('page',pk,sk,observed))
        except Exception:
            pass
    for ok in offset_keys:
        for stride in (observed,100):
            p=dict(base); p[ok]=stride
            if sk: p[sk]=100
            try:
                _,items=_request(sess,ep,p,35)
                new=len(set(_item_keys(items))-first)
                if new>best[0]: best=(new,('offset',ok,sk,stride))
            except Exception:
                pass
    return best[1] or ('page',pagekey,sizekey,observed)


def adaptive_enumerate(sess,sel,expected):
    ep,params,pagekey,sizekey,cnt,_=sel
    firstp=dict(params); firstp[pagekey]=1; firstp[sizekey]=100
    obj,items=_request(sess,ep,firstp)
    if not items:
        return {},1

    seen={}
    def absorb(rows):
        new=0
        for d in rows:
            iu=v14.source_image(d)
            k=v4.key_of(d) or (v4.product_url(d)+'|'+iu).strip('|')
            if not k or not iu: continue
            if k not in seen:
                seen[k]=d; new+=1
        return new
    absorb(items)

    reported=v4.count_of(obj) or cnt or 0
    pager=_discover_pager(sess,ep,params,pagekey,sizekey,items)
    mode,key,sk,stride=pager
    repeats=0; step=2; requests=1
    # Dynamic ceiling only prevents pathological infinite loops; it is not a
    # category/page coverage cap. It scales from the reported/expected workload.
    basis=max(reported or 0,expected or 0,len(seen))
    max_requests=max(20,min(100000,math.ceil(max(1,basis)/max(1,stride))+10))

    while requests<max_requests:
        p=dict(params)
        if mode=='page':
            p[key]=step
        else:
            p[key]=(step-1)*stride
        if sk: p[sk]=100
        try:
            o,rows=_request(sess,ep,p)
        except Exception:
            break
        requests+=1
        if not rows: break
        new=absorb(rows)
        repeats = repeats+1 if new==0 else 0
        rep=v4.count_of(o) or reported
        if rep: reported=rep
        if reported and len(seen)>=reported: break
        if repeats>=2: break
        step+=1
        time.sleep(.08)
    return seen,requests


v4.enumerate_api=adaptive_enumerate

if __name__=='__main__':
    v4.main()
