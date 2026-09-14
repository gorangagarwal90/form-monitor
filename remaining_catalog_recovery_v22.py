"""GemsNY unresolved-category recovery V22.

V21 still rejected many HTTP-200 product payloads as "no-usable-products".
Inspection of the existing browser recovery showed an important schema difference:
GemsNY backend payloads can expose source images as relative asset paths rather
than absolute http(s) URLs. V18/V21's robust_source_image accepted only absolute
URLs, so valid product lists could be discarded before enumeration.

V22 preserves every strict gate: exact expected product count, one source image
per accepted product, measured original/source dimensions >=500x500, and no
category/page limits. It only broadens source-image normalization to the relative
asset forms already used by GemsNY's frontend.
"""
import re
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse, urljoin

import remaining_api_recovery_v4 as v4
import remaining_catalog_recovery_v20 as v20
import remaining_catalog_recovery_v21 as v21

ASSET='https://assets.gemsny.com/'


def robust_source_image_relative(d):
    found=[]
    def norm(s):
        s=(s or '').strip()
        if not s:return ''
        if s.startswith('//'):s='https:'+s
        elif s.startswith('http://') or s.startswith('https://'):pass
        elif s.startswith('/') or re.search(r'^(?:image-(?:gemstone|jewelry)|images?|media|uploads?)/',s,re.I):
            s=urljoin(ASSET,s.lstrip('/'))
        else:
            return ''
        try:
            p=urlparse(s)
            q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
               if k.lower() not in {'width','height','w','h','quality','q','format','fit','crop','auto','dpr','resize'}]
            return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))
        except Exception:
            return s
    def walk(x,path=''):
        if isinstance(x,dict):
            for k,v in x.items():walk(v,(path+'.'+str(k)).lower())
        elif isinstance(x,list):
            for v in x[:80]:walk(v,path)
        elif isinstance(x,str):
            raw=x.strip();u=norm(raw)
            if not u:return
            low=(path+' '+raw+' '+u).lower()
            if re.search(r'logo|icon|badge|sprite|placeholder|loader|certificate|favicon',low):return
            imageish=(re.search(r'image|photo|picture|media|thumb|gallery|asset|src',path,re.I) or
                      re.search(r'\.(?:jpe?g|png|webp|avif)(?:\?|$)',raw,re.I) or
                      re.search(r'image-(?:gemstone|jewelry)|/images?/|/media/|/upload/|cdn',u,re.I))
            if not imageish:return
            score=0
            if re.search(r'original|source|master|full|zoom|large|main|primary',path,re.I):score+=14
            if re.search(r'image|photo|picture|media|gallery|src',path,re.I):score+=6
            if re.search(r'image-(?:gemstone|jewelry)|/original/|/source/',u,re.I):score+=8
            if re.search(r'thumb|thumbnail|small|tiny|swatch',low,re.I):score-=10
            found.append((score,u))
    walk(d)
    return sorted(found,key=lambda x:x[0],reverse=True)[0][1] if found else ''


# Patch all dynamic references used by V21 scoring, usability checks,
# enumeration and final image extraction.
v20.v18.robust_source_image=robust_source_image_relative
v4.img_of=robust_source_image_relative
v21.v4.img_of=robust_source_image_relative

# V21's smart flatten/probe/enumerator now see relative source images as usable.
v4.flatten=v21.smart_flatten
v4.candidates=v21.candidates
v4.probe=v21.flexible_probe
v4.enumerate_api=v21.flexible_enumerate

if __name__=='__main__':
    v4.main()
