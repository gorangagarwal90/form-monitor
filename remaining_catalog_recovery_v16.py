"""Strict GemsNY unresolved-category recovery V16.

Preserves V15 validation but fixes backend contract selection using contracts that
were empirically confirmed by the API probe. In particular, loose gemstone
requests use /gemstone with the supported `type` filter and minimal parameters,
rather than the invalid guessed filter bundle that produced 400/404 responses.

No partial category is accepted: V15's full enumeration and exact post-enumeration
count validation remain in force, and original/source images are checked at
>=500x500.
"""
import json
import remaining_api_recovery_v4 as v4
import remaining_catalog_recovery_v14 as v14
import remaining_catalog_recovery_v15 as v15

_ORIGINAL_CANDIDATES = v4.candidates


def _dedupe(candidates):
    out=[]; seen=set()
    for ep,params,pagekey,sizekey in candidates:
        sig=(ep,json.dumps(params,sort_keys=True),pagekey,sizekey)
        if sig not in seen:
            seen.add(sig); out.append((ep,params,pagekey,sizekey))
    return out


def contract_candidates(row):
    gem,typ,loose,style=v4.infer(row)
    out=[]
    def add(ep,params,page='page',size='pageSize'):
        out.append((ep,params,page,size))

    vals=[]
    if gem:
        for x in (gem, gem.replace('-',' ').title(), gem.replace('-',' ')):
            if x and x not in vals: vals.append(x)

    # Confirmed by remaining API probe: /gemstone supports type + page/pageSize.
    # Keep requests minimal because extra guessed filters trigger whitelist errors.
    if loose:
        for x in vals:
            add('/gemstone', {'type':x})
            add('/gemstone', {'client_id':v4.CLIENT,'type':x})

    # Confirmed ring contract: Preset Ring + stoneType.
    if typ=='ring':
        for x in vals:
            add('/ring/v2', {'type':'Preset Ring','stoneType':x})
            add('/ring/v2', {'type':'Preset Ring','stoneType':x,'style':style.replace('-',' ').title() if style else ''})

    # Confirmed preset jewelry contracts use gem_type and tolerate page/pageSize.
    if typ=='earring':
        for ep in ('/v3/preset-earring','/preset-earring'):
            for x in vals:
                add(ep, {'gem_type':x})
                add(ep, {'client_id':v4.CLIENT,'gem_type':x})
    if typ=='pendant':
        for ep in ('/v3/preset-pendant','/preset-pendant'):
            for x in vals:
                add(ep, {'gem_type':x})
                add(ep, {'client_id':v4.CLIENT,'gem_type':x})
    if typ=='bracelet':
        for ep in ('/v3/preset-bracelet','/preset-bracelet'):
            for x in vals:
                add(ep, {'gem_type':x})
                add(ep, {'client_id':v4.CLIENT,'gem_type':x})

    # Append earlier candidates as fallback for already-proven category variants.
    out.extend(_ORIGINAL_CANDIDATES(row))
    return _dedupe(out)


v4.candidates=contract_candidates
v4.probe=v15.strict_probe
v4.enumerate_api=v15.strict_enumerate_api
v4.img_of=v14.source_image

if __name__=='__main__':
    v4.main()
