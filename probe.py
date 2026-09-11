import asyncio
import csv
from pathlib import Path
from types import SimpleNamespace

from audit import main

OUT = Path('qa_probe_output')

async def run():
    args = SimpleNamespace(
        site='https://www.gemsny.com',
        sitemap='https://www.gemsny.com/sitemap',
        min_width=500,
        min_height=500,
        delay=0,
        output_dir=str(OUT),
        shard_index=0,
        shard_count=1,
        max_categories=1,
        max_pages=1,
        check_original=True,
    )
    await main(args)

asyncio.run(run())

with open(OUT / 'all_images.csv', encoding='utf-8-sig', newline='') as f:
    rows = list(csv.DictReader(f))
with open(OUT / 'coverage.csv', encoding='utf-8-sig', newline='') as f:
    coverage = list(csv.DictReader(f))

print('QA_IMAGE_ROWS', len(rows))
print('QA_COVERAGE', coverage)
for r in rows[:5]:
    print('QA_SAMPLE', {
        'sku': r.get('sku'),
        'served': f"{r.get('served_width')}x{r.get('served_height')}",
        'declared': f"{r.get('declared_src_width')}x{r.get('declared_src_height')}",
        'original': f"{r.get('original_width')}x{r.get('original_height')}",
        'status': r.get('status'),
        'basis': r.get('compliance_basis'),
        'image': r.get('image_url'),
    })

# Hard guardrails: the workflow must fail if the scraper silently stops finding products.
assert len(rows) >= 20, f'QA FAILED: expected at least 20 product images, got {len(rows)}'
assert coverage and int(coverage[0].get('unique_products_detected') or 0) >= 20, 'QA FAILED: product coverage too low'
assert all(int(r.get('original_width') or 0) > 0 and int(r.get('original_height') or 0) > 0 for r in rows[:10]), 'QA FAILED: source dimensions unavailable'
print('QA PASSED: live GemsNY product images are being detected and source dimensions are readable.')
