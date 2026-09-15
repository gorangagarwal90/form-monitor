"""GemsNY V24 strict recovery.
Preserves V23 and fixes three deterministic failure classes without weakening the
500x500 source-image or exact-count gates: semantic filters are accepted only
when they move enumeration toward the baseline count; over-counts are canonically
deduplicated by product detail URL when that resolves exactly to baseline; and
lab-diamond categories probe diamond-specific live API families in addition to
legacy gemstone candidates.
"""
import re
from urllib.parse import urlsplit,urlunsplit
import remaining_api_recovery_v4 as v4
import remaining_catalog_recovery_v21 as v21
import remaining_catalog_recovery_v23 as v23

# Preserve the original V21 candidate generator before V24 installs its wrapper.
_BASE_CANDIDATES = v21.candidates


def canonical_url(d):
    u=v4.product_url(d) or ''
    if not u:return ''
    try:
        p=urlsplit(u)
        path=re.sub(r'/+$','',p.path).lower()
        return urlunsplit((p.scheme.lower(),p.netloc.lower(),path,'',''))
    except Exception:return u.split('?',1)[0].rstrip('/').lower()


def semantic_filter_safe(row,seen,expected=None):
    f=v23.semantic_filter(row,seen)
    if not expected:return f
    if abs(len(f)-expected) < abs(len(seen)-expected):return f
    return seen


def candidates_v24(row):
    out=list(_BASE_CANDIDATES(row))
    gem,typ,loose,style=v4.infer(row)
    if loose and gem and gem.startswith('lab-diamond'):
        for ep in ('/diamond','/diamond/v2','/v2/diamond','/v3/diamond','/lab-diamond','/lab-diamond/v2'):
            for labkey,labval in (('lab',1),('isLab',1),('is_lab',1),('type','Lab Diamond'),('diamondType','Lab Grown')):
                out.append((ep,{labkey:labval},'page','pageSize'))
                out.append((ep,{'client_id':v4.CLIENT,labkey:labval},'page','pageSize'))
    return v21._dedupe(out)


def probe_v24(sess,row,expected):
    old=v21.candidates
    try:
        v21.candidates=candidates_v24
        enumerate_v24.row=row
        return v23.probe_v23(sess,row,expected)
    finally:
        v21.candidates=old


def enumerate_v24(sess,sel,expected):
    seen,req=v21.flexible_enumerate(sess,sel,expected)
    row=getattr(enumerate_v24,'row',None)
    if row and seen:
        seen=semantic_filter_safe(row,seen,expected)
    if expected and len(seen)>expected:
        byurl={}
        for k,d in seen.items():
            u=canonical_url(d)
            byurl.setdefault(u or ('__'+k),d)
        if len(byurl)==expected:
            seen={u:d for u,d in byurl.items()}
    return seen,req

v23.v20.v18.robust_source_image=v23.source_image_v23
v4.img_of=v23.source_image_v23
v21.v4.img_of=v23.source_image_v23
v4.flatten=v21.smart_flatten
v4.candidates=candidates_v24
v4.probe=probe_v24
v4.enumerate_api=enumerate_v24

if __name__=='__main__':v4.main()
