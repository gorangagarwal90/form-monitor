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
    print('IMAGES', await page.evaluate('document.images.length'))

    data = await page.evaluate(r'''()=>{
      const abs=u=>{try{return new URL(u,location.href).href}catch(e){return''}};
      return [...document.images].map((img,idx)=>{
        let n=img, ctx=[];
        for(let d=0;d<10&&n;d++,n=n.parentElement){
          ctx.push({
            d,
            tag:n.tagName||'',
            cls:(n.className&&String(n.className).slice(0,500))||'',
            text:(n.innerText||'').trim().slice(0,1200),
            href:(n.matches&&n.matches('a[href]'))?n.href:''
          });
        }
        const src=abs(img.currentSrc||img.src||img.getAttribute('data-src')||img.getAttribute('data-lazy-src')||'');
        const alt=(img.getAttribute('alt')||'').trim();
        const text=ctx.map(c=>c.text).join(' ');
        return {
          idx, alt, src,
          nw:img.naturalWidth||0, nh:img.naturalHeight||0,
          outer:img.outerHTML.slice(0,2200), ctx, text
        };
      }).filter(x=>{
        const big=Math.max(x.nw,x.nh)>=120;
        const productContext=/View\s*Details/i.test(x.text);
        const asset=/jewelry|ring|earring|preset|product/i.test(x.src+' '+x.alt+' '+x.outer);
        const menu=/mega_menus|item-image/i.test(x.src+' '+x.outer);
        return big && !menu && (productContext || asset);
      }).slice(0,80);
    }''')

    print('PRODUCT_CANDIDATES', len(data))
    for x in data[:40]:
        print('\nIMG',x['idx'],'ALT=',x['alt'],'SRC=',x['src'],'NAT=',x['nw'],x['nh'])
        print('OUTER=',x['outer'])
        for c in x['ctx'][:9]:
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
