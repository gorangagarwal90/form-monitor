import asyncio
from playwright.async_api import async_playwright

URLS = [
    'https://www.gemsny.com/sapphire-rings',
    'https://www.gemsny.com/sapphire-earrings',
]

async def inspect(page, url):
    r = await page.goto(url, wait_until='domcontentloaded', timeout=60000)
    await page.wait_for_timeout(4000)
    print('\n=== PAGE ===', url)
    print('STATUS', r.status if r else None)
    print('TITLE', await page.title())
    print('URL', page.url)
    print('IMAGES', await page.evaluate('document.images.length'))
    body = (await page.locator('body').inner_text())
    print('BODY_HEAD', body[:3500].replace('\n',' | '))

    data = await page.evaluate(r'''()=>{
      const abs=u=>{try{return new URL(u,location.href).href}catch(e){return''}};
      const imgs=[...document.images];
      return imgs.map((img,idx)=>{
        let n=img, ctx=[];
        for(let d=0;d<9&&n;d++,n=n.parentElement){
          ctx.push({
            d,
            tag:n.tagName||'',
            cls:(n.className&&String(n.className).slice(0,350))||'',
            text:(n.innerText||'').trim().slice(0,900),
            href:(n.matches&&n.matches('a[href]'))?n.href:''
          });
        }
        return {
          idx,
          alt:(img.getAttribute('alt')||'').trim(),
          src:abs(img.currentSrc||img.src||img.getAttribute('data-src')||''),
          nw:img.naturalWidth||0,
          nh:img.naturalHeight||0,
          outer:img.outerHTML.slice(0,1800),
          ctx
        };
      }).filter(x=>{
        const hay=(x.alt+' '+x.src+' '+x.outer+' '+x.ctx.map(c=>c.text+' '+c.cls).join(' ')).toLowerCase();
        return /sapphire|ring|earring|product|item#|sku/.test(hay) && !/logo|icon|social/.test(x.alt.toLowerCase());
      }).slice(0,50);
    }''')

    print('CANDIDATES', len(data))
    for x in data[:25]:
        print('\nIMG',x['idx'],'ALT=',x['alt'],'SRC=',x['src'],'NAT=',x['nw'],x['nh'])
        print('OUTER=',x['outer'])
        for c in x['ctx'][:8]:
            if c['text'] or c['cls'] or c['href']:
                print('CTX',c)

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(
            viewport={'width':1440,'height':1200},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
        )
        page=await c.new_page()
        for u in URLS:
            await inspect(page,u)
        await c.close(); await b.close()

asyncio.run(main())
