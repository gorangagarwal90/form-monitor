import argparse,csv,itertools,json,re,time
from pathlib import Path
from urllib.parse import urlparse,urlunparse,parse_qsl,urlencode,urljoin
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from PIL import Image

BASE='https://storebe.gemsny.com'
SITE='https://www.gemsny.com/'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36'
CLIENT='cc4c1d93b1a0'
ENDPOINTS={
 'loose':'/gemstone',
 'ring':'/ring/v2',
 'earring':'/v3/preset-earring',
 'pendant':'/v3/preset-pendant',
 'bracelet':'/v3/preset-bracelet',
 'band':'/v3/preset-band',
}
GENERIC={'natural','preset','loose','gemstone','gemstones','jewelry','jewellery','ring','rings','earring','earrings','pendant','pendants','bracelet','bracelets','wedding','band','bands','engagement','collection','collections','shop','all','the','for','with','and','of','in','stone','stones'}
ALIASES={
 'sidestone':['side stone','sidestone'], 'side-stone':['side stone','sidestone'],
 'three-stone':['three stone','3 stone'], 'three':['three stone','3 stone'],
 'half-eternity':['half eternity'], 'three-fourth-eternity':['three fourth eternity','3/4 eternity'],
 'dangle':['dangle','dangling'], 'stud':['stud'], 'hoop':['hoop'], 'halo':['halo'],
 'solitaire':['solitaire'], 'vintage':['vintage'], 'two-stone':['two stone','2 stone'],
 'round':['round'], 'oval':['oval'], 'cushion':['cushion'], 'emerald-cut':['emerald cut'],
 'pear':['pear'], 'marquise':['marquise'], 'princess':['princess'], 'radiant':['radiant'],
 'heart':['heart'], 'asscher':['asscher'], 'trillion':['trillion'],
}

def read_csv(p):
 with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def write_csv(p,fields,rows):
 with open(p,'w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([{k:r.get(k,'') for k in fields} for r in rows])

def as_int(v,d=0):
 try:return int(float(str(v).replace(',','').strip()))
 except:return d

def norm(s):
 s=str(s or '').lower().replace('&',' and ')
 s=re.sub(r'[-_/]+',' ',s);s=re.sub(r'[^a-z0-9. ]+',' ',s);s=re.sub(r'\s+',' ',s).strip()
 return s

def tokens(s):return [x for x in norm(s).split() if len(x)>=3 and x not in GENERIC and not x.isdigit()]

def flatten(obj):
 if isinstance(obj,list):return obj
 if not isinstance(obj,dict):return []
 for k in ('items','data','products','results','result','rows','records'):
  v=obj.get(k)
  if isinstance(v,list):return v
  if isinstance(v,dict):
   x=flatten(v)
   if x:return x
 best=[]
 for v in obj.values():
  x=flatten(v)
  if len(x)>len(best):best=x
 return best

def pagination_total(obj):
 if not isinstance(obj,dict):return 0
 p=obj.get('pagination')
 if isinstance(p,dict):
  for k in ('total','count','totalCount','total_count','recordsTotal','totalRecords'):
   v=p.get(k)
   if isinstance(v,(int,float)):return int(v)
   if isinstance(v,str) and v.replace(',','').isdigit():return int(v.replace(',',''))
 for k in ('total','count','totalCount','total_count','recordsTotal','totalRecords'):
  v=obj.get(k)
  if isinstance(v,(int,float)):return int(v)
 return 0

def key_of(d):
 for k in ('sku','item','item_number','itemNumber','product_sku','productSku','stock_number','stockNumber','product_id','productId','id'):
  v=d.get(k) if isinstance(d,dict) else None
  if v not in (None,''):return str(v)
 return ''

def title_of(d):
 for k in ('title','name','result_page_title','detail_page_title','product_name','productName'):
  v=d.get(k) if isinstance(d,dict) else None
  if v:return str(v)
 return ''

def product_url(d):
 for k in ('redirect_url','redirectUrl','url','product_url','productUrl','detail_url','detailUrl','slug'):
  v=d.get(k) if isinstance(d,dict) else None
  if v:
   s=str(v);return s if s.startswith('http') else urljoin(SITE,s)
 return ''

def source_image(d):
 found=[]
 def walk(x,path=''):
  if isinstance(x,dict):
   for k,v in x.items():walk(v,(path+'.'+str(k)).lower())
  elif isinstance(x,list):
   for v in x[:20]:walk(v,path)
  elif isinstance(x,str):
   s=x.strip()
   if (s.startswith('http://') or s.startswith('https://') or s.startswith('//')) and re.search(r'\.(?:jpe?g|png|webp)(?:\?|$)',s,re.I):
    if re.search(r'logo|icon|badge|sprite|placeholder|loader|certificate',s,re.I):return
    score=0
    if re.search(r'original|source|main|primary',path):score+=8
    if re.search(r'image|photo|picture|media',path):score+=4
    if re.search(r'image-(?:gemstone|jewelry)',s,re.I):score+=5
    if re.search(r'thumb|thumbnail|small|tiny',path+s,re.I):score-=6
    found.append((score,s))
 walk(d)
 if not found:return ''
 s=sorted(found,key=lambda x:x[0],reverse=True)[0][1]
 if s.startswith('//'):s='https:'+s
 try:
  p=urlparse(s);q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in {'width','height','w','h','quality','q','format','fit','crop','auto','dpr'}]
  s=urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),''))
 except:pass
 return s

def family(row):
 t=norm((row.get('category_name') or '')+' '+urlparse(row.get('category_url') or '').path)
 if 'earring' in t:return 'earring'
 if 'pendant' in t or 'necklace' in t:return 'pendant'
 if 'bracelet' in t:return 'bracelet'
 if 'wedding' in t or re.search(r'\bband\b',t):return 'band'
 if 'ring' in t:return 'ring'
 if 'basic search' in t or 'loose' in t or any(g in t for g in ['sapphire','ruby','emerald','alexandrite','tanzanite','aquamarine','tsavorite','morganite','peridot','tourmaline','topaz','amethyst','citrine','spinel','garnet','diamond']):return 'loose'
 return 'other'

def scalar_fields(d,prefix='',depth=0):
 out={}
 if depth>2 or not isinstance(d,dict):return out
 for k,v in d.items():
  kk=(prefix+'.'+str(k) if prefix else str(k)).lower()
  if isinstance(v,(str,int,float,bool)) and v not in ('',None):
   sv=norm(v)
   if sv and len(sv)<=120 and not re.search(r'price|url|image|id$|sku|lead|date|rank|wishlist|carat_weight$|default',kk):out[kk]=sv
  elif isinstance(v,dict):out.update(scalar_fields(v,kk,depth+1))
 return out

def session():
 s=requests.Session();s.headers.update({'User-Agent':UA,'Accept':'application/json,text/plain,*/*','Origin':SITE.rstrip('/'),'Referer':SITE,'Accept-Language':'en-US,en;q=0.9'})
 return s

def fetch_catalog(sess,fam):
 ep=ENDPOINTS[fam];seen={};page=1;stagnant=0;reported=0
 while page<=10000:
  params={'client_id':CLIENT,'page':page,'pageSize':100}
  r=sess.get(BASE+ep,params=params,timeout=60);r.raise_for_status();obj=r.json();items=flatten(obj);reported=max(reported,pagination_total(obj))
  if not items:break
  new=0
  for d in items:
   k=key_of(d) or (product_url(d)+'|'+source_image(d)).strip('|')
   if k and k not in seen:seen[k]=d;new+=1
  stagnant=stagnant+1 if new==0 else 0
  if reported and len(seen)>=reported:break
  if len(items)<2:break
  if stagnant>=2:break
  page+=1;time.sleep(.04)
 return seen,reported,page

def category_terms(row):
 text=norm((row.get('category_name') or '')+' '+urlparse(row.get('category_url') or '').path)
 ts=set(tokens(text))
 phrases=set()
 for k,vals in ALIASES.items():
  if norm(k) in text:
   phrases.update(norm(v) for v in vals)
 for n in (2,3):
  w=text.split()
  for i in range(len(w)-n+1):
   ph=' '.join(w[i:i+n])
   if any(x not in GENERIC for x in ph.split()):phrases.add(ph)
 phrases.update(ts)
 return text,sorted(phrases,key=lambda x:(-len(x),x))

def build_predicates(items,row):
 text,terms=category_terms(row);preds=[];seen=set()
 sample=list(items.items())
 # term-contains predicates across stable scalar fields
 field_values={}
 for k,d in sample:
  for f,v in scalar_fields(d).items():field_values.setdefault(f,{}).setdefault(v,set()).add(k)
 allkeys=set(items)
 for f,vals in field_values.items():
  if len(vals)>300:continue
  for val,ks in vals.items():
   ov=[t for t in terms if t and (t in val or val in t) and len(t)>=3]
   if not ov:continue
   sig=frozenset(ks)
   if not sig or sig==allkeys:continue
   score=max(len(x) for x in ov)*10 + (20 if any(x==val for x in ov) else 0)
   key=('eq',f,val)
   if key not in seen:seen.add(key);preds.append((score,key,sig))
 # field contains explicit term, useful when one field has variants like Blue Sapphire / Pink Sapphire
 for f,vals in field_values.items():
  if len(vals)>300:continue
  for t in terms:
   if len(t)<4:continue
   ks=set()
   for val,s in vals.items():
    if t in val:ks|=s
   sig=frozenset(ks)
   if sig and sig!=allkeys:
    key=('contains',f,t)
    if key not in seen:seen.add(key);preds.append((len(t)*8,key,sig))
 preds.sort(key=lambda x:x[0],reverse=True)
 # keep diverse top predicates to make combination search tractable
 uniq=[];sets=set()
 for p in preds:
  if p[2] in sets:continue
  sets.add(p[2]);uniq.append(p)
  if len(uniq)>=45:break
 return uniq

def select_exact(items,row,expected):
 if not expected:return {},'expected count unavailable'
 preds=build_predicates(items,row)
 # Direct exact-count predicate first.
 exact=[p for p in preds if len(p[2])==expected]
 if exact:
  p=max(exact,key=lambda x:x[0]);return {k:items[k] for k in p[2]},'predicate='+repr(p[1])
 # Intersections of strongest predicates. Require each additional predicate to reduce the set.
 top=preds[:32]
 for n in (2,3,4):
  for combo in itertools.combinations(top,n):
   s=set(combo[0][2]);valid=True
   for p in combo[1:]:
    ns=s.intersection(p[2])
    if len(ns)>=len(s):valid=False;break
    s=ns
    if len(s)<expected:valid=False;break
   if valid and len(s)==expected:
    return {k:items[k] for k in s},'predicates='+repr([p[1] for p in combo])
 return {},f'no exact semantic subset; predicates={[(p[1],len(p[2])) for p in preds[:16]]}'

def measure(sess,u):
 try:
  r=sess.get(u,headers={'User-Agent':UA,'Referer':SITE},timeout=35,stream=True);r.raise_for_status()
  with Image.open(r.raw) as im:return int(im.width),int(im.height),''
 except Exception as e:return 0,0,f'{type(e).__name__}: {e}'[:250]

def exclusions(roots):
 urls=set()
 for root in roots:
  if not root:continue
  for p in Path(root).rglob('coverage*.csv'):
   try:
    for r in read_csv(p):
     if r.get('status') in ('COMPLETE','NON_PRODUCT_PAGE') and r.get('category_url'):urls.add(r['category_url'])
   except:pass
 return urls

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--baseline-coverage',required=True);ap.add_argument('--exclude-root',action='append',default=[]);ap.add_argument('--output-dir',required=True);ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--shard-count',type=int,required=True);ap.add_argument('--min-width',type=int,default=500);ap.add_argument('--min-height',type=int,default=500);a=ap.parse_args()
 out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
 base=read_csv(a.baseline_coverage);exc=exclusions(a.exclude_root)
 targets=[r for r in base if r.get('category_url') and r.get('status')!='COMPLETE' and r['category_url'] not in exc]
 mine=[r for i,r in enumerate(targets) if i%a.shard_count==a.shard_index]
 sess=session();catalogs={};imgs=[];cov=[];fail=0
 for idx,row in enumerate(mine,1):
  name=row.get('category_name') or row['category_url'];url=row['category_url'];expected=as_int(row.get('expected_products_if_detected'));fam=family(row)
  if fam=='other':
   fail+=1;cov.append({'category_name':name,'category_url':url,'status':'INCOMPLETE - UNSUPPORTED FAMILY','expected_products_if_detected':expected,'unique_products_detected':0,'images_checked':0,'failed_images':0,'image_errors':0,'note':'V14 catalog recovery could not classify product family'});continue
  try:
   if fam not in catalogs:
    catalogs[fam]=fetch_catalog(sess,fam)
    print(f'catalog {fam}: unique={len(catalogs[fam][0])} reported={catalogs[fam][1]} pages={catalogs[fam][2]}',flush=True)
   items,reported,pages=catalogs[fam]
   chosen,note=select_exact(items,row,expected)
  except Exception as e:
   chosen={};note=f'{type(e).__name__}: {e}'
  if not chosen:
   fail+=1;cov.append({'category_name':name,'category_url':url,'status':'INCOMPLETE - CATALOG SUBSET','expected_products_if_detected':expected,'unique_products_detected':0,'images_checked':0,'failed_images':0,'image_errors':0,'note':f'family={fam}; catalog={len(catalogs.get(fam,({},0,0))[0])}; {note}'[:1800]});print(f'[{idx}/{len(mine)}] unresolved {name}: expected={expected} {note[:300]}',flush=True);continue
  sources={source_image(d) for d in chosen.values() if source_image(d)}
  if len(sources)!=len(chosen):
   fail+=1;cov.append({'category_name':name,'category_url':url,'status':'INCOMPLETE - SOURCE IMAGE','expected_products_if_detected':expected,'unique_products_detected':len(chosen),'images_checked':0,'failed_images':0,'image_errors':0,'note':f'{len(chosen)-len(sources)} products lacked a source image; {note}'[:1800]});continue
  dims={}
  with ThreadPoolExecutor(max_workers=24) as ex:
   fs={ex.submit(measure,sess,u):u for u in sources}
   for f in as_completed(fs):dims[fs[f]]=f.result()
  cat=[]
  for k,d in chosen.items():
   iu=source_image(d);w,h,err=dims.get(iu,(0,0,'not measured'));st='ERROR' if err else ('PASS' if w>=a.min_width and h>=a.min_height else 'FAIL')
   cat.append({'category_name':name,'category_url':url,'category_page_url':url,'page_number':'API_CATALOG','sku':key_of(d),'product_key':k,'product_title':title_of(d),'product_url':product_url(d),'image_alt':title_of(d),'card_family':'API_CATALOG_RECOVERY','image_url':iu,'original_url':iu,'original_width':w,'original_height':h,'original_status':st,'threshold_width':a.min_width,'threshold_height':a.min_height,'status':st,'compliance_basis':'ORIGINAL SOURCE','compliance_width':w,'compliance_height':h,'failure_reason':err if err else ('' if st=='PASS' else f'{w}x{h} below {a.min_width}x{a.min_height}'),'audit_mode':'REMAINING_CATALOG_RECOVERY_V14','api_endpoint':ENDPOINTS[fam],'audited_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
  errs=sum(1 for r in cat if r['status']=='ERROR');bad=sum(1 for r in cat if r['status']=='FAIL')
  # Image fetch errors are not silently accepted; category remains incomplete until every source is measurable.
  if errs:
   fail+=1;cov.append({'category_name':name,'category_url':url,'status':'INCOMPLETE - IMAGE FETCH','expected_products_if_detected':expected,'unique_products_detected':len(chosen),'images_checked':len(cat),'failed_images':bad,'image_errors':errs,'note':note[:1800]});imgs.extend(cat);continue
  cov.append({'category_name':name,'category_url':url,'status':'COMPLETE','expected_products_if_detected':expected,'unique_products_detected':len(chosen),'images_checked':len(cat),'failed_images':bad,'image_errors':0,'note':f'family={fam}; exact catalog subset; {note}'[:1800]});imgs.extend(cat);print(f'[{idx}/{len(mine)}] COMPLETE {name}: {len(chosen)}/{expected} images={len(cat)} fail500={bad}',flush=True)
 cov_fields=['category_name','category_url','status','expected_products_if_detected','unique_products_detected','images_checked','failed_images','image_errors','note']
 img_fields=['category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_alt','card_family','image_url','original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis','compliance_width','compliance_height','failure_reason','audit_mode','api_endpoint','audited_at']
 write_csv(out/f'coverage_v14_{a.shard_index}.csv',cov_fields,cov);write_csv(out/f'image_details_v14_{a.shard_index}.csv',img_fields,imgs)
 with open(out/f'summary_v14_{a.shard_index}.json','w') as f:json.dump({'shard':a.shard_index,'assigned':len(mine),'complete':sum(r['status']=='COMPLETE' for r in cov),'unresolved':sum(r['status']!='COMPLETE' for r in cov),'images':len(imgs),'fail500':sum(r.get('status')=='FAIL' for r in imgs)},f,indent=2)
 raise SystemExit(2 if fail else 0)

if __name__=='__main__':main()
