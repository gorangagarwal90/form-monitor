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
        max_categories=1,
        max_pages=2,
        check_original=True,
    )
    await main(args)


asyncio.run(run())

with open(OUT / "all_images.csv", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
with open(OUT / "coverage.csv", encoding="utf-8-sig", newline="") as f:
    coverage = list(csv.DictReader(f))

print("PAGINATION_QA_IMAGES", len(rows))
print("PAGINATION_QA_COVERAGE", coverage)
print("PAGE_COUNTS", sorted({r.get('page_number') for r in rows}))

assert len(coverage) == 1, f"QA FAILED: expected one category, got {len(coverage)}"
c = coverage[0]
assert int(c.get("pages_visited") or 0) == 2, f"QA FAILED: expected 2 pages visited, got {c}"
assert c.get("status") == "PARTIAL - PAGE LIMIT", f"QA FAILED: pagination status wrong: {c}"
assert str(c.get("pagination_detected")).lower() == "true", f"QA FAILED: numeric pagination not detected: {c}"
assert len(rows) >= 40, f"QA FAILED: second page did not add enough product images: {len(rows)}"
assert len({r.get('product_key') for r in rows if r.get('product_key')}) >= 40, "QA FAILED: duplicate/repeated first page detected"
assert {r.get('page_number') for r in rows} >= {'1', '2'}, "QA FAILED: rows were not recorded from both pages"

print("QA PASSED: numeric pagination advances to page 2 and captures new product images.")
