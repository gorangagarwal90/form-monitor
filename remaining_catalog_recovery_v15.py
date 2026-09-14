"""Strict GemsNY unresolved-category recovery V15.

Builds on the family-specific API contracts from remaining_api_recovery_v4,
but fixes two correctness issues:
1) contract discovery must not reject a product-bearing response merely because
   its first-page reported total differs from the baseline category count;
2) enumeration must not stop when it happens to reach the expected count.
   The complete API result set is enumerated first, and v4's existing strict
   post-enumeration equality check decides whether the category is complete.

Original/source image selection is delegated to V14's source_image resolver,
which strips resize parameters and prefers original/source/main media fields.
"""
import time
import remaining_api_recovery_v4 as v4
import remaining_catalog_recovery_v14 as v14


def strict_probe(sess, row, expected):
    diagnostics=[]
    best=None
    for ep,params,pagekey,sizekey in v4.candidates(row):
        p=dict(params); p[pagekey]=1; p[sizekey]=100
        try:
            r=sess.get(v4.BASE+ep,params=p,timeout=35)
            diagnostics.append(f'{ep}:{r.status_code}')
            if r.status_code!=200:
                continue
            obj=r.json(); items=v4.flatten(obj); cnt=v4.count_of(obj)
            if not items:
                continue
            usable=sum(1 for d in items[:30] if v4.key_of(d) and v14.source_image(d))
            if usable==0:
                diagnostics.append(f'{ep}:no-usable-products')
                continue
            # Prefer an exact reported total when available, but do not require
            # it during discovery. Exactness is enforced after full enumeration.
            score=(1 if expected and cnt==expected else 0, usable, -abs((cnt or len(items))-(expected or (cnt or len(items)))))
            candidate=(score,(ep,params,pagekey,sizekey,cnt,diagnostics.copy()))
            if best is None or candidate[0] > best[0]:
                best=candidate
        except Exception as e:
            diagnostics.append(f'{ep}:{type(e).__name__}')
    if best:
        ep,params,pk,sk,cnt,_=best[1]
        return ep,params,pk,sk,cnt,diagnostics
    return None,None,None,None,0,diagnostics


def strict_enumerate_api(sess, sel, expected):
    ep,params,pagekey,sizekey,cnt,_=sel
    seen={}; page=1; repeats=0
    # Never stop simply because len(seen) reaches expected. A broad endpoint
    # could otherwise create a false exact-count success.
    while page<=10000:
        p=dict(params); p[pagekey]=page; p[sizekey]=100
        r=sess.get(v4.BASE+ep,params=p,timeout=60); r.raise_for_status()
        obj=r.json(); items=v4.flatten(obj)
        if not items:
            break
        new=0
        for d in items:
            iu=v14.source_image(d)
            k=v4.key_of(d) or (v4.product_url(d)+'|'+iu).strip('|')
            if not k or not iu:
                continue
            if k not in seen:
                seen[k]=d; new+=1
        repeats = repeats+1 if new==0 else 0
        if cnt and len(seen)>=cnt:
            break
        # A short page is a natural terminal page regardless of expected count.
        if len(items)<100:
            break
        if repeats>=2:
            break
        page+=1; time.sleep(.08)
    return seen,page


# Patch the proven v4 driver with strict discovery/enumeration and source-image
# selection. Its main() already preserves prior completed coverage, validates
# exact product counts, emits per-category evidence, checks dimensions, and
# exits non-zero whenever unresolved coverage remains.
v4.probe=strict_probe
v4.enumerate_api=strict_enumerate_api
v4.img_of=v14.source_image

if __name__=='__main__':
    v4.main()
