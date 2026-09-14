"""GemsNY unresolved-category recovery V20.

V19 proved that many remaining GemsNY endpoints return a valid first batch but
ignore the assumed page/pageSize pair. V20 preserves every previously validated
category and broadens pagination discovery without weakening the strict gates:
exact expected product count, original/source image discovery, and >=500x500
source-image validation.

Key V20 change: pagination is discovered as a *pair* of position and page-size
controls. V19 tested many position keys but only one size key; several backends
ignore pagination unless the matching size/limit control is supplied. V20 tests
page and offset families against multiple size-key variants (and no size key),
then enumerates only with a variant that actually produces new product IDs.
"""
import math,time

import remaining_api_recovery_v4 as v4
import remaining_catalog_recovery_v14 as v14
import remaining_catalog_recovery_v17 as v17
import remaining_catalog_recovery_v18 as v18

v14.source_image=v18.robust_source_image
v4.img_of=v18.robust_source_image
v4.candidates=v17.candidates
v4.probe=v17.v15.strict_probe


def _identity(d):
    """Use product identity for pagination discovery; image availability is not
    required merely to prove that a request advanced to a different batch."""
    return v4.key_of(d) or v4.product_url(d)


def _item_keys(items):
    return [k for k in (_identity(d) for d in items) if k]


def _request(sess,ep,params,timeout=60):
    r=sess.get(v4.BASE+ep,params=params,timeout=timeout)
    r.raise_for_status()
    obj=r.json()
    return obj,v4.flatten(obj)


def _discover_pager(sess,ep,base,pagekey,sizekey,first_items):
    first=set(_item_keys(first_items))
    if not first:
        return None

    page_keys=[]
    for k in (pagekey,'page','pageNo','page_no','pageNumber','page_number',
              'currentPage','current_page','pageIndex','page_index'):
        if k and k not in page_keys: page_keys.append(k)
    offset_keys=[]
    for k in ('offset','skip','start','from','startIndex','start_index','first','position'):
        if k not in offset_keys: offset_keys.append(k)
    size_keys=[]
    for k in (sizekey,'pageSize','page_size','limit','perPage','per_page','size',
              'take','length','rows','itemsPerPage','items_per_page'):
        if k and k not in size_keys: size_keys.append(k)
    # Some APIs paginate correctly only when no explicit size key is sent.
    size_variants=[None]+size_keys
    observed=max(1,len(first_items))
    strides=[]
    for n in (observed,20,24,30,36,48,50,60,72,90,91,96,100):
        if n>0 and n not in strides: strides.append(n)

    best=(0,None)
    # Probe ordinal page controls. Try both common 1-based and alternate values;
    # selection is based on actual new product identities, not HTTP status alone.
    for pk in page_keys:
        for sk in size_variants:
            for page_value in (2,3,0):
                p=dict(base); p[pk]=page_value
                if sk: p[sk]=100
                try:
                    _,items=_request(sess,ep,p,30)
                    new=len(set(_item_keys(items))-first)
                    if new>best[0]: best=(new,('page',pk,sk,1))
                    if new>=max(1,min(10,len(first)//5)):
                        break
                except Exception:
                    pass
    # Probe offset-style controls using multiple plausible backend strides and
    # size-key pairings. This is intentionally broader than V19 but bounded.
    for ok in offset_keys:
        for sk in size_variants:
            for stride in strides:
                p=dict(base); p[ok]=stride
                if sk: p[sk]=100
                try:
                    _,items=_request(sess,ep,p,30)
                    new=len(set(_item_keys(items))-first)
                    if new>best[0]: best=(new,('offset',ok,sk,stride))
                    if new>=max(1,min(10,len(first)//5)):
                        break
                except Exception:
                    pass
    return best[1]


def adaptive_enumerate(sess,sel,expected):
    ep,params,pagekey,sizekey,cnt,_=sel

    # Establish the reference batch without forcing a potentially wrong size key.
    first_candidates=[]
    for sk in (sizekey,None,'limit','page_size','pageSize'):
        p=dict(params); p[pagekey]=1
        if sk: p[sk]=100
        try:
            obj,items=_request(sess,ep,p)
            if items:
                first_candidates.append((obj,items,p))
        except Exception:
            pass
    if not first_candidates:
        return {},1
    # Prefer the largest usable batch, reducing requests for large categories.
    obj,items,firstp=max(first_candidates,key=lambda x:len(x[1]))

    seen={}
    def absorb(rows):
        new=0
        for d in rows:
            iu=v14.source_image(d)
            k=_identity(d) or (v4.product_url(d)+'|'+iu).strip('|')
            if not k or not iu: continue
            if k not in seen:
                seen[k]=d; new+=1
        return new
    absorb(items)

    reported=v4.count_of(obj) or cnt or 0
    pager=_discover_pager(sess,ep,params,pagekey,sizekey,items)
    if not pager:
        # No empirically advancing contract: return only proven rows. Strict
        # caller count validation will keep this category unresolved rather than
        # falsely accepting a partial first batch.
        return seen,1

    mode,key,sk,stride=pager
    repeats=0; step=2; requests=1
    basis=max(reported or 0,expected or 0,len(seen))
    effective_stride=max(1,stride if mode=='offset' else len(items))
    max_requests=max(25,min(100000,math.ceil(max(1,basis)/effective_stride)+15))

    while requests<max_requests:
        p=dict(params)
        if mode=='page': p[key]=step
        else: p[key]=(step-1)*stride
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
        if expected and len(seen)>=expected: break
        if reported and len(seen)>=reported: break
        if repeats>=2: break
        step+=1
        time.sleep(.06)
    return seen,requests


v4.enumerate_api=adaptive_enumerate

if __name__=='__main__':
    v4.main()
