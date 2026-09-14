"""GemsNY unresolved-category recovery V18.

Builds on V17 but broadens original/source image discovery for backend payloads
whose image URLs are extensionless (CDN/API URLs). V17 rejected these as
"no-usable-products" even when product rows were otherwise valid.

The resolver still prefers original/source/main image fields, strips resize
query parameters, excludes obvious non-product assets, and keeps the existing
strict category-count and >=500x500 source-image validation from V17.
"""
import re
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import remaining_api_recovery_v4 as v4
import remaining_catalog_recovery_v14 as v14
import remaining_catalog_recovery_v17 as v17


def robust_source_image(d):
    found=[]
    def walk(x,path=''):
        if isinstance(x,dict):
            for k,v in x.items():
                walk(v,(path+'.'+str(k)).lower())
        elif isinstance(x,list):
            for v in x[:40]:
                walk(v,path)
        elif isinstance(x,str):
            s=x.strip()
            if not (s.startswith('http://') or s.startswith('https://') or s.startswith('//')):
                return
            low=(path+' '+s).lower()
            if re.search(r'logo|icon|badge|sprite|placeholder|loader|certificate|favicon',low):
                return
            # Require either an image/media-like field/path or a common raster extension.
            if not (re.search(r'image|photo|picture|media|thumb|gallery|asset',path,re.I) or
                    re.search(r'\.(?:jpe?g|png|webp|avif)(?:\?|$)',s,re.I) or
                    re.search(r'/image(?:s)?/|/media/|/upload/|cdn',s,re.I)):
                return
            score=0
            if re.search(r'original|source|master|full|zoom|large|main|primary',path,re.I): score+=12
            if re.search(r'image|photo|picture|media|gallery',path,re.I): score+=6
            if re.search(r'image-(?:gemstone|jewelry)|/original/|/source/',s,re.I): score+=6
            if re.search(r'thumb|thumbnail|small|tiny|swatch',low,re.I): score-=8
            found.append((score,s))
    walk(d)
    if not found:
        return ''
    s=sorted(found,key=lambda x:x[0],reverse=True)[0][1]
    if s.startswith('//'):
        s='https:'+s
    try:
        p=urlparse(s)
        q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
           if k.lower() not in {'width','height','w','h','quality','q','format','fit','crop','auto','dpr','resize'}]
        s=urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))
    except Exception:
        pass
    return s


# V17's strict_probe and enumerator resolve v14.source_image dynamically, so
# patching it here upgrades discovery and enumeration without weakening V17's
# exact-count or dimension gates.
v14.source_image=robust_source_image
v4.img_of=robust_source_image
v4.candidates=v17.candidates
v4.probe=v17.v15.strict_probe
v4.enumerate_api=v17.enumerate_api_no_short_page

if __name__=='__main__':
    v4.main()
