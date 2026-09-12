import argparse,csv,io,json,os,re,time
from pathlib import Path
from urllib.parse import urlencode,urlparse,parse_qsl,urlunparse
import requests
from PIL import Image

BASE='https://storebe.gemsny.com'
TARGETS=[
 {'name':'Natural Diamonds Pair','url':'https://www.gemsny.com/natural-diamond-pair/basic-search','api':'/diamond/pair-diamonds','params':{'category':'Natural Diamonds Pair','shape':'','carat':'','color':'','clarity':'','certificate':'','price':'','mediashipping':'','sortBy':'priceLowToHigh','shippingOptions':'exp','cut':'','settingSku':''},'page':'page','size':'pageSize','count':'/diamond/diamonds-pair/count'},
 {'name':'Natural Diamonds Pair - MYO Earrings','url':'https://www.gemsny.com/natural-diamond-pair/basic-search?myo=earrings','api':'/diamond/pair-diamonds','params':{'category':'Natural Diamonds Pair','shape':'','carat':'','color':'','clarity':'','certificate':'','price':'','mediashipping':'','sortBy':'priceLowToHigh','shippingOptions':'exp','cut':'','settingSku':''},'page':'page','size':'pageSize','count':'/diamond/diamonds-pair/count'},
 {'name':'Ready to Ship Earrings','url':'https://www.gemsny.com/ready-to-ship/earrings','api':'/ready-to-ship-product','params':{'type':'earring','gem_type':'','ring_size':'','metal':'','shape':'','carat':'','price':'','sort_by':'priceLowToHigh','client_id':'cc4c1d93b1a0'},'page':'page','size':'page_size'},
 {'name':'Sapphire Three Fourth Eternity Wedding Rings','url':'https://www.gemsny.com/wedding-rings/sapphire/three-fourth-eternity','api':'/v3/preset-band','params':{'client_id':'cc4c1d93b1a0','stone_type':'Sapphire','shape':'','category':'Three Fourth Eternity','metal':'','total_weight':'','price':'','band_width':'','setting_type':'','band_type':'','sort_by':'featured'},'page':'page','size':'page_size'},
 {'name':'Ruby Bracelets','url':'https://www.gemsny.com/ruby-bracelets/preset','api':'/v3/preset-bracelet','params':{'client_id':'cc4c1d93b1a0','gem_type':'ruby','color':'','category':'','metal':'','shape':'','price':'','sortBy':'featured','sortByShipping':''},'page':'page','size':'pageSize'},
 {'name':'Natural Swiss Topaz Pendants','url':'https://www.gemsny.com/natural-swiss-topaz-pendants/preset','api':'/v3/preset-pendant','params':{'client_id':'cc4c1d93b1a0','type':'Preset Pendant','gem_type':'swiss-topaz','shape':'','style':'','metal':'','price':'','gemstone_quality_grade':'','diamond_quality_grade':'','carat':'','ready_to_ship':'false','sort_by':'featured'},'page':'page','size':'page_size'},
 {'name':'Engagement Ring Settings','url':'https://www.gemsny.com/engagement-rings/setting','api':'/ring/v2','params':{'type':'Myo Ring','style':'Engagement','metal':'','centerStoneShape':'','price':'','width':'','sideStoneShape':'','centerStoneSetting':'','sideStoneSetting':'','collection':'','sortBy':'featured','stoneType':''},'page':'page','size':'pageSize'},
 {'name':'Ready to Ship Rings','url':'https://www.gemsny.com/ready-to-ship/rings','api':'/ready-to-ship-product','params':{'type':'ring','gem_type':'','ring_size':'','metal':'','shape':'','carat':'','price':'','sort_by':'priceLowToHigh','client_id':'cc4c1d93b1a0'},'page':'page','size':'page_size'},
]

def flatten_items(obj):
    if isinstance(obj,list): return obj
    if not isinstance(obj,dict): return []
    for k in ('items','data','products','results','result','rows'):
        v=obj.get(k)
        if isinstance(v,list): return v
        if isinstance(v,dict):
            x=flatten_items(v)
            if x:return x
    best=[]
    for v in obj.values():
        x=flatten_items(v)
        if len(x)>len(best):best=x
    return best

def val(d,*keys):
    for k in keys:
        v=d.get(k) if isinstance(d,dict) else None
        if v not in (None,''): return v
    return ''

def image_url(d):
    if not isinstance(d,dict):return ''
    for k in ('image','image_url','imageUrl','img','thumb','thumbnail','main_image','mainImage','product_image','productImage'):
        v=d.get(k)
        if isinstance(v,str) and v.startswith('http'):return v
        if isinstance(v,dict):
            for vv in v.values():
                if isinstance(vv,str) and vv.startswith('http'):return vv
    for k,v in d.items():
        if 'image' in k.lower() and isinstance(v,str) and v.startswith('http'):return v
    return ''

def product_key(d):
    return str(val(d,'sku','item_number','itemNumber','product_sku','productSku','id','product_id','productId','code'))

def product_url(d):
    u=val(d,'redirect_url','url','product_url','productUrl','slug')
    if not u:return ''
    if str(u).startswith('http'):return str(u)
    return 'https://www.gemsny.com/'+str(u).lstrip('/')

def find_count(obj):
    if isinstance(obj,(int,float)):return int(obj)
    if not isinstance(obj,dict):return 0
    for k in ('count','total','total_count','totalCount','recordsTotal','result_count'):
        v=obj.get(k)
        if isinstance(v,(int,float)):return int(v)
        if isinstance(v,str) and v.isdigit():return int(v)
    for v in obj.values():
        c=find_count(v)
        if c:return c
    return 0

def dims(sess,url,cache):
    if not url:return (0,0,'ERROR','missing image url')
    if url in cache:return cache[url]
    try:
        r=sess.get(url,timeout=30,headers={'User-Agent':'Mozilla/5.0'},stream=True)
        if r.status_code!=200:
            out=(0,0,'ERROR',f'HTTP {r.status_code}')
        else:
            data=r.content
            im=Image.open(io.BytesIO(data)); out=(int(im.width),int(im.height),'OK','')
    except Exception as e: out=(0,0,'ERROR',str(e)[:200])
    cache[url]=out; return out

def write_csv(path,fields,rows):
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([{k:r.get(k,'') for k in fields} for r in rows])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output-dir',required=True);ap.add_argument('--min-width',type=int,default=500);ap.add_argument('--min-height',type=int,default=500);a=ap.parse_args()
    out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    sess=requests.Session();sess.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36','Accept':'application/json,text/plain,*/*'})
    rows=[];cov=[];cache={}
    for t in TARGETS:
        seen={}; page=1; expected=0; pages=0; note=''; repeats=0
        if t.get('count'):
            try: expected=find_count(sess.get(BASE+t['count'],params=t['params'],timeout=60).json())
            except Exception as e: note=f'count endpoint error: {e}'
        while True:
            p=dict(t['params']);p[t['page']]=page;p[t['size']]=100
            try:
                r=sess.get(BASE+t['api'],params=p,timeout=90);r.raise_for_status();obj=r.json();items=flatten_items(obj)
            except Exception as e:
                note=(note+'; ' if note else '')+f'page {page} error {e}';break
            pages+=1
            if not expected: expected=find_count(obj)
            if not items: break
            new=0
            for d in items:
                k=product_key(d)
                if not k:k=json.dumps(d,sort_keys=True)[:300]
                if k in seen:continue
                seen[k]=d;new+=1
            print(t['name'],page,'items',len(items),'new',new,'unique',len(seen),'expected',expected,flush=True)
            if new==0:
                repeats+=1
                if repeats>=2:break
            else: repeats=0
            if expected and len(seen)>=expected:break
            page+=1
            time.sleep(.15)
        for k,d in seen.items():
            iu=image_url(d);w,h,ds,err=dims(sess,iu,cache)
            status='ERROR' if ds=='ERROR' else ('PASS' if w>=a.min_width and h>=a.min_height else 'FAIL')
            rows.append({'category_name':t['name'],'category_url':t['url'],'category_page_url':t['url'],'page_number':'API','sku':product_key(d),'product_key':product_key(d),'product_title':str(val(d,'title','name','product_name','productName')),'product_url':product_url(d),'image_alt':str(val(d,'title','name')),'card_family':'API_RECOVERY','image_url':iu,'current_src_url':iu,'served_width':w,'served_height':h,'served_status':ds,'declared_src_width':w,'declared_src_height':h,'declared_src_status':ds,'original_url':iu,'original_width':w,'original_height':h,'original_status':ds,'threshold_width':a.min_width,'threshold_height':a.min_height,'status':status,'compliance_basis':'API source image','compliance_width':w,'compliance_height':h,'failure_reason':err if status=='ERROR' else ('' if status=='PASS' else f'{w}x{h} below {a.min_width}x{a.min_height}'),'audit_mode':'EXCEPTION_API','audited_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
        st='COMPLETE'
        if expected and len(seen)<expected:st='INCOMPLETE - PRODUCT COUNT'
        if not seen:st='INCOMPLETE - NO PRODUCTS'
        cov.append({'category_name':t['name'],'category_url':t['url'],'status':st,'pages_visited':pages,'pagination_detected':True,'expected_last_page':'','expected_products_if_detected':expected,'unique_products_detected':len(seen),'images_checked':len(seen),'failed_images':sum(1 for r in rows if r['category_url']==t['url'] and r['status']=='FAIL'),'image_errors':sum(1 for r in rows if r['category_url']==t['url'] and r['status']=='ERROR'),'note':note or f'API exhausted at page {pages}; unique={len(seen)} expected={expected}'})
    img_fields=['category_name','category_url','category_page_url','page_number','sku','product_key','product_title','product_url','image_alt','card_family','image_url','current_src_url','served_width','served_height','served_status','declared_src_width','declared_src_height','declared_src_status','original_url','original_width','original_height','original_status','threshold_width','threshold_height','status','compliance_basis','compliance_width','compliance_height','failure_reason','audit_mode','audited_at']
    cov_fields=['category_name','category_url','status','pages_visited','pagination_detected','expected_last_page','expected_products_if_detected','unique_products_detected','images_checked','failed_images','image_errors','note']
    write_csv(out/'all_images.csv',img_fields,rows);write_csv(out/'failed_images.csv',img_fields,[r for r in rows if r['status'] in ('FAIL','ERROR')]);write_csv(out/'coverage.csv',cov_fields,cov)
    with open(out/'summary.json','w') as f:json.dump({'categories':len(cov),'complete':sum(1 for x in cov if x['status']=='COMPLETE'),'images':len(rows),'fails':sum(1 for x in rows if x['status']=='FAIL'),'errors':sum(1 for x in rows if x['status']=='ERROR')},f,indent=2)
    if any(x['status']!='COMPLETE' for x in cov):raise SystemExit('Strict exception API recovery incomplete')
if __name__=='__main__':main()
