import asyncio, json
from playwright.async_api import async_playwright

PAGE_URL='https://www.gemsny.com/lab-diamonds/basic-search/pear'
API='https://storebe.gemsny.com/diamond/diamonds?category=Lab-Grown%20Diamonds&client_id=null&shape=Pear&price=&carat=&color=&cut=&clarity=&symmetry=&polish=&depth=&table=&fluorescence=&lwRatio=&certificate=IGI,%20GIA&pair=&mediashipping=&page=1&pageSize=2&sortBy=priceLowToHigh&shippingOptions=exp&settingSku='
COUNT='https://storebe.gemsny.com/diamond/diamonds/count?category=Lab-Grown%20Diamonds&client_id=null&shape=Pear&price=&carat=&color=&cut=&clarity=&symmetry=&polish=&depth=&table=&fluorescence=&lwRatio=&certificate=IGI,%20GIA&pair=&mediashipping=&sortBy=priceLowToHigh&shippingOptions=exp&settingSku='

async def main():
  async with async_playwright() as p:
    browser=await p.chromium.launch(headless=True)
    ctx=await browser.new_context(viewport={'width':1440,'height':1200}, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36')
    page=await ctx.new_page()
    await page.goto(PAGE_URL, wait_until='domcontentloaded', timeout=90000)
    await page.wait_for_timeout(4000)
    for label,url in [('COUNT',COUNT),('DATA',API)]:
      r=await ctx.request.get(url, timeout=60000)
      print(f'==={label} STATUS=== {r.status}')
      text=await r.text()
      print(text[:50000])
    sample=await page.evaluate(r'''() => [...document.querySelectorAll('.GridProductInner, [class*="GridProductInner"]')].slice(0,3).map(e=>({text:(e.innerText||'').slice(0,1000),html:e.outerHTML.slice(0,6000)}))''')
    print('===DOM SAMPLE===')
    print(json.dumps(sample,indent=2))
    await browser.close()

# Recovery diagnostic rerun 2026-09-12
asyncio.run(main())
