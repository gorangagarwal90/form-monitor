import math,re,json
from html import unescape
from urllib.parse import urljoin
import remaining_browser_recovery_v9 as core

# Preserve V9 functions before monkey-patching.
ORIGINAL_ENUMERATE_BROWSER = core.enumerate_browser
ORIGINAL_DOM_PRODUCTS = core.dom_products

# V11.2: keep strict V9 exact-count/source-image rules, but broaden discovery for
# Next/RSC pages and newer card markup. Nothing is accepted unless the final
# unique product count still equals the expected category inventory.

def _decode_raw(txt):
    if not txt:
        return ''
    s=unescape(str(txt))
    # Next/RSC payloads commonly JSON-escape slash/quote/unicode sequences.
    for _ in range(3):
        old=s
        s=s.replace('\\/','/').replace('\\"','"').replace('\\u0026','&').replace('\\u003d','=').replace('\\u003f','?').replace('\\u002f','/').replace('\\u003a',':')
        if s==old:
            break
    return s

def _raw_products(txt,base_url='https://www.gemsny.com/'):
    """Recover product identities directly from escaped Next/RSC/HTML image paths.

    GemsNY source-image URLs contain /image-gemstone/<product-key>/ or
    /image-jewelry/<product-key>/, which gives us a stable product key without
    trusting rendered card markup. The strict caller still requires the exact
    expected unique-product count before accepting coverage.
    """
    s=_decode_raw(txt)
    out={}
    # Absolute or protocol-relative asset URLs.
    pats=[
        r'(https?:\\?/\\?/(?:assets\\.)?gemsny\\.com/[^\s"\'<>]+)',
        r'(//(?:assets\\.)?gemsny\\.com/[^\s"\'<>]+)',
        r'((?:https?:)?//[^\s"\'<>]*?/image-(?:jewelry|gemstone)/[^\s"\'<>]+)',
        r'(/image-(?:jewelry|gemstone)/[^\s"\'<>]+)',
    ]
    vals=[]
    for pat in pats:
        try: vals.extend(re.findall(pat,s,re.I))
        except Exception: pass
    for raw in vals:
        u=_decode_raw(raw).rstrip('\\,;)]}')
        if u.startswith('//'): u='https:'+u
        elif u.startswith('/'): u=urljoin('https://assets.gemsny.com/',u.lstrip('/'))
        m=re.search(r'/image-(?:jewelry|gemstone)/([^/?#"\'\\]+)',u,re.I)
        if not m: continue
        k=m.group(1).strip()
        if not k or len(k)<2: continue
        if re.search(r'logo|icon|badge|sprite|placeholder|loader',u,re.I): continue
        out.setdefault(k,{'sku':k,'product_url':'','image':u})
    return list(out.values())

def _response_collector(caps):
    def onresp(resp):
        try:
            if resp.status != 200:
                return
            ct=(resp.headers.get('content-type') or '').lower()
            if not any(x in ct for x in ('json','javascript','text/plain','text/x-component','event-stream','text/html')) and 'storebe.gemsny.com' not in resp.url:
                return
            obj=None; txt=''
            try:
                obj=resp.json()
            except Exception:
                try: txt=resp.text()
                except Exception: txt=''
                try:
                    if txt and txt[:1] in '[{': obj=json.loads(txt)
                except Exception:
                    pass
            items=core.best_items(obj) if obj is not None else []
            if not items and txt:
                items=_raw_products(txt,resp.url)
            if not items:
                return
            req=resp.request
            try: headers=req.all_headers()
            except Exception: headers={}
            caps.append({'url':resp.url,'method':req.method,'post_data':req.post_data,'headers':headers,'items':items})
        except Exception:
            pass
    return onresp

def _first_visible(page,selectors):
    for sel in selectors:
        try:
            loc=page.locator(sel)
            for i in range(min(loc.count(),30)):
                el=loc.nth(i)
                if el.is_visible() and el.is_enabled():
                    return el
        except Exception:
            pass
    return None

def _click_pagination_once(page):
    el=_first_visible(page,[
        'a[rel="next"]','button[rel="next"]','button[aria-label*="next" i]','a[aria-label*="next" i]',
        'button:has-text("Next")','a:has-text("Next")','button:has-text("Load More")','a:has-text("Load More")',
        '[class*="pagination" i] a:has-text("2")','[class*="pagination" i] button:has-text("2")',
        'a[href*="page=2"]','a[href*="pageno=2"]','a[href*="pageNo=2"]'
    ])
    if not el: return False
    try:
        el.scroll_into_view_if_needed(timeout=3000); el.click(timeout=7000)
        try: page.wait_for_load_state('domcontentloaded',timeout=15000)
        except Exception: pass
        page.wait_for_timeout(3500)
        try: page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
        except Exception: pass
        page.wait_for_timeout(1200)
        return True
    except Exception:
        return False

def capture_candidates(page,url):
    caps=[]; handler=_response_collector(caps); page.on('response',handler)
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=60000); page.wait_for_timeout(5000)
        for i in range(8):
            try: page.evaluate(f'window.scrollTo(0,document.body.scrollHeight*{(i+1)/8})')
            except Exception: pass
            page.wait_for_timeout(500)
        _click_pagination_once(page); page.wait_for_timeout(1200)
    except Exception:
        pass
    try: page.remove_listener('response',handler)
    except Exception: pass
    out=[];seen=set()
    for c in sorted(caps,key=lambda x:len(x['items']),reverse=True):
        sig=(c['method'],c['url'],c.get('post_data') or '')
        if sig not in seen: seen.add(sig); out.append(c)
    return out

def _embedded_products(page):
    """Extract product-ish objects/links/source-image paths from Next data/RSC/HTML."""
    out={}
    def add(d):
        if not isinstance(d,dict): return
        k=core.key(d); im=core.choose_image(d)
        if k and im: out.setdefault(str(k),d)
    try:
        scripts=page.locator('script').all_text_contents()
    except Exception:
        scripts=[]
    for txt in scripts:
        if not txt or len(txt)<20: continue
        # Full JSON script payloads (e.g. __NEXT_DATA__).
        try:
            obj=json.loads(txt)
            for lst in core.walk_lists(obj):
                for d in lst:
                    add(d)
        except Exception:
            pass
        for d in _raw_products(txt,page.url):
            add(d)
    # Last-resort hydrated HTML parser. It deliberately requires a product-like
    # URL plus an image URL, or a GemsNY source-image path whose directory gives
    # us the product key.
    try:
        html=page.content()
    except Exception:
        html=''
    if html:
        for d in _raw_products(html,page.url):
            add(d)
        for m in re.finditer(r'href=["\']([^"\']+)["\']',html,re.I):
            href=m.group(1)
            if href.startswith(('#','javascript:','mailto:','tel:')): continue
            if not re.search(r'/(?:sapphire|ruby|emerald|alexandrite|tanzanite|aquamarine|tsavorite|morganite|peridot|tourmaline|topaz|amethyst|citrine|spinel|garnet|diamond|ring|earring|pendant|bracelet|wedding)',href,re.I): continue
            chunk=html[max(0,m.start()-1800):min(len(html),m.end()+2200)]
            ims=re.findall(r'(?:src|data-src|data-original|srcset)=["\']([^"\']+)',chunk,re.I)
            im=next((x.split(',')[0].strip().split()[0] for x in ims if not re.search(r'logo|icon|badge|sprite|placeholder|loader',x,re.I)), '')
            if not im: continue
            pu=urljoin(page.url,href); mm=re.search(r'(\d{4,}|[A-Z]{1,6}-?\d{3,})',pu,re.I)
            k=(mm.group(1) if mm else pu)
            out.setdefault(k,{'sku':k,'product_url':pu,'image':urljoin(page.url,im)})
    return out

def dom_products(page):
    base=ORIGINAL_DOM_PRODUCTS(page)
    seen={str(x.get('sku')):x for x in base if x.get('sku')}
    for k,v in _embedded_products(page).items(): seen.setdefault(k,v)
    return list(seen.values())

def _next_control(page):
    return _first_visible(page,[
        'a[rel="next"]','button[rel="next"]','button[aria-label*="next" i]','a[aria-label*="next" i]',
        'button:has-text("Next")','a:has-text("Next")','button:has-text("Load More")','a:has-text("Load More")'
    ])

def enumerate_browser(page,url,expected):
    qseen,qpages,qerr=ORIGINAL_ENUMERATE_BROWSER(page,url,expected)
    if len(qseen)==expected: return qseen,qpages,qerr
    # Retry initial page with broadened embedded/RSC extraction.
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=60000); page.wait_for_timeout(5000)
        for _ in range(6):
            try: page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
            except Exception: pass
            page.wait_for_timeout(500)
    except Exception:
        pass
    first=dom_products(page); seen={str(x['sku']):x for x in first if x.get('sku')}
    if len(seen)==expected: return seen,1,''
    if not seen: return qseen if len(qseen)>0 else seen,1,f'embedded/RSC DOM product count 0/{expected}; prior={qerr}'
    pages=1; stagnant=0; est=max(1,len(seen)); max_steps=max(20,math.ceil(expected/est)+20)
    for _ in range(max_steps):
        ctl=_next_control(page)
        if not ctl: break
        before=len(seen)
        try:
            ctl.scroll_into_view_if_needed(timeout=3000); ctl.click(timeout=7000)
            try: page.wait_for_load_state('domcontentloaded',timeout=12000)
            except Exception: pass
            page.wait_for_timeout(2000)
            try: page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
            except Exception: pass
            page.wait_for_timeout(800)
        except Exception:
            break
        pages+=1
        for x in dom_products(page):
            if x.get('sku'): seen.setdefault(str(x['sku']),x)
        if len(seen)>=expected: break
        stagnant=stagnant+1 if len(seen)==before else 0
        if stagnant>=3: break
    if len(seen)==expected: return seen,pages,''
    if len(qseen)>len(seen): return qseen,qpages,qerr
    return seen,pages,f'embedded/RSC DOM product count {len(seen)}/{expected}; prior={qerr}'

core.capture_candidates=capture_candidates
core.dom_products=dom_products
core.enumerate_browser=enumerate_browser

if __name__=='__main__':
    core.main()
