import asyncio, json, re
from urllib.parse import urlparse
from playwright.async_api import async_playwright

# Deep recovery probe for the only 10 categories still outside the validated
# standard-diamond recovery.  It records backend request/response shapes so the
# next recovery step can enumerate every product without relying on flaky PLP
# pagination.
TARGETS = [
    'https://www.gemsny.com/natural-diamond-pair/basic-search',
    'https://www.gemsny.com/natural-diamond-pair/basic-search?myo=earrings',
    'https://www.gemsny.com/ready-to-ship/earrings',
    'https://www.gemsny.com/wedding-rings/sapphire/three-fourth-eternity',
    'https://www.gemsny.com/ruby-bracelets/preset',
    'https://www.gemsny.com/natural-swiss-topaz-pendants/preset',
    'https://www.gemsny.com/ruby/basic-search/african',
    'https://www.gemsny.com/engagement-rings/setting',
    'https://www.gemsny.com/ready-to-ship/rings',
    'https://www.gemsny.com/sapphires/basic-search/blue-sapphires?myo=engagement-rings',
]

INTEREST = (
    'storebe.gemsny.com', '/api/', '_rsc=', 'diamond', 'gemstone', 'ready-to-ship',
    'preset-', '/ring/', 'search'
)

def compact_json(obj, limit=12000):
    try:
        s = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    except Exception:
        s = repr(obj)
    return s[:limit]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={'width': 1440, 'height': 1200},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
        )
        for url in TARGETS:
            page = await ctx.new_page()
            reqs, responses = [], []

            def onreq(req):
                u = req.url
                if any(k.lower() in u.lower() for k in INTEREST):
                    if req.resource_type in ('xhr', 'fetch') or 'storebe.gemsny.com' in u or '_rsc=' in u:
                        reqs.append({'method': req.method, 'type': req.resource_type, 'url': u})

            async def capture(resp):
                u = resp.url
                if not any(k.lower() in u.lower() for k in INTEREST):
                    return
                rt = resp.request.resource_type
                if rt not in ('xhr', 'fetch') and 'storebe.gemsny.com' not in u and '_rsc=' not in u:
                    return
                rec = {'status': resp.status, 'type': rt, 'url': u, 'content_type': resp.headers.get('content-type','')}
                try:
                    body = await resp.body()
                    rec['bytes'] = len(body)
                    text = body.decode('utf-8', errors='replace')
                    # Preserve enough response payload to identify pagination,
                    # product IDs and source-image fields, but cap output size.
                    rec['sample'] = text[:12000]
                except Exception as e:
                    rec['error'] = repr(e)
                responses.append(rec)

            page.on('request', onreq)
            page.on('response', lambda r: asyncio.create_task(capture(r)))
            print('\n===TARGET===', url, flush=True)
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=90000)
                await page.wait_for_timeout(7000)
                for _ in range(3):
                    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    await page.wait_for_timeout(1000)
                body = await page.evaluate("() => (document.body?.innerText||'')")
                print('BODY_META', compact_json({
                    'title': await page.title(),
                    'result_matches': re.findall(r'Result\\s*\\(([^)]+)\\)', body)[:5],
                    'item_numbers': re.findall(r'Item#:\\s*([A-Za-z0-9_-]+)', body)[:30],
                    'text_head': body[:1800],
                }, 5000), flush=True)

                # Force several explicit page navigations.  This is especially
                # useful for Next/RSC gemstone grids whose API is server-side.
                sep = '&' if '?' in url else '?'
                for n in (2, 3, 5):
                    try:
                        await page.goto(f'{url}{sep}page={n}', wait_until='domcontentloaded', timeout=60000)
                        await page.wait_for_timeout(2500)
                    except Exception as e:
                        print('PAGE_NAV_ERROR', n, repr(e), flush=True)

                # Allow response-capture tasks to finish.
                await page.wait_for_timeout(2500)
                uniq_req, seen = [], set()
                for r in reqs:
                    k=(r['method'],r['url'])
                    if k not in seen:
                        seen.add(k); uniq_req.append(r)
                print('REQUESTS', compact_json(uniq_req[-120:], 30000), flush=True)

                # Deduplicate by URL+status and retain representative payloads.
                uniq_resp, seen = [], set()
                for r in responses:
                    k=(r.get('status'),r.get('url'))
                    if k not in seen:
                        seen.add(k); uniq_resp.append(r)
                print('RESPONSES', compact_json(uniq_resp[-80:], 120000), flush=True)
            except Exception as e:
                print('ERROR', repr(e), flush=True)
            await page.close()
        await browser.close()

asyncio.run(main())
