import argparse,csv,json,sys
from pathlib import Path
import xlsxwriter

ACCEPTED={'COMPLETE','NON_PRODUCT_PAGE'}
def rows(p):
    with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def num(v):
    try:return int(float(str(v or 0).replace(',','').strip()))
    except:return 0
def find_image_file(folder):
    for n in ('all_images.csv','images.csv'):
        p=folder/n
        if p.exists():return p
    return None
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline',required=True);ap.add_argument('--recovery-root',action='append',default=[]);ap.add_argument('--output',required=True);a=ap.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    bc=list(Path(a.baseline).rglob('coverage.csv'))
    if len(bc)!=1:raise SystemExit(f'baseline coverage count={len(bc)}')
    base_cov=rows(bc[0]);bimg=find_image_file(bc[0].parent)
    if not bimg:raise SystemExit('baseline image file missing')
    base_urls={r.get('category_url','') for r in base_cov if r.get('category_url')}
    if len(base_urls)!=700:raise SystemExit(f'expected 700 baseline categories, got {len(base_urls)}')
    chosen={r['category_url']:(r,bc[0].parent,'baseline') for r in base_cov if r.get('category_url')}
    stats=[]
    for root in a.recovery_root:
        for cp in Path(root).rglob('coverage.csv'):
            try:cr=rows(cp)
            except:continue
            im=find_image_file(cp.parent);label=str(cp.parent);accepted=0
            for r in cr:
                u=r.get('category_url','');st=r.get('status')
                # COMPLETE requires image evidence; NON_PRODUCT_PAGE is an
                # explicitly accounted work unit and correctly has zero images.
                if u in base_urls and ((st=='COMPLETE' and im) or st=='NON_PRODUCT_PAGE'):
                    chosen[u]=(r,cp.parent,label);accepted+=1
            stats.append({'source':label,'coverage_rows':len(cr),'accepted_rows':accepted,'has_images':bool(im)})
    missing=sorted(base_urls-set(chosen));noncomplete=sorted(u for u,(r,_,_) in chosen.items() if r.get('status') not in ACCEPTED)
    cache={};final_cov=[];final_img=[];mismatch=[]
    for u in sorted(base_urls):
        if u not in chosen:continue
        r,folder,label=chosen[u];final_cov.append(r);st=r.get('status')
        if st=='NON_PRODUCT_PAGE':
            if num(r.get('images_checked'))!=0:mismatch.append({'category_url':u,'coverage_images_checked':r.get('images_checked'),'actual_rows':0,'source':label})
            continue
        imgp=find_image_file(folder)
        if not imgp:continue
        k=str(imgp)
        if k not in cache:cache[k]=rows(imgp)
        rr=[x for x in cache[k] if x.get('category_url')==u];final_img.extend(rr)
        exp=num(r.get('images_checked'))
        if exp!=len(rr):mismatch.append({'category_url':u,'coverage_images_checked':exp,'actual_rows':len(rr),'source':label})
    complete=sum(r.get('status')=='COMPLETE' for r in final_cov);nonprod=sum(r.get('status')=='NON_PRODUCT_PAGE' for r in final_cov)
    summary={'validation':'FAIL','rule':'source image width >= 500 AND height >= 500','work_units_expected':700,'product_categories_complete':complete,'non_product_units_accounted':nonprod,'work_units_accounted':complete+nonprod,'missing_categories':missing,'noncomplete_categories':noncomplete,'image_count_mismatches':mismatch,'image_checks':len(final_img),'source_components':stats}
    if missing or noncomplete or mismatch or not final_img or complete+nonprod!=700:
        (out/'validation_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary,indent=2));sys.exit(2)
    fields=[]
    for r in final_img:
        for k in r:
            if k not in fields:fields.append(k)
    cfields=[]
    for r in final_cov:
        for k in r:
            if k not in cfields:cfields.append(k)
    def wr(p,ff,rr):
        with open(p,'w',encoding='utf-8-sig',newline='') as f:w=csv.DictWriter(f,fieldnames=ff,extrasaction='ignore');w.writeheader();w.writerows(rr)
    wr(out/'all_images.csv',fields,final_img);wr(out/'coverage.csv',cfields,final_cov)
    failed=[r for r in final_img if str(r.get('status','')).upper()=='FAIL'];errors=[r for r in final_img if str(r.get('status','')).upper()=='ERROR'];issues=failed+errors;wr(out/'failed_and_error_images.csv',fields,issues)
    prods=set();imgs=set()
    for r in final_img:
        pk=r.get('product_key') or r.get('product_id') or r.get('sku') or r.get('product_url')
        if pk:prods.add((r.get('category_url',''),pk))
        iu=r.get('original_url') or r.get('image_url')
        if iu:imgs.add(iu)
    summary.update({'validation':'PASS','missing_categories':[],'noncomplete_categories':[],'image_count_mismatches':[],'category_product_assignments':len(prods),'unique_source_image_urls':len(imgs),'below_500x500':len(failed),'image_errors':len(errors),'issues_total':len(issues)})
    (out/'validation_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    wb=xlsxwriter.Workbook(out/'GemsNY_Full_Image_Audit_500x500.xlsx',{'constant_memory':True});ws=wb.add_worksheet('Summary')
    for i,(k,v) in enumerate(summary.items()):ws.write(i,0,k);ws.write(i,1,json.dumps(v) if isinstance(v,(list,dict)) else v)
    def sheet(name,data,ff):
        s=wb.add_worksheet(name);[s.write(0,j,h) for j,h in enumerate(ff)]
        for i,r in enumerate(data,1):
            for j,h in enumerate(ff):s.write(i,j,str(r.get(h,''))[:32767])
        s.freeze_panes(1,0);s.autofilter(0,0,max(1,len(data)),max(0,len(ff)-1))
    sheet('Category Coverage',final_cov,cfields)
    for n,start in enumerate(range(0,len(issues),900000),1):sheet(f'Issues {n}',issues[start:start+900000],fields)
    wb.close();print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
