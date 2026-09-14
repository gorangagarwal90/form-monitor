import math,time,re
from urllib.parse import urlparse
import remaining_browser_recovery_v9 as core

# Preserve the original V9 browser enumerator before monkey-patching core below.
# Without this alias, enumerate_browser() recursively calls itself after assignment.
ORIGINAL_ENUMERATE_BROWSER = core.enumerate_browser

# V11 keeps the strict V9 counting/image rules, but actively exercises the live
# pagination UI so APIs that are only called after page 1 become observable.

def _response_collector(caps):
    def onresp(resp):
        try:
            if resp.status != 200:
                return
            ct=(resp.headers.get('content-type') or '').lower()
            if not any(x in ct for x in ('json','javascript','text/plain')) and 'storebe.gemsny.com' not in resp.url:
                return
            try:
                obj=resp.json()
            except Exception:
                txt=resp.text()
                if not txt or txt[:1] not in '[{':
                    return
                import json
                obj=json.loads(txt)
            items=core.best_items(obj)
            if not items:
                return
            req=resp.request
            try:
                headers=req.all_headers()
            except Exception:
                headers={}
            caps.append({'url':resp.url,'method':req.method,'post_data':req.post_data,'headers':headers,'items':items})
        except Exception:
            pass
    return onresp

def _first_visible(page,selectors):
    for sel in selectors:
        try:
            loc=page.locator(sel)
            n=min(loc.count(),20)
            for i in range(n):
                el=loc.nth(i)
                if el.is_visible() and el.is_enabled():
                    return el
        except Exception:
            pass
    return None

def _click_pagination_once(page):
    selectors=[
        'a[rel="next"]','button[rel="next"]',
        'button[aria-label*="next" i]','a[aria-label*="next" i]',
        'button:has-text("Next")','a:has-text("Next")',
        'button:has-text("Load More")','a:has-text("Load More")',
        '[class*="pagination" i] a:has-text("2")','[class*="pagination" i] button:has-text("2")',
        'a[href*="page=2"]','a[href*="pageno=2"]','a[href*="pageNo=2"]'
    ]
    el=_first_visible(page,selectors)
    if not el:
        return False
    try:
        el.scroll_into_view_if_needed(timeout=3000)
        el.click(timeout=7000)
        try:
            page.wait_for_load_state('domcontentloaded',timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(3500)
        try: page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
        except Exception: pass
        page.wait_for_timeout(1200)
        return True
    except Exception:
        return False

def capture_candidates(page,url):
    caps=[]
    handler=_response_collector(caps)
    page.on('response',handler)
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=60000)
        page.wait_for_timeout(4500)
        for i in range(6):
            try: page.evaluate(f'window.scrollTo(0,document.body.scrollHeight*{(i+1)/6})')
            except Exception: pass
            page.wait_for_timeout(500)
        _click_pagination_once(page)
        page.wait_for_timeout(1000)
    except Exception:
        pass
    try: page.remove_listener('response',handler)
    except Exception: pass
    out=[];seen=set()
    for c in sorted(caps,key=lambda x:len(x['items']),reverse=True):
        sig=(c['method'],c['url'],c.get('post_data') or '')
        if sig not in seen:
            seen.add(sig);out.append(c)
    return out

def _next_control(page):
    return _first_visible(page,[
        'a[rel="next"]','button[rel="next"]',
        'button[aria-label*="next" i]','a[aria-label*="next" i]',
        'button:has-text("Next")','a:has-text("Next")',
        'button:has-text("Load More")','a:has-text("Load More")'
    ])

def enumerate_browser(page,url,expected):
    # First keep the proven query-parameter path from V9.
    qseen,qpages,qerr=ORIGINAL_ENUMERATE_BROWSER(page,url,expected)
    if len(qseen)==expected:
        return qseen,qpages,qerr

    # Then use the site's actual controls. This supports server-rendered grids,
    # POST pagination and infinite/load-more components without guessing params.
    first=core.load_dom(page,url,2600)
    seen={x['sku']:x for x in first if x.get('sku')}
    if len(seen)==expected:
        return seen,1,''
    if not seen:
        return qseen if len(qseen)>0 else seen,1,f'click DOM product count 0/{expected}; prior={qerr}'
    pages=1;stagnant=0
    est=max(1,len(seen));max_steps=max(20,math.ceil(expected/est)+20)
    for _ in range(max_steps):
        ctl=_next_control(page)
        if not ctl:
            break
        before=len(seen)
        try:
            ctl.scroll_into_view_if_needed(timeout=3000)
            ctl.click(timeout=7000)
            try: page.wait_for_load_state('domcontentloaded',timeout=12000)
            except Exception: pass
            page.wait_for_timeout(1700)
            try: page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
            except Exception: pass
            page.wait_for_timeout(700)
        except Exception:
            break
        pages+=1
        cur=core.dom_products(page)
        for x in cur:
            if x.get('sku'): seen.setdefault(x['sku'],x)
        if len(seen)>=expected:
            break
        stagnant = stagnant+1 if len(seen)==before else 0
        if stagnant>=3:
            break
    if len(seen)==expected:
        return seen,pages,''
    if len(qseen)>len(seen):
        return qseen,qpages,qerr
    return seen,pages,f'click DOM product count {len(seen)}/{expected}; prior={qerr}'

core.capture_candidates=capture_candidates
core.enumerate_browser=enumerate_browser

if __name__=='__main__':
    core.main()
