import math,re
from urllib.parse import urljoin,urlparse,parse_qsl,urlencode,urlunparse
import requests
import remaining_browser_recovery_v11 as v11

core=v11.core
UA=getattr(core,'UA','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36')

# V12 adds a non-headless HTTP/SSR recovery path. Public GemsNY category pages are
# server-rendered for crawlers while Playwright can receive a client shell with no
# product cards. We still accept a category only when the exact expected unique
# product count is enumerated; image validation remains the existing source-image
# >=500x500 rule in V9/V11 build_rows.

def _session():
    s=requests.Session()
    s.headers.update({
        'User-Agent':UA,
        'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language':'en-US,en;q=0.9',
        'Cache-Control':'no-cache',
        'Pragma':'no-cache',
        'Referer':'https://www.gemsny.com/'
    })
    return s

def _get(sess,url):
    try:
        r=sess.get(url,timeout=45,allow_redirects=True)
        if r.status_code!=200:return '',f'HTTP {r.status_code}'
        txt=r.text or ''
        if len(txt)<500:return '',f'short HTML {len(txt)} bytes'
        return txt,''
    except Exception as e:
        return '',f'{type(e).__name__}: {e}'[:220]

def _abs(base,u):
    if not u:return ''
    u=v11._decode_raw(u).strip().strip('"\'')
    if u.startswith('//'):return 'https:'+u
    return urljoin(base,u)

def _products_from_html(html,base):
    out={}
    # Strongest source: original GemsNY asset paths embedded in SSR/Next/RSC.
    for d in v11._raw_products(html,base):
        k=str(d.get('sku') or '')
        if k:out.setdefault(k,d)

    s=v11._decode_raw(html)
    # Product detail links normally end in the numeric stock/item number. Pair a
    # nearby product link with a nearby original image URL when possible.
    link_re=re.compile(r'href=["\']([^"\']*?-([0-9]{4,})(?:[/?#"\']|$))',re.I)
    for m in link_re.finditer(s):
        href=m.group(1); sku=m.group(2)
        pu=_abs(base,href)
        if not pu or 'gemsny.com' not in urlparse(pu).netloc.lower():continue
        chunk=s[max(0,m.start()-5000):min(len(s),m.end()+5000)]
        candidates=[]
        candidates += re.findall(r'(https?:?//[^\s"\'<>]+?/image-(?:gemstone|jewelry)/[^\s"\'<>]+)',chunk,re.I)
        candidates += re.findall(r'((?:/|https?://)[^\s"\'<>]*?/image-(?:gemstone|jewelry)/[^\s"\'<>]+)',chunk,re.I)
        candidates += re.findall(r'(https?://assets\.gemsny\.com/[^\s"\'<>]+)',chunk,re.I)
        im=''
        for c in candidates:
            c=_abs(base,c).rstrip('\\,;)]}')
            if re.search(r'logo|icon|badge|sprite|placeholder|loader|certificate',c,re.I):continue
            im=core.norm_image(c);break
        old=out.get(sku)
        if old and core.choose_image(old):continue
        if im:out[sku]={'sku':sku,'product_url':pu,'image':im}
    return out

def _page_links(html,base):
    s=v11._decode_raw(html); out=[]; seen=set()
    for href in re.findall(r'href=["\']([^"\']+)["\']',s,re.I):
        u=_abs(base,href)
        if not u or urlparse(u).netloc.lower().replace('www.','')!='gemsny.com':continue
        q=dict(parse_qsl(urlparse(u).query,keep_blank_values=True))
        if any(str(q.get(k,'')).isdigit() and int(q.get(k,'0') or 0)>=2 for k in ('page','pageno','pageNo','pageNumber','pg','p')):
            if u not in seen:seen.add(u);out.append(u)
    return out

def _mutate(url,param,val):
    p=urlparse(url); q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k!=param]
    q.append((param,str(val)))
    return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))

def enumerate_ssr(url,expected):
    sess=_session(); html,err=_get(sess,url)
    if not html:return {},0,f'SSR {err}'
    first=_products_from_html(html,url); seen=dict(first); pages=1
    if len(seen)==expected:return seen,pages,''
    if not seen:return {},pages,f'SSR product count 0/{expected}'

    firstsig=tuple(seen.keys())[:12]
    chosen_urls=[]
    # Prefer pagination URLs actually emitted by the SSR page.
    emitted=_page_links(html,url)
    if emitted:
        chosen_urls=sorted(emitted,key=lambda u: next((int(v) for k,v in parse_qsl(urlparse(u).query) if k in ('page','pageno','pageNo','pageNumber','pg','p') and str(v).isdigit()),999999))

    if chosen_urls:
        stagnant=0
        for u in chosen_urls:
            h,e=_get(sess,u); pages+=1
            if not h:continue
            before=len(seen)
            for k,d in _products_from_html(h,u).items():seen.setdefault(k,d)
            if len(seen)==expected:return seen,pages,''
            if len(seen)>expected:return seen,pages,f'SSR product count {len(seen)}/{expected} (over-enumerated)'
            stagnant=stagnant+1 if len(seen)==before else 0
            if stagnant>=3:break

    # If emitted links do not expose all pages, probe common query pagination
    # fields and continue only when page 2 has a different product signature.
    chosen=None
    for param in ('page','pageno','pageNo','pageNumber','pg','p'):
        u=_mutate(url,param,2); h,e=_get(sess,u)
        if not h:continue
        pp=_products_from_html(h,u); sig=tuple(pp.keys())[:12]
        if sig and sig!=firstsig:
            chosen=param; break
    if not chosen:
        return seen,pages,f'SSR product count {len(seen)}/{expected}; no working pagination'

    step=max(1,len(first)); maxp=math.ceil(expected/step)+12; stagnant=0; prev=firstsig
    for n in range(2,maxp+1):
        u=_mutate(url,chosen,n); h,e=_get(sess,u); pages+=1
        if not h:break
        pp=_products_from_html(h,u)
        if not pp:break
        sig=tuple(pp.keys())[:12]
        before=len(seen)
        for k,d in pp.items():seen.setdefault(k,d)
        if len(seen)==expected:return seen,pages,''
        if len(seen)>expected:return seen,pages,f'SSR product count {len(seen)}/{expected} (over-enumerated)'
        stagnant=stagnant+1 if len(seen)==before or sig==prev else 0; prev=sig
        if stagnant>=3:break
    return seen,pages,'' if len(seen)==expected else f'SSR product count {len(seen)}/{expected}; pagination_param={chosen}'


def enumerate_browser(page,url,expected):
    ssr,sp,se=enumerate_ssr(url,expected)
    if len(ssr)==expected:return ssr,sp,''
    # Retain V11's live-browser/RSC fallback. Prefer whichever route recovered
    # more unique products, but never mark partial coverage COMPLETE.
    try:
        b,bp,be=v11.enumerate_browser(page,url,expected)
    except Exception as e:
        b,bp,be={},0,f'{type(e).__name__}: {e}'
    if len(b)==expected:return b,bp,''
    if len(ssr)>=len(b):return ssr,sp,f'{se}; browser={be}'
    return b,bp,f'{be}; ssr={se}'

core.enumerate_browser=enumerate_browser
core.capture_candidates=v11.capture_candidates
core.dom_products=v11.dom_products

if __name__=='__main__':
    core.main()
