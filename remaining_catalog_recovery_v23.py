"""GemsNY unresolved-category recovery V23.

V22 proved source-image normalization was only part of the remaining problem.
Several live endpoints return a broader family even when a URL-derived filter is
ignored (for example a style-specific ring URL returning the full gemstone ring
family). V23 preserves all previous strict gates and adds two safeguards:

1. Broader normalization for relative/bare image asset values found under image
   fields, still measuring the resolved original/source object at >=500x500.
2. Category-semantic post-filtering. When the returned product payload actually
   exposes a dimension (gem, shape, style, origin), only rows matching the URL's
   requested value are retained. This lets a broad backend response be reduced to
   the exact category without inventing products or weakening count validation.

Acceptance remains strict: after semantic filtering, the unique product count
must exactly match the baseline expected count when one exists. No category/page
limit is introduced.
"""
import json,re
from urllib.parse import urlparse,urljoin,parse_qsl,urlencode,urlunparse

import remaining_api_recovery_v4 as v4
import remaining_catalog_recovery_v17 as v17
import remaining_catalog_recovery_v20 as v20
import remaining_catalog_recovery_v21 as v21
import remaining_catalog_recovery_v22 as v22

ASSET='https://assets.gemsny.com/'


def source_image_v23(d):
    found=[]
    def norm(raw,path):
        s=(raw or '').strip()
        if not s:return ''
        if s.startswith('//'):s='https:'+s
        elif s.startswith('http://') or s.startswith('https://'):pass
        else:
            image_field=bool(re.search(r'image|photo|picture|media|gallery|asset|src|thumb',path,re.I))
            image_value=bool(re.search(r'\.(?:jpe?g|png|webp|avif)(?:\?|$)',s,re.I) or re.search(r'image-|images?/|media/|uploads?/',s,re.I))
            if not (image_field or image_value):return ''
            s=urljoin(ASSET,s.lstrip('/'))
        try:
            p=urlparse(s)
            q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
               if k.lower() not in {'width','height','w','h','quality','q','format','fit','crop','auto','dpr','resize'}]
            return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))
        except Exception:return s
    def walk(x,path=''):
        if isinstance(x,dict):
            for k,v in x.items():walk(v,(path+'.'+str(k)).lower())
        elif isinstance(x,list):
            for v in x[:120]:walk(v,path)
        elif isinstance(x,str):
            u=norm(x,path)
            if not u:return
            low=(path+' '+x+' '+u).lower()
            if re.search(r'logo|icon|badge|sprite|placeholder|loader|certificate|favicon',low):return
            score=0
            if re.search(r'original|source|master|full|zoom|large|main|primary',path,re.I):score+=16
            if re.search(r'image|photo|picture|media|gallery|asset|src',path,re.I):score+=7
            if re.search(r'image-(?:gemstone|jewelry)|/original/|/source/',u,re.I):score+=8
            if re.search(r'thumb|thumbnail|small|tiny|swatch',low,re.I):score-=12
            found.append((score,u))
    walk(d)
    return max(found,key=lambda x:x[0])[1] if found else ''


def blob(d):
    vals=[]
    def walk(x,k=''):
        if isinstance(x,dict):
            for kk,v in x.items():
                if not re.search(r'image|photo|picture|media|src|url',str(kk),re.I):walk(v,str(kk))
        elif isinstance(x,list):
            for v in x[:80]:walk(v,k)
        elif isinstance(x,(str,int,float,bool)):
            vals.append(str(x).lower().replace('_',' ').replace('-',' '))
    walk(d)
    return ' '.join(vals)

ALL_SHAPES=[x.lower() for x in v17.SHAPES.values()]
ALL_ORIGINS=[x.lower() for x in set(v17.ORIGINS.values())]
ALL_STYLES=sorted({x.lower().replace('-',' ') for xs in v17.STYLE_VALUES.values() for x in xs})
ALL_GEMS=['sapphire','ruby','emerald','alexandrite','tanzanite','aquamarine','tsavorite','morganite','peridot','tourmaline','topaz','amethyst','citrine','spinel','garnet','diamond']


def _mentions_any(t,vals):
    return any(re.search(r'(?<![a-z])'+re.escape(v)+r'(?![a-z])',t) for v in vals)


def semantic_filter(row,seen):
    if not seen:return seen
    gem,typ,loose,style=v4.infer(row)
    shape,origin,styles,myo=v17.url_filters(row)
    rows=list(seen.items()); texts={k:blob(d) for k,d in rows}
    dims=[]
    if gem:
        target=gem.replace('-',' ').replace('lab ','').strip()
        dims.append(('gem',[target],ALL_GEMS))
        if gem.startswith('lab-'):dims.append(('lab',['lab','lab grown','lab created','laboratory'],['lab','natural']))
    if shape:dims.append(('shape',[shape.lower()],ALL_SHAPES))
    if origin:dims.append(('origin',[origin.lower()],ALL_ORIGINS))
    if styles:dims.append(('style',[s.lower().replace('-',' ') for s in styles],ALL_STYLES))
    out=dict(seen)
    for _,targets,vocab in dims:
        exposed=sum(1 for t in texts.values() if _mentions_any(t,vocab))
        # Enforce a dimension only when payloads demonstrably expose it; otherwise
        # do not guess from missing metadata.
        if exposed < max(3,int(len(texts)*0.15)):continue
        keep={k:d for k,d in out.items() if any(re.search(r'(?<![a-z])'+re.escape(v)+r'(?![a-z])',texts[k]) for v in targets)}
        if keep:out=keep
    return out


def probe_v23(sess,row,expected):
    best=None; diagnostics=[]
    for ep,params,pk,sk in v21.candidates(row):
        base={k:v for k,v in params.items() if v not in ('',None,[],{})}
        variants=[base]
        p=dict(base);p[pk or 'page']=1;variants.append(p)
        seen_sig=set()
        for p in variants:
            sig=json.dumps(p,sort_keys=True,separators=(',',':'))
            if sig in seen_sig:continue
            seen_sig.add(sig)
            try:
                r=sess.get(v4.BASE+ep,params=p,timeout=30);diagnostics.append(f'{ep}:{r.status_code}')
                if r.status_code!=200:continue
                obj=r.json();items=v21.smart_flatten(obj);cnt=v4.count_of(obj)
                usable=[d for d in items if isinstance(d,dict) and (v4.key_of(d) or v4.product_url(d)) and source_image_v23(d)]
                if not usable:continue
                temp={str(i):d for i,d in enumerate(usable)};f=semantic_filter(row,temp)
                ratio=len(f)/max(1,len(temp))
                exact=1 if expected and cnt==expected else 0
                closeness=-abs((cnt or len(f))-(expected or (cnt or len(f))))
                score=(exact,ratio,closeness,len(f))
                cand=(score,(ep,p,pk,sk,cnt,diagnostics[:]))
                if best is None or cand[0]>best[0]:best=cand
                if exact and ratio>=.8:return cand[1]
            except Exception as e:diagnostics.append(f'{ep}:{type(e).__name__}')
    return best[1] if best else (None,None,None,None,0,diagnostics)


def enumerate_v23(sess,sel,expected):
    seen,req=v21.flexible_enumerate(sess,sel,expected)
    # row context is not part of the legacy enumerate signature, so semantic
    # filtering is injected in main through a small state set by probe_v23.
    row=getattr(enumerate_v23,'row',None)
    if row and seen:
        filtered=semantic_filter(row,seen)
        if expected and len(filtered)==expected:return filtered,req
        # Never replace a set that already exactly satisfies the strict gate.
        if not expected or len(seen)!=expected:
            seen=filtered
    return seen,req


# Remember the current category row for semantic post-filtering without changing
# the legacy recovery runner API.
def probe_with_state(sess,row,expected):
    enumerate_v23.row=row
    return probe_v23(sess,row,expected)

v20.v18.robust_source_image=source_image_v23
v4.img_of=source_image_v23
v21.v4.img_of=source_image_v23
v4.flatten=v21.smart_flatten
v4.candidates=v21.candidates
v4.probe=probe_with_state
v4.enumerate_api=enumerate_v23

if __name__=='__main__':v4.main()
