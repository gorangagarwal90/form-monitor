"""Strict GemsNY unresolved-category recovery V17.1.

Extends V16 with category-URL derived filters and fixes a critical pagination
bug: GemsNY APIs may cap a requested pageSize=100 to fewer rows (commonly 91).
A short first page therefore does NOT prove end-of-results. Enumeration now
continues until the API reports its total, an empty page is reached, or repeated
pages prove that pagination is not advancing. Exact post-enumeration product
count remains the acceptance gate. Original/source images must still be >=500x500.
"""
import json,re,time,math
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
    style=[]; joined='-'.join(toks)
    for k,vals in STYLE_VALUES.items():
        if k in joined or k.replace('-',' ') in (row.get('category_name') or '').lower():
            style.extend(vals)
    q=parse_qs(u.query); myo=(q.get('myo') or [''])[0].lower()
    return shape,origin,list(dict.fromkeys(style)),myo

def candidates(row):
    gem,typ,loose,style=v4.infer(row); shape,origin,styles,myo=url_filters(row)
    out=list(BASE_CANDIDATES(row)); gemvals=[]
    if gem:
        for x in (gem,gem.replace('-',' ').title(),gem.replace('-',' ')):
            if x and x not in gemvals: gemvals.append(x)
    def add(ep,p,pk='page',sk='pageSize'): out.append((ep,p,pk,sk))
    if loose:
        for gv in gemvals:
            base={'type':gv}
            if shape:
                for k in ('shape','stoneShape'): add('/gemstone',{**base,k:shape})
            if origin:
                for k in ('origin','country','stoneOrigin'): add('/gemstone',{**base,k:origin})
            if shape and origin:
                for skey in ('shape','stoneShape'):
                    for okey in ('origin','country','stoneOrigin'):
                        add('/gemstone',{**base,skey:shape,okey:origin})
            if myo:
                for k in ('myo','usage','jewelryType'):
                    for mv in (myo,myo.rstrip('s').title(),myo.title()): add('/gemstone',{**base,k:mv})
    if typ=='ring':
        for gv in gemvals:
            base={'type':'Preset Ring','stoneType':gv}
            if shape:
                for k in ('centerStoneShape','shape'): add('/ring/v2',{**base,k:shape})
            for sv in styles:
                add('/ring/v2',{**base,'style':sv})
                if shape: add('/ring/v2',{**base,'style':sv,'centerStoneShape':shape})
    if typ in ('earring','pendant','bracelet'):
        eps={'earring':('/v3/preset-earring','/preset-earring'),
             'pendant':('/v3/preset-pendant','/preset-pendant'),
             'bracelet':('/v3/preset-bracelet','/preset-bracelet')}[typ]
        for ep in eps:
            for gv in gemvals:
                base={'gem_type':gv}
                if shape: add(ep,{**base,'shape':shape})
                for sv in styles:
                    for k in ('style','category'):
                        add(ep,{**base,k:sv})
                        if shape: add(ep,{**base,k:sv,'shape':shape})
    return dedupe(out)

def enumerate_api_no_short_page(sess, sel, expected):
    ep,params,pagekey,sizekey,cnt,_=sel
    seen={}; page=1; repeats=0; observed_page_size=0
    # Dynamic ceiling avoids an artificial page/category limit. For ordinary
    # categories it stays small; for very large reported totals it scales to the
    # actual work required. Repeated-page detection prevents infinite loops when
    # a backend ignores the page parameter.
    max_pages=100000
    while page<=max_pages:
        p=dict(params); p[pagekey]=page; p[sizekey]=100
        r=sess.get(v4.BASE+ep,params=p,timeout=60); r.raise_for_status()
        obj=r.json(); items=v4.flatten(obj)
        if not items: break
        if page==1:
            observed_page_size=max(1,len(items))
            reported=v4.count_of(obj) or cnt or 0
            if reported:
                max_pages=max(3,min(100000,math.ceil(reported/observed_page_size)+3))
        new=0
        for d in items:
            iu=v14.source_image(d)
            k=v4.key_of(d) or (v4.product_url(d)+'|'+iu).strip('|')
            if not k or not iu: continue
            if k not in seen:
                seen[k]=d; new+=1
        repeats = repeats+1 if new==0 else 0
        reported=v4.count_of(obj) or cnt or 0
        if reported and len(seen)>=reported: break
        if repeats>=2: break
        page+=1; time.sleep(.08)
    return seen,page

v4.NON_PRODUCT_RE=re.compile(r'/(?:accessibility|affiliates|about|privacy|terms|contact|education|blog|faq|returns|shipping|warranty|appraisal|track-order|order-status|refer|careers|press|reviews|financing|appointment|gemologist|login|register|best-price-guarantee|birthstone|compare-stone|custom-jewelry|astrological)(?:/|$)',re.I)
v4.candidates=candidates
v4.probe=v15.strict_probe
v4.enumerate_api=enumerate_api_no_short_page
v4.img_of=v14.source_image

if __name__=='__main__':
    v4.main()
