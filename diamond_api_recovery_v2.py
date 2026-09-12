import argparse, asyncio, hashlib, json, math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

import diamond_api_recovery as base


async def prep(args):
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    targets = base.read_targets(args.targets)
    snapshot = {'created_at': datetime.now(timezone.utc).isoformat(), 'targets': [], 'groups': {}}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={'width': 1440, 'height': 1200},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36',
        )
        page = await ctx.new_page()
        group_targets = defaultdict(list)

        for i, t in enumerate(targets, 1):
            api = await base.capture_api(page, t['category_url'])
            if not api:
                raise RuntimeError(f'No diamond API request captured for {t["category_url"]}')
            key = hashlib.sha1(api.encode()).hexdigest()[:16]
            group_targets[key].append(t)
            snapshot['targets'].append({**t, 'group_key': key, 'api_base': api})
            print(f'CAPTURE {i}/{len(targets)} group={key} {t["category_url"]}', flush=True)

        for gi, (key, members) in enumerate(group_targets.items(), 1):
            api = next(x['api_base'] for x in snapshot['targets'] if x['group_key'] == key)
            page_size = args.page_size
            item_map = {}
            observed_counts = []
            pass_meta = []
            reconciled = False
            reconciliation_basis = ''
            max_passes = 4

            for pass_no in range(1, max_passes + 1):
                c = await base.api_json(ctx.request, base.count_url(api))
                start_count = int(c.get('totalCount') or c.get('count') or 0)
                if start_count <= 0:
                    raise RuntimeError(f'Zero/invalid API count for group {key}: {api}: {c}')
                observed_counts.append(start_count)
                pages = math.ceil(start_count / page_size)
                before = len(item_map)
                pass_ids = []
                raw_rows = 0

                for n in range(1, pages + 1):
                    data = await base.api_json(ctx.request, base.with_page(api, n, page_size))
                    batch = data.get('items') or []
                    if not batch:
                        raise RuntimeError(
                            f'Empty API batch group={key} pass={pass_no} page={n}/{pages}, count={start_count}'
                        )
                    raw_rows += len(batch)
                    for item in batch:
                        iid, compact = base.compact_item(item)
                        if iid:
                            pass_ids.append(iid)
                            item_map[iid] = compact
                    if n == 1 or n % 25 == 0 or n == pages:
                        print(
                            f'SNAPSHOT group={gi}/{len(group_targets)} key={key} pass={pass_no}/{max_passes} '
                            f'page={n}/{pages} union_unique={len(item_map)} raw_rows={raw_rows} start_count={start_count}',
                            flush=True,
                        )

                c2 = await base.api_json(ctx.request, base.count_url(api))
                end_count = int(c2.get('totalCount') or c2.get('count') or 0)
                observed_counts.append(end_count)
                pass_unique = set(pass_ids)
                duplicate_rows = max(0, raw_rows - len(pass_unique))
                sig = hashlib.sha1('\n'.join(sorted(pass_unique)).encode()).hexdigest()
                meta = {
                    'pass': pass_no,
                    'start_count': start_count,
                    'end_count': end_count,
                    'raw_rows': raw_rows,
                    'unique_ids': len(pass_unique),
                    'duplicate_rows': duplicate_rows,
                    'id_signature': sig,
                    'union_unique': len(item_map),
                    'gained': len(item_map) - before,
                }
                pass_meta.append(meta)
                print(f'RECONCILE group={key} {json.dumps(meta, sort_keys=True)}', flush=True)

                # Normal case: immutable union meets/exceeds the live count.
                if end_count > 0 and len(item_map) >= end_count:
                    reconciled = True
                    reconciliation_basis = 'UNION_MEETS_LIVE_COUNT'
                    break

                # Some GemsNY inventory endpoints report totalCount including duplicate
                # rows. Prove that this is an API-count duplication (not missing products)
                # before accepting the unique SKU snapshot: two consecutive complete
                # passes must fetch exactly totalCount rows, expose the same unique ID set,
                # keep a stable count, and the shortfall must equal duplicated rows.
                if len(pass_meta) >= 2:
                    a, b = pass_meta[-2], pass_meta[-1]
                    stable_duplicate_count = (
                        a['start_count'] == a['end_count'] == b['start_count'] == b['end_count']
                        and a['raw_rows'] == a['start_count']
                        and b['raw_rows'] == b['start_count']
                        and a['id_signature'] == b['id_signature']
                        and a['unique_ids'] == b['unique_ids'] == len(item_map)
                        and a['duplicate_rows'] == a['start_count'] - a['unique_ids']
                        and b['duplicate_rows'] == b['start_count'] - b['unique_ids']
                        and a['duplicate_rows'] > 0
                    )
                    if stable_duplicate_count:
                        reconciled = True
                        reconciliation_basis = 'STABLE_BACKEND_DUPLICATE_ROWS'
                        print(
                            f'COUNT RECONCILED group={key}: backend totalCount={b["end_count"]}, '
                            f'unique_skus={b["unique_ids"]}, duplicate_rows={b["duplicate_rows"]}; '
                            f'two consecutive full passes had identical SKU signatures',
                            flush=True,
                        )
                        break

            if not reconciled:
                final_count = observed_counts[-1] if observed_counts else 0
                raise RuntimeError(
                    f'Snapshot count mismatch after {max_passes} reconciliation passes group={key}: '
                    f'latest API count={final_count}, unique items={len(item_map)}, '
                    f'observed_counts={observed_counts}, pass_meta={pass_meta}'
                )

            items = list(item_map.values())
            snapshot_count = len(items)
            snapshot['groups'][key] = {
                'api_base': api,
                'count': snapshot_count,
                'items': items,
                'target_urls': [m['category_url'] for m in members],
                'observed_api_counts': observed_counts,
                'inventory_drift': snapshot_count - (observed_counts[-1] if observed_counts else snapshot_count),
                'reconciliation_basis': reconciliation_basis,
                'reconciliation_passes': pass_meta,
                'backend_duplicate_rows': (
                    pass_meta[-1]['duplicate_rows'] if reconciliation_basis == 'STABLE_BACKEND_DUPLICATE_ROWS' else 0
                ),
            }

        await browser.close()

    (out / 'diamond_snapshot.json').write_text(json.dumps(snapshot, separators=(',', ':')), encoding='utf-8')
    summary = {
        'targets': len(snapshot['targets']),
        'distinct_inventory_groups': len(snapshot['groups']),
        'group_counts': {k: v['count'] for k, v in snapshot['groups'].items()},
        'total_group_items': sum(v['count'] for v in snapshot['groups'].values()),
        'inventory_drift': {k: v.get('inventory_drift', 0) for k, v in snapshot['groups'].items()},
        'reconciliation_basis': {k: v.get('reconciliation_basis', '') for k, v in snapshot['groups'].items()},
        'backend_duplicate_rows': {k: v.get('backend_duplicate_rows', 0) for k, v in snapshot['groups'].items()},
    }
    (out / 'snapshot_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('prep')
    p.add_argument('--targets', required=True)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--page-size', type=int, default=500)
    a = sub.add_parser('audit')
    a.add_argument('--snapshot', required=True)
    a.add_argument('--output-dir', required=True)
    a.add_argument('--shard-index', type=int, required=True)
    a.add_argument('--shard-count', type=int, default=16)
    a.add_argument('--concurrency', type=int, default=24)
    a.add_argument('--min-width', type=int, default=500)
    a.add_argument('--min-height', type=int, default=500)
    m = sub.add_parser('merge')
    m.add_argument('--snapshot', required=True)
    m.add_argument('--input-dir', required=True)
    m.add_argument('--output-dir', required=True)
    m.add_argument('--shard-count', type=int, default=16)
    args = ap.parse_args()
    if args.mode == 'prep':
        asyncio.run(prep(args))
    elif args.mode == 'audit':
        asyncio.run(base.audit_shard(args))
    else:
        base.merge(args)


if __name__ == '__main__':
    main()
