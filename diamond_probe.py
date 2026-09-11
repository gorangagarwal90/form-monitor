import asyncio, json, re
from playwright.async_api import async_playwright

URL='https://www.gemsny.com/lab-diamonds/basic-search/pear'

async def main():
  async with async_playwright() as p:
    browser=await p.chromium.launch(headless=True)
    ctx=await browser.new_context(viewport={'width':1440,'height':1200}, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36')
    page=await ctx.new_page()
    req=[]
    def on_response(resp):
      u=resp.url.lower()
      if any(k in u for k in ('diamond','search','product','inventory','api','graphql')):
        if len(req)<250: req.append({'status':resp.status,'url':resp.url,'type':resp.request.resource_type})
    page.on('response', on_response)
    await page.goto(URL, wait_until='domcontentloaded', timeout=90000)
    await page.wait_for_timeout(7000)
    for _ in range(12):
      await page.mouse.wheel(0,900)
      await page.wait_for_timeout(500)
    info=await page.evaluate(r'''() => {
      const arr=[];
      const abs=u=>{try{return new URL(u,location.href).href}catch(e){return ''}};
      for (const img of [...document.images]) {
        const src=abs(img.currentSrc||img.getAttribute('src')||img.getAttribute('data-src')||'');
        let a=img.closest('a[href]');
        let p=img.closest('tr,[role="row"],article,li,[class*="card"],[class*="product"],[class*="diamond"],[class*="item"]');
        arr.push({src,alt:img.alt||'',cls:img.className||'',w:img.naturalWidth||0,h:img.naturalHeight||0,anchor:a?a.href:'',parent:p?p.className||p.tagName:'',text:p?(p.innerText||'').slice(0,500):''});
      }
      const els=[...document.querySelectorAll('[class*="diamond"],[class*="product"],[class*="inventory"],[class*="grid"],[class*="table"]')].slice(0,300).map(e=>({tag:e.tagName,cls:e.className||'',text:(e.innerText||'').slice(0,300),html:e.outerHTML.slice(0,1200)}));
      return {url:location.href,title:document.title,images:arr,elements:els,body:(document.body.innerText||'').slice(0,30000)};
    }''')
    print('===PAGE INFO===')
    print(json.dumps({k:info[k] for k in ('url','title')},indent=2))
    print('===IMAGES===')
    print(json.dumps(info['images'][:300],indent=2))
    print('===CANDIDATE ELEMENTS===')
    print(json.dumps(info['elements'][:200],indent=2))
    print('===RESPONSES===')
    print(json.dumps(req,indent=2))
    await browser.close()

asyncio.run(main())
