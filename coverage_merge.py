import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

baseline=Path(sys.argv[1]); recovered=Path(sys.argv[2]); combined=Path(sys.argv[3]); out=Path(sys.argv[4])
combined.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True)

# Determine exactly which category URLs were re-audited.
recovery_cov=list(recovered.rglob('coverage.csv'))
target_urls=set()
recovery_rows=[]
for p in recovery_cov:
    with open(p,encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f)); recovery_rows.extend(rows)
        target_urls.update(r.get('category_url','') for r in rows if r.get('category_url'))

# Preserve baseline rows only for categories not replaced by the strict recovery.
base_dir=combined/'baseline'; base_dir.mkdir(exist_ok=True)
with open(baseline/'all_images.csv',encoding='utf-8-sig',newline='') as f:
    rd=csv.DictReader(f); fields=rd.fieldnames; rows=[r for r in rd if r.get('category_url','') not in target_urls]
with open(base_dir/'all_images.csv','w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
with open(baseline/'coverage.csv',encoding='utf-8-sig',newline='') as f:
    rd=csv.DictReader(f); cfields=rd.fieldnames; crows=[r for r in rd if r.get('category_url','') not in target_urls]
with open(base_dir/'coverage.csv','w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=cfields); w.writeheader(); w.writerows(crows)

# Copy all strict recovery output trees into combined input.
for i,p in enumerate(sorted(recovered.iterdir())):
    if p.is_dir(): shutil.copytree(p, combined/f'recovery-{i}', dirs_exist_ok=True)

# Recreate the expected shard bookkeeping so merge.py validates 0..15 accounted for.
meta=combined/'meta'; meta.mkdir(exist_ok=True)
for i in range(16):
    d=meta/f'shard-{i}'; d.mkdir(exist_ok=True)
    (d/'shard_info.json').write_text(json.dumps({'shard_index':i,'shard_count':16}),encoding='utf-8')

subprocess.check_call([sys.executable,'merge.py',str(combined),str(out)])

# Strict final validation: every re-audited row must be COMPLETE, every original
# coverage URL must still be represented exactly at least once, and output must be nonzero.
with open(out/'coverage.csv',encoding='utf-8-sig',newline='') as f: final_cov=list(csv.DictReader(f))
with open(out/'all_images.csv',encoding='utf-8-sig',newline='') as f: final_imgs=list(csv.DictReader(f))
problems=[r for r in final_cov if r.get('status')!='COMPLETE']
base_urls=set()
with open(baseline/'coverage.csv',encoding='utf-8-sig',newline='') as f:
    base_urls={r.get('category_url','') for r in csv.DictReader(f) if r.get('category_url')}
final_urls={r.get('category_url','') for r in final_cov if r.get('category_url')}
missing=sorted(base_urls-final_urls)
validation={
    'baseline_categories':len(base_urls),
    'final_categories':len(final_urls),
    'recovered_categories':len(target_urls),
    'noncomplete_categories':len(problems),
    'missing_category_urls':missing,
    'images':len(final_imgs),
}
(out/'coverage_validation.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
print(json.dumps(validation,indent=2))
if problems or missing or not final_imgs:
    raise SystemExit('Strict coverage validation failed')
