import asyncio, json
from playwright.async_api import async_playwright
TARGETS=[
'https://www.gemsny.com/ready-to-ship/earrings',
'https://www.gemsny.com/wedding-rings/sapphire/three-fourth-eternity',
'https://www.gemsny.com/ruby-bracelets/preset',
'https://www.gemsny.com/natural-swiss-topaz-pendants/preset',
'https://www.gemsny.com/ruby/basic-search/african',
'https://www.gemsny.com/engagement-rings/setting',
'https://www.gemsny.com/ready-to-ship/rings',
'https://www.gemsny.com/sapphires/basic-search/blue-sapphires?myo=engagement-rings',
]
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True)
  ctx=await b.new_context(viewport={'width':1440,'height':1200},user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36')
  for url in TARGETS:
   page=await ctx.new_page(); reqs=[]
   def onreq(req):
    u=req.url
    if 'storebe.gemsny.com' in u or '/api/' in u or 'search' in u.lower() or 'product' in u.lower():
     if req.resource_type in ('xhr','fetch') or 'storebe.gemsny.com' in u: reqs.append({'method':req.method,'type':req.resource_type,'url':u})
   page.on('request',onreq)
   print('\n===TARGET===',url)
   try:
    await page.goto(url,wait_until='domcontentloaded',timeout=90000); await page.wait_for_timeout(6000)
    for _ in range(3):
     await page.evaluate('window.scrollTo(0, document.body.scrollHeight)'); await page.wait_for_timeout(1200)
    body=await page.evaluate("() => (document.body?.innerText||'').slice(0,3000)")
    print('BODY',json.dumps(body[:1500]))
    uniq=[]; seen=set()
    for r in reqs:
     k=(r['method'],r['url'])
     if k in seen: continue
     seen.add(k); uniq.append(r)
    print('REQUESTS',json.dumps(uniq[-80:],indent=2))
   except Exception as e: print('ERROR',repr(e))
   await page.close()
  await b.close()
asyncio.run(main())
