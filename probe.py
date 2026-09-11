import asyncio
from playwright.async_api import async_playwright

URL='https://www.gemsny.com/sapphires/basic-search'

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(
            viewport={'width':1440,'height':1200},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
        )
        page=await c.new_page()
        r=await page.goto(URL,wait_until='domcontentloaded',timeout=60000)
        await page.wait_for_timeout(3000)
        print('STATUS',r.status if r else None)
        print('IMAGES',await page.evaluate('document.images.length'))

        candidates=await page.evaluate(r'''()=>{
          const imgs=[...document.images];
          return imgs.map((i,idx)=>{
            const src=i.currentSrc||i.src||i.getAttribute('data-src')||i.getAttribute('data-lazy-src')||'';
            const alt=(i.getAttribute('alt')||'').trim();
            let n=i, contexts=[];
            for(let d=0;d<7&&n;d++,n=n.parentElement){
              contexts.push({
                d,
                tag:n.tagName||'',
                cls:(n.className&&String(n.className).slice(0,300))||'',
                text:(n.innerText||'').trim().slice(0,500),
                href:(n.matches&&n.matches('a[href]'))?n.href:''
              });
            }
            return {idx,src,alt,nw:i.naturalWidth||0,nh:i.naturalHeight||0,outer:i.outerHTML.slice(0,1500),contexts};
          }).filter(x=>
            /images-stones|images-jewelry|91042|74131|63682/i.test(x.src+' '+x.alt+' '+x.outer+' '+x.contexts.map(c=>c.text).join(' '))
          ).slice(0,30);
        }''')
        print('PRODUCTISH_COUNT',len(candidates))
        for x in candidates:
            print('\nPRODUCT_IMG',x['idx'],'ALT=',x['alt'],'SRC=',x['src'],'NAT=',x['nw'],x['nh'])
            print('OUTER=',x['outer'])
            for ctx in x['contexts']:
                print('CTX',ctx)

        await c.close(); await b.close()

asyncio.run(main())
