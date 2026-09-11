import asyncio
import csv
from pathlib import Path
from types import SimpleNamespace

from audit import main

OUT = Path("qa_probe_output")

async def run():
    args = SimpleNamespace(
        site="https://www.gemsny.com",
        sitemap="https://www.gemsny.com/sitemap",
        min_width=500,
        min_height=500,
        delay=0,
        output_dir=str(OUT),
        shard_index=0,
        shard_count=1,
        max_categories=4,
        max_pages=1,
        check_original=True,
    )
    await main(args)

asyncio.run(run())

with open(OUT / "all_images.csv", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
with open(OUT / "coverage.csv", encoding="utf-8-sig", newline="") as f:
    coverage = list(csv.DictReader(f))

print("QA_TOTAL_IMAGES", len(rows))
print("QA_CATEGORY_COUNT", len(coverage))
for c in coverage:
    print("QA_CATEGORY", c)

for r in rows[:8]:
    print("QA_SAMPLE", {
        "category": r.get("category_name"),
        "sku": r.get("sku"),
        "served": f"{r.get('served_width')}x{r.get('served_height')}",
        "declared": f"{r.get('declared_src_width')}x{r.get('declared_src_height')}",
        "original": f"{r.get('original_width')}x{r.get('original_height')}",
        "status": r.get("status"),
        "basis": r.get("compliance_basis"),
    })

assert len(coverage) == 4, f"QA FAILED: expected 4 category checks, got {len(coverage)}"
bad = [c for c in coverage if c.get("status") in ("ERROR", "REVIEW - NO PRODUCT IMAGES")]
assert not bad, f"QA FAILED: categories without usable product detection: {bad}"
assert all(int(c.get("images_checked") or 0) > 0 for c in coverage), f"QA FAILED: zero-image category: {coverage}"
assert len(rows) >= 40, f"QA FAILED: total detected product images unexpectedly low: {len(rows)}"
assert all(int(r.get("original_width") or 0) > 0 and int(r.get("original_height") or 0) > 0 for r in rows[:20]), "QA FAILED: source dimensions unavailable"
print("QA PASSED: loose-gem and jewelry category product images are being detected with readable source dimensions.")
