import asyncio
from playwright.async_api import async_playwright

URL='https://www.gemsny.com/sapphires/basic-search'

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True)
        c=await b.new_context(viewport={'width':1440,'height':1200},user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36')
        page=await c.new_page()
        r=await page.goto(URL,wait_until='domcontentloaded',timeout=60000)
        await page.wait_for_timeout(3000)
        print('STATUS', r.status if r else None)
        print('URL', page.url)
        print('TITLE', await page.title())
        print('IMAGES', await page.evaluate('document.images.length'))
        print('HAS_ITEM', await page.evaluate("document.body.innerText.includes('Item#')"))
        print('HAS_RESULT', await page.evaluate("document.body.innerText.includes('Result (')"))
        print('BODY', (await page.locator('body').inner_text())[:2500].replace('\n',' | '))
        imgs=await page.evaluate("""()=>[...document.images].slice(0,40).map(i=>({alt:i.alt,src:i.currentSrc||i.src,nw:i.naturalWidth,nh:i.naturalHeight}))""")
        for i,x in enumerate(imgs): print('IMG',i,x)
        html=await page.content()
        print('HTML_LEN',len(html),'SKU_IN_HTML', 'SKU 97856' in html, 'ITEM_IN_HTML','Item#:97856' in html)
        await c.close(); await b.close()

asyncio.run(main())
