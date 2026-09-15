import requests
import remaining_browser_recovery_v12 as v12

core = v12.core
_ORIGINAL_SYNC_PLAYWRIGHT = core.sync_playwright

# V13 keeps every strict V12 rule (exact expected product count and original/source
# image >=500x500 validation) but hardens both HTTP and browser execution. GitHub
# runners can receive HTTP 403 for direct category SSR and an empty client shell.
# Warm the public origin first, retain cookies, and use a realistic headed browser
# context before visiting unresolved categories. No partial category is accepted.

_UAS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
    'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
]

def _session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': _UAS[0],
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Referer': 'https://www.gemsny.com/',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Upgrade-Insecure-Requests': '1',
    })
    try:
        s.get('https://www.gemsny.com/', timeout=30, allow_redirects=True)
    except Exception:
        pass
    return s

def _get(sess, url):
    last = ''
    for ua in _UAS:
        try:
            r = sess.get(url, timeout=45, allow_redirects=True, headers={'User-Agent': ua})
            last = f'HTTP {r.status_code}'
            if r.status_code != 200:
                continue
            txt = r.text or ''
            if len(txt) < 500:
                last = f'short HTML {len(txt)} bytes'
                continue
            return txt, ''
        except Exception as e:
            last = f'{type(e).__name__}: {e}'[:220]
    return '', last or 'no response'

v12._session = _session
v12._get = _get

class _ContextProxy:
    def __init__(self, ctx):
        self._ctx = ctx
        self._warmed = False
        try:
            ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                window.chrome = window.chrome || {runtime: {}};
            """)
        except Exception:
            pass
    @property
    def request(self):
        return self._ctx.request
    def new_page(self):
        p = self._ctx.new_page()
        if not self._warmed:
            try:
                p.goto('https://www.gemsny.com/', wait_until='domcontentloaded', timeout=45000)
                p.wait_for_timeout(2500)
            except Exception:
                pass
            self._warmed = True
        return p
    def __getattr__(self, name):
        return getattr(self._ctx, name)

class _BrowserProxy:
    def __init__(self, browser):
        self._browser = browser
    def new_context(self, *args, **kwargs):
        kwargs.setdefault('user_agent', _UAS[0])
        kwargs.setdefault('locale', 'en-US')
        kwargs.setdefault('timezone_id', 'America/New_York')
        kwargs.setdefault('viewport', {'width': 1440, 'height': 1100})
        kwargs.setdefault('extra_http_headers', {
            'Accept-Language': 'en-US,en;q=0.9',
            'Upgrade-Insecure-Requests': '1',
        })
        return _ContextProxy(self._browser.new_context(*args, **kwargs))
    def __getattr__(self, name):
        return getattr(self._browser, name)

class _ChromiumProxy:
    def __init__(self, chromium):
        self._chromium = chromium
    def launch(self, *args, **kwargs):
        kwargs['headless'] = False
        args_list = list(kwargs.get('args') or [])
        for arg in ['--disable-blink-features=AutomationControlled','--window-size=1440,1100','--disable-dev-shm-usage','--no-sandbox']:
            if arg not in args_list:
                args_list.append(arg)
        kwargs['args'] = args_list
        return _BrowserProxy(self._chromium.launch(*args, **kwargs))
    def __getattr__(self, name):
        return getattr(self._chromium, name)

class _PWProxy:
    def __init__(self, pw):
        self._pw = pw
        self.chromium = _ChromiumProxy(pw.chromium)
    def __getattr__(self, name):
        return getattr(self._pw, name)

class _PWManager:
    def __init__(self):
        self._manager = _ORIGINAL_SYNC_PLAYWRIGHT()
    def __enter__(self):
        return _PWProxy(self._manager.__enter__())
    def __exit__(self, exc_type, exc, tb):
        return self._manager.__exit__(exc_type, exc, tb)

def headed_sync_playwright():
    return _PWManager()

core.sync_playwright = headed_sync_playwright
core.enumerate_browser = v12.enumerate_browser
core.capture_candidates = v12.v11.capture_candidates
core.dom_products = v12.v11.dom_products

if __name__ == '__main__':
    core.main()
