import argparse,csv,json
from collections import Counter,defaultdict
from pathlib import Path
import xlsxwriter

def rows(path):
    with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def num(v):
    try:return int(float(str(v).replace(',','').strip()))
    except:return None

def find_one(root,name):
    hits=list(Path(root).rglob(name))
    if len(hits)!=1: raise SystemExit(f'Expected exactly one {name} under {root}, found {len(hits)}: {hits}')
    return hits[0]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--baseline',required=True);ap.add_argument('--diamond',required=True);ap.add_argument('--exception',required=True);ap.add_argument('--server',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    sources=[('baseline',Path(a.baseline)),('diamond',Path(a.diamond)),('exception',Path(a.exception))]
    server_roots=[]
    for cov in Path(a.server).rglob('coverage.csv'): server_roots.append(cov.parent)
    if len(server_roots)!=2: raise SystemExit(f'Expected 2 server-grid coverage work units, found {len(server_roots)}')
    recoveries=sources[1:]+[(f'server{i}',p) for i,p in enumerate(server_roots)]

    base_cov=rows(find_one(a.baseline,'coverage.csv'));base_img=rows(find_one(a.baseline,'all_images.csv'))
    base_urls={r.get('category_url','') for r in base_cov if r.get('category_url')}
    if len(base_urls)!=700: raise SystemExit(f'Baseline must define exactly 700 category URLs, got {len(base_urls)}')

    rec_cov=[];rec_img=[];target_urls=set();source_stats={}
    for label,root in recoveries:
        c=rows(find_one(root,'coverage.csv')); im=rows(find_one(root,'all_images.csv'))
        urls={r.get('category_url','') for r in c if r.get('category_url')}
        if not urls: raise SystemExit(f'{label}: zero category URLs')
        if any(r.get('status')!='COMPLETE' for r in c): raise SystemExit(f'{label}: non-COMPLETE coverage row found')
        overlap=target_urls & urls
        if overlap: raise SystemExit(f'Recovery category overlap in {label}: {sorted(overlap)[:5]}')
        target_urls|=urls;rec_cov.extend(c);rec_img.extend(im)
        source_stats[label]={'categories':len(urls),'images':len(im)}
    if len(target_urls)!=37: raise SystemExit(f'Expected 37 recovered categories (27 diamond + 8 API exception + 2 server-grid), got {len(target_urls)}')
    if not target_urls <= base_urls: raise SystemExit(f'Recovery includes unknown baseline URLs: {sorted(target_urls-base_urls)}')

    final_cov=[r for r in base_cov if r.get('category_url') not in target_urls]+rec_cov
    final_img=[r for r in base_img if r.get('category_url') not in target_urls]+rec_img
    byurl={};dups=[]
    for r in final_cov:
        u=r.get('category_url','')
        if u in byurl:dups.append(u)
        byurl[u]=r
    final_urls=set(byurl)
    missing=sorted(base_urls-final_urls);extra=sorted(final_urls-base_urls)
    noncomplete=[u for u,r in byurl.items() if r.get('status')!='COMPLETE']
    if dups or missing or extra or len(final_urls)!=700 or noncomplete:
        raise SystemExit(f'Coverage validation failed categories={len(final_urls)} duplicates={len(dups)} missing={len(missing)} extra={len(extra)} noncomplete={len(noncomplete)}')
    if not final_img: raise SystemExit('Zero final image rows')

    actual=Counter(r.get('category_url','') for r in final_img)
    count_mismatch=[]
    for u,r in byurl.items():
        exp=num(r.get('images_checked'))
        if exp is not None and actual[u]!=exp: count_mismatch.append({'category_url':u,'coverage_images_checked':exp,'actual_rows':actual[u]})
    if count_mismatch: raise SystemExit(f'Image-row accounting mismatch for {len(count_mismatch)} categories; first={count_mismatch[:3]}')

    all_fields=[]
    for r in final_img:
        for k in r:
            if k not in all_fields:all_fields.append(k)
    cov_fields=[]
    for r in final_cov:
        for k in r:
            if k not in cov_fields:cov_fields.append(k)
    with open(out/'all_images.csv','w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=all_fields,extrasaction='ignore');w.writeheader();w.writerows(final_img)
    with open(out/'coverage.csv','w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cov_fields,extrasaction='ignore');w.writeheader();w.writerows(final_cov)

    def stat(r):return str(r.get('status','')).upper()
    failed=[r for r in final_img if stat(r)=='FAIL']
    errors=[r for r in final_img if stat(r)=='ERROR']
    issues=failed+errors
    with open(out/'failed_and_error_images.csv','w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=all_fields,extrasaction='ignore');w.writeheader();w.writerows(issues)

    unique_products=set(); unique_images=set()
    for r in final_img:
        pk=r.get('product_key') or r.get('product_id') or r.get('sku') or r.get('product_url')
        if pk: unique_products.add((r.get('category_url',''),pk))
        iu=r.get('original_url') or r.get('image_url')
        if iu: unique_images.add(iu)
    summary={'validation':'PASS','rule':'source image width >= 500 AND height >= 500','categories_expected':700,'categories_complete':700,'recovered_categories':len(target_urls),'image_checks':len(final_img),'category_product_assignments':len(unique_products),'unique_source_image_urls':len(unique_images),'below_500x500':len(failed),'image_errors':len(errors),'issues_total':len(issues),'source_components':source_stats,'missing_category_urls':[],'noncomplete_category_urls':[],'image_count_mismatches':[]}
    (out/'validation_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')

    wb=xlsxwriter.Workbook(out/'GemsNY_Full_Image_Audit_500x500.xlsx',{'constant_memory':True})
    ws=wb.add_worksheet('Summary');
    for i,(k,v) in enumerate(summary.items()):
        ws.write(i,0,k);ws.write(i,1,json.dumps(v) if isinstance(v,(dict,list)) else v)
    def sheet(name,data,fields):
        s=wb.add_worksheet(name);[s.write(0,j,h) for j,h in enumerate(fields)]
        for i,r in enumerate(data,1):
            for j,h in enumerate(fields):s.write(i,j,str(r.get(h,''))[:32767])
        s.freeze_panes(1,0);s.autofilter(0,0,max(1,len(data)),max(0,len(fields)-1))
    sheet('Category Coverage',final_cov,cov_fields)
    # Actionable failures/errors fit safely below Excel's row limit; complete raw rows remain in all_images.csv.
    max_rows=900000
    for n,start in enumerate(range(0,len(issues),max_rows),1): sheet(f'Issues {n}',issues[start:start+max_rows],all_fields)
    wb.close()
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
