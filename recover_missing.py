import argparse
import asyncio
import audit


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--site', default='https://www.gemsny.com')
    p.add_argument('--sitemap', default='https://www.gemsny.com/sitemap')
    p.add_argument('--min-width', type=int, default=500)
    p.add_argument('--min-height', type=int, default=500)
    p.add_argument('--delay', type=float, default=2)
    p.add_argument('--output-dir', required=True)
    p.add_argument('--shard-index', type=int, required=True)
    p.add_argument('--shard-count', type=int, default=16)
    p.add_argument('--slice-index', type=int, required=True)
    p.add_argument('--slice-count', type=int, default=8)
    p.add_argument('--max-categories', type=int, default=0)
    p.add_argument('--max-pages', type=int, default=0)
    p.add_argument('--check-original', action='store_true')
    p.add_argument('--no-check-original', dest='check_original', action='store_false')
    p.set_defaults(check_original=True)
    return p.parse_args()


async def run(args):
    original_discover = audit.discover

    async def sliced_discover(page, site, sitemap, max_categories, shard, shards):
        categories = await original_discover(page, site, sitemap, max_categories, shard, shards)
        sliced = categories[args.slice_index::args.slice_count]
        print(
            f'RECOVERY shard={args.shard_index} slice={args.slice_index + 1}/{args.slice_count} '
            f'categories={len(sliced)} of original {len(categories)}',
            flush=True,
        )
        return sliced

    audit.discover = sliced_discover
    try:
        await audit.main(args)
    finally:
        audit.discover = original_discover


if __name__ == '__main__':
    asyncio.run(run(parse_args()))
