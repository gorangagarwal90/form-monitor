import argparse,csv,json,re,time,io
from pathlib import Path
from urllib.parse import urlparse,urljoin,parse_qsl,urlencode,urlunparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from PIL import Image

BASE='https://storebe.gemsny.com'
ASSET='https://assets.gemsny.com/'
CLIENT='cc4c1d93b1a0'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
GEMS=('sapphire','ruby','emerald','alexandrite','tanzanite','aquamarine','tsavorite','morganite','peridot','tourmaline','topaz','amethyst','citrine','spinel','garnet','diamond')

def rows(p):
 with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def num(v):
 try:return int(float(str(v or 0).replace(',','').strip()))
 except:return 0
def write(p,fields,rr):
 with open(p,'w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([{k:r.get(k,'') for k in fields} for r in rr])
def infer(r):
 s=(urlparse(r['category_url']).path+' '+(r.get('category_name') or '')).lower()
 if 'earring' in s:f='earring'
 elif 'pendant' in s:f='pendant'
 elif 'bracelet' in s:f='bracelet'
 elif 'wedding' in s or 'band' in s:f='band'
 elif 'ring' in s:f='ring'
 else:f='other'
 g=next((x for x in GEMS if x in s),'')
 if 'swiss topaz' in s or 'swiss-topaz' in s:g='swiss-topaz'
 if 'london topaz' in s or 'london-topaz' in s:g='london-topaz'
 if 'pink tourmaline' in s or 'pink-tourmaline' in s:g='tourmaline'
 style=''
 for x in ('halo','solitaire','sidestone','side-stone','three-stone','two-stone','vintage','dangle','hoop','stud','half-eternity','three-fourth-eternity','eternity'):
  if x in s or x.replace('-',' ') in s:style=x
 return f,g,style
def exclusions(roots):
 out=set()
 for root in roots:
  if not root:continue
  for p in Path(root).rglob('coverage*.csv'):
   try:
    for r in rows(p):
     if r.get('status') in ('COMPLETE','NON_PRODUCT_PAGE') and r.get('category_url'):out.add(r['category_url'])
   except:pass
 return out
def uniqpairs(xs):
 out=[];seen=set()
 for ep,p in xs:
  k=(ep,json.dumps(p,sort_keys=True))
  if k not in seen:seen.add(k);out.append((ep,p))
 return out
def candidates(r):
 f,g,style=infer(r);xs=[]
 vals=[]
 if g: vals=[g,g.replace('-',' '),g.replace('-',' ').title()]
 if f in ('earring','pendant','bracelet'):
  ep={'earring':'/v3/preset-earring','pendant':'/v3/preset-pendant','bracelet':'/v3/preset-bracelet'}[f]
  xs += [(ep,{}),(ep,{'client_id':CLIENT})]
  for v in vals:
   xs += [(ep,{'gem_type':v}),(ep,{'client_id':CLIENT,'gem_type':v})]
  if style:
   for k in ('style','category'):
    xs += [(ep,{k:style}),(ep,{'client_id':CLIENT,k:style})]
    for v in vals: xs += [(ep,{'gem_type':v,k:style}),(ep,{'client_id':CLIENT,'gem_type':v,k:style})]
 elif f=='band':
  ep='/v3/preset-band';xs += [(ep,{}),(ep,{'client_id':CLIENT})]
  for v in vals:
   for k in ('stone_type','gem_type'):
    xs += [(ep,{k:v}),(ep,{'client_id':CLIENT,k:v})]
  if style:
   sv=style.replace('-',' ').title()
   for k in ('category','band_type','setting_type'):
    xs += [(ep,{k:sv}),(ep,{'client_id':CLIENT,k:sv})]
    for v in vals:xs += [(ep,{'stone_type':v,k:sv}),(ep,{'client_id':CLIENT,'stone_type':v,k:sv})]
 elif f=='ring':
  ep='/ring/v2';xs += [(ep,{}),(ep,{'type':'Preset Ring'})]
  for v in vals:
   for k in ('stoneType','gemType'):
    xs += [(ep,{k:v}),(ep,{'type':'Preset Ring',k:v})]
  if style:
   sv=style.replace('-',' ').title()
   xs += [(ep,{'style':sv}),(ep,{'type':'Preset Ring','style':sv})]
   for v in vals:xs += [(ep,{'type':'Preset Ring','style':sv,'stoneType':v}),(ep,{'type':'Preset Ring','style':sv,'gemType':v})]
 return uniqpairs(xs)
def flatten(o):
 if isinstance(o,list):return o
 if not isinstance(o,dict):return []
 for k in ('items','products','results','rows','records','data'):
  v=o.get(k)
  if isinstance(v,list):return v
  if isinstance(v,dict):
   z=flatten(v)
   if z:return z
 return []
def pagination(o):
 if not isinstance(o,dict):return {}
 p=o.get('pagination')
 if isinstance(p,dict):return p
 for v in o.values():
  if isinstance(v,dict):
   z=pagination(v)
   if z:return z
 return {}
def key(d):
 for k in ('sku','main_sku','product_sku','item_number','id','product_id'):
  v=d.get(k) if isinstance(d,dict) else None
  if v not in (None,''):return str(v)
 return ''
def image_paths(d):
 out=[]
 def add(v):
  if isinstance(v,str) and re.search(r'\.(?:jpe?g|png|webp)(?:\?|$)',v,re.I) and not re.search(r'certificate|logo|icon|badge|placeholder',v,re.I):out.append(v)
 def walk(v,depth=0):
  if depth>3:return
  if isinstance(v,str):add(v)
  elif isinstance(v,list):
   for x in v:walk(x,depth+1)
  elif isinstance(v,dict):
   for kk,vv in v.items():
    if re.search(r'image|media|photo|thumb|url|src',str(kk),re.I):walk(vv,depth+1)
 if isinstance(d,dict):
  for kk,v in d.items():
   if re.search(r'image|media|photo|thumb',str(kk),re.I):walk(v)
 return out
def norm_image(v):
 if not v:return ''
 if v.startswith('//'):u='https:'+v
 elif v.startswith('http'):u=v
 else:u=urljoin(ASSET,v.lstrip('/'))
 try:
  p=urlparse(u);drop={'width','height','w','h','quality','q','format','fit','crop','auto','dpr'}
  q=[(k,x) for k,x in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in drop]
  return urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q),''))
 except:return u
def choose_image(d):
 pp=image_paths(d)
 for p in pp:
  if 'image-jewelry/' in p:return norm_image(p)
 for p in pp:
  if 'video-jewelry/' not in p and 'imposed-' not in p:return norm_image(p)
 return norm_image(pp[0]) if pp else ''
def probe(sess,r,expected):
 diag=[]
 for ep,p in candidates(r):
  try:
   z=sess.get(BASE+ep,params=p,timeout=30);rec=f'{ep} {p} => {z.status_code}'
   if z.status_code!=200:diag.append(rec);continue
   o=z.json();it=flatten(o);pg=pagination(o);tot=num(pg.get('totalCount') or pg.get('total') or 0)
   diag.append(rec+f' items={len(it)} total={tot}')
   if it and expected>0 and tot==expected:return ep,p,pg,diag
  except Exception as e:diag.append(f'{ep} {p} => {type(e).__name__}')
 return None,None,None,diag
def page_signature(items):return tuple(key(x) for x in items[:8] if key(x))
def pagination_contract(sess,ep,base,pg):
 size=num(pg.get('pageSize')) or 24
 for sk in ('page_size','pageSize'):
  try:
   p1=dict(base);p1.update({'page':1,sk:min(size,100)})
   p2=dict(base);p2.update({'page':2,sk:min(size,100)})
   r1=sess.get(BASE+ep,params=p1,timeout=30);r2=sess.get(BASE+ep,params=p2,timeout=30)
   if r1.status_code==200 and r2.status_code==200:
    a=flatten(r1.json());b=flatten(r2.json())
    if a and b and page_signature(a)!=page_signature(b):return sk,min(size,100)
  except:pass
 # page itself often works while size key is rejected
 try:
  r1=sess.get(BASE+ep,params={**base,'page':1},timeout=30);r2=sess.get(BASE+ep,params={**base,'page':2},timeout=30)
  if r1.status_code==200 and r2.status_code==200 and page_signature(flatten(r1.json()))!=page_signature(flatten(r2.json())):return '',size
 except:pass
 return None,None
def enumerate_all(sess,ep,base,pg,expected):
 sk,size=pagination_contract(sess,ep,base,pg)
 if sk is None:return {},0,'pagination contract not found'
 seen={};pages=0;maxp=num(pg.get('totalPages')) or ((expected+size-1)//size)
 for page in range(1,maxp+1):
  p=dict(base);p['page']=page
  if sk:p[sk]=size
  r=sess.get(BASE+ep,params=p,timeout=45);r.raise_for_status();it=flatten(r.json());pages+=1
  if not it:break
  for d in it:
   k=key(d)
   if k:seen.setdefault(k,d)
  if len(seen)>=expected:break
  time.sleep(.03)
 return seen,pages,''
def measure(sess,u):
 if not u:return 0,0,'missing source image URL'
 try:
  r=sess.get(u,headers={'User-Agent':UA,'Referer':'https://www.gemsny.com/'},timeout=35,stream=True);r.raise_for_status()
  with Image.open(r.raw) as im:return int(im.width),int(im.height),''
 except Exception as e:return 0,0,f'{type(e).__name__}: {e}'[:250]
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--baseline-coverage',required=True);ap.add_argument('--exclude-root',action='append',default=[]);ap.add_argument('--output-dir',required=True);ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--shard-count',type=int,required=True);ap.add_argument('--min-width',type=int,default=500);ap.add_argument('--min-height',type=int,default=500);a=ap.parse_args()
 od=Path(a.output_dir);od.mkdir(parents=True,exist_ok=True);base=rows(a.baseline_coverage);exc=exclusions(a.exclude_root)
 targets=[r for r in base if r.get('category_url') and r.get('status')!='COMPLETE' and r['category_url'] not in exc and num(r.get('expected_products_if_detected'))>0]
 mine=[r for i,r in enumerate(targets) if i%a.shard_count==a.shard_index]
 sess=requests.Session();sess.headers.update({'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Origin':'https://www.gemsny.com','Referer':'https://www.gemsny.com/'})
 cov=[];imgs=[]
 for n,r in enumerate(mine,1):
  name=r.get('category_name') or r['category_url'];url=r['category_url'];expected=num(r.get('expected_products_if_detected'));ep,p,pg,diag=probe(sess,r,expected)
  if not ep:
   cov.append({'category_name':name,'category_url':url,'status':'UNRESOLVED - NO EXACT API CONTRACT','expected_products_if_detected':expected,'unique_products_detected':0,'images_checked':0,'failed_images':0,'image_errors':0,'note':' | '.join(diag[-8:])});print(f'[{n}/{len(mine)}] unresolved {name}',flush=True);continue
  try:seen,pages,err=enumerate_all(sess,ep,p,pg,expected)
  except Exception as e:seen={};pages=0;err=f'{type(e).__name__}: {e}'
  if len(seen)!=expected:
   cov.append({'category_name':name,'category_url':url,'status':'UNRESOLVED - API PRODUCT COUNT','expected_products_if_detected':expected,'unique_products_detected':len(seen),'images_checked':0,'failed_images':0,'image_errors':0,'note':f'{ep}; pages={pages}; {err}'});continue
  sources={choose_image(d) for d in seen.values()};dims={}
  with ThreadPoolExecutor(max_workers=24) as ex:
   fs={ex.submit(measure,sess,u):u for u in sources}
   for f in as_completed(fs):dims[fs[f]]=f.result()
  cat=[]
  for k,d in seen.items():
   u=choose_image(d);w,h,e=dims.get(u,(0,0,'not measured'));st='ERROR' if e else ('PASS' if w>=a.min_width and h>=a.min_height else 'FAIL')
   cat.append({'category_name':name,'category_url':url,'category_page_url':url,'page_number':'API','sku':k,'product_key':k,'product_title':str(d.get('name') or d.get('title') or ''),'product_url':str(d.get('redirect_url') or ''),'image_url':u,'original_url':u,'original_width':w,'original_height':h,'original_status':st,'threshold_width':a.min_width,'threshold_height':a.min_height,'status':st,'compliance_basis':'ORIGINAL SOURCE','compliance_width':w,'compliance_height':h,'failure_reason':e if e else ('' if st=='PASS' else f'{w}x{h} below {a.min_width}x{a.min_height}'),'audit_mode':'REMAINING_EXACT_API_V7','api_endpoint':ep,'audited_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
  imgs.extend(cat);cov.append({'category_name':name,'category_url':url,'status':'COMPLETE','expected_products_if_detected':expected,'unique_products_detected':len(seen),'images_checked':len(cat),'failed_images':sum(x['status']=='FAIL' for x in cat),'image_errors':sum(x['status']=='ERROR' for x in cat),'note':f'Exact backend total matched live baseline; {ep}; pages={pages}; every expected SKU enumerated.'});print(f'[{n}/{len(mine)}] COMPLETE {name}: {len(seen)}',flush=True)
 cf=['category_name','category_url','status','expected_products_if_detected','unique_products_detected','images_checked','failed_images','image_errors','note'];imf=['category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_url','original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis','compliance_width','compliance_height','failure_reason','audit_mode','api_endpoint','audited_at']
 write(od/'coverage.csv',cf,cov);write(od/'images.csv',imf,imgs);(od/'summary.json').write_text(json.dumps({'shard':a.shard_index,'targets':len(mine),'complete':sum(x['status']=='COMPLETE' for x in cov),'unresolved':sum(x['status']!='COMPLETE' for x in cov),'products':sum(num(x['unique_products_detected']) for x in cov if x['status']=='COMPLETE'),'images':len(imgs)},indent=2))
if __name__=='__main__':main()
