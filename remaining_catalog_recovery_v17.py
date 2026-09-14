"""Strict GemsNY unresolved-category recovery V17.

Extends V16 with category-URL derived filters so shape/origin/style-specific
categories are not compared against broad gem/family endpoints. It still accepts
a category only after full enumeration exactly matches the baseline expected
product count. Original/source image selection and >=500x500 validation remain
unchanged.
"""
import json,re
from urllib.parse import urlparse,parse_qs
import remaining_api_recovery_v4 as v4
import remaining_catalog_recovery_v14 as v14
import remaining_catalog_recovery_v15 as v15
import remaining_catalog_recovery_v16 as v16

BASE_CANDIDATES=v16.contract_candidates

SHAPES={
 'round':'Round','oval':'Oval','cushion':'Cushion','emerald':'Emerald','pear':'Pear',
 'heart':'Heart','marquise':'Marquise','asscher':'Asscher','princess':'Princess',
 'radiant':'Radiant','trillion':'Trillion','square':'Square'
}
ORIGINS={
 'brazilian':'Brazil','brazil':'Brazil','indian':'India','india':'India',
 'ceylon':'Ceylon','sri-lanka':'Sri Lanka','srilanka':'Sri Lanka','burma':'Burma',
 'burmese':'Burma','madagascar':'Madagascar','mozambique':'Mozambique',
 'tanzania':'Tanzania','zambia':'Zambia','colombia':'Colombia','colombian':'Colombia'
}
STYLE_VALUES={
 'halo':['Halo'],'solitaire':['Solitaire'],'sidestone':['Side Stone','Sidestone','Side-Stone'],
 'side-stone':['Side Stone','Sidestone','Side-Stone'],'three-stone':['Three Stone','Three-Stone'],
 'two-stone':['Two Stone','Two-Stone'],'vintage':['Vintage'],'dangle':['Dangle'],
 'hoop':['Hoop'],'stud':['Stud'],'half-eternity':['Half Eternity','Half-Eternity'],
 'three-fourth-eternity':['Three Fourth Eternity','Three-Fourth Eternity']
}


def dedupe(xs):
    out=[];seen=set()
    for ep,p,pk,sk in xs:
        sig=(ep,json.dumps(p,sort_keys=True),pk,sk)
        if sig not in seen:
            seen.add(sig);out.append((ep,p,pk,sk))
    return out


def url_filters(row):
    u=urlparse(row['category_url']); toks=[x for x in u.path.lower().split('/') if x]
    shape=next((SHAPES[t] for t in toks if t in SHAPES),'')
    origin=next((ORIGINS[t] for t in toks if t in ORIGINS),'')
    style=[]
    joined='-'.join(toks)
    for k,vals in STYLE_VALUES.items():
        if k in joined or k.replace('-',' ') in (row.get('category_name') or '').lower():
            style.extend(vals)
    q=parse_qs(u.query)
    myo=(q.get('myo') or [''])[0].lower()
    return shape,origin,list(dict.fromkeys(style)),myo


def candidates(row):
    gem,typ,loose,style=v4.infer(row); shape,origin,styles,myo=url_filters(row)
    out=list(BASE_CANDIDATES(row))
    gemvals=[]
    if gem:
        for x in (gem,gem.replace('-',' ').title(),gem.replace('-',' ')):
            if x and x not in gemvals:gemvals.append(x)

    def add(ep,p,pk='page',sk='pageSize'): out.append((ep,p,pk,sk))

    if loose:
        # Probe only plausible, documented-looking URL-derived filters. The API
        # rejects unsupported keys quickly; strict post-enumeration equality is
        # still the acceptance gate.
        for gv in gemvals:
            base={'type':gv}
            if shape:
                for k in ('shape','stoneShape'):
                    add('/gemstone',{**base,k:shape})
            if origin:
                for k in ('origin','country','stoneOrigin'):
                    add('/gemstone',{**base,k:origin})
            if shape and origin:
                for skey in ('shape','stoneShape'):
                    for okey in ('origin','country','stoneOrigin'):
                        add('/gemstone',{**base,skey:shape,okey:origin})
            # MYO query pages normally use the same loose inventory, but try
            # explicit usage keys where the backend supports them.
            if myo:
                for k in ('myo','usage','jewelryType'):
                    for mv in (myo,myo.rstrip('s').title(),myo.title()):
                        add('/gemstone',{**base,k:mv})

    if typ=='ring':
        for gv in gemvals:
            base={'type':'Preset Ring','stoneType':gv}
            if shape:
                for k in ('centerStoneShape','shape'):
                    add('/ring/v2',{**base,k:shape})
            for sv in styles:
                add('/ring/v2',{**base,'style':sv})
                if shape:
                    add('/ring/v2',{**base,'style':sv,'centerStoneShape':shape})

    if typ in ('earring','pendant','bracelet'):
        eps={'earring':('/v3/preset-earring','/preset-earring'),
             'pendant':('/v3/preset-pendant','/preset-pendant'),
             'bracelet':('/v3/preset-bracelet','/preset-bracelet')}[typ]
        for ep in eps:
            for gv in gemvals:
                base={'gem_type':gv}
                if shape:
                    add(ep,{**base,'shape':shape})
                for sv in styles:
                    for k in ('style','category'):
                        add(ep,{**base,k:sv})
                        if shape:add(ep,{**base,k:sv,'shape':shape})
    return dedupe(out)


# Expanded explicit non-product routes. They remain accounted work units, but
# are not fabricated as product categories.
v4.NON_PRODUCT_RE=re.compile(r'/(?:accessibility|affiliates|about|privacy|terms|contact|education|blog|faq|returns|shipping|warranty|appraisal|track-order|order-status|refer|careers|press|reviews|financing|appointment|gemologist|login|register|best-price-guarantee|birthstone|compare-stone|custom-jewelry|astrological)(?:/|$)',re.I)
v4.candidates=candidates
v4.probe=v15.strict_probe
v4.enumerate_api=v15.strict_enumerate_api
v4.img_of=v14.source_image

if __name__=='__main__':
    v4.main()
