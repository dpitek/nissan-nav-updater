#!/usr/bin/env python3
"""
update_pois.py — Bulk-update REFER001/POINT047 with NC POIs from OpenStreetMap.

Pipeline:
  1. Fetch NC POIs from Overpass API (restaurant, fuel, bank, etc.)
  2. Load category-matched donor blocks from anchor cache
  3. Build patched blocks with new names
  4. Append all blocks to POINT047.DAT.cmp, update .ind

Usage:
    # Fetch OSM data and rebuild caches only (no card required):
    python updaters/update_pois.py --fetch-only

    # Apply pre-built cache to card:
    python updaters/update_pois.py \
        [--refer-dir /Volumes/485-1929-00/REFER001] \
        [--dry-run]

    # Full pipeline (fetch + apply):
    python updaters/update_pois.py [--refer-dir ...] [--dry-run]

Visible via: Nearby Places → category search ✅
NOT visible via: Search by Name (trie not updated) ❌

OSM data source: Overpass API (ODbL license)
© OpenStreetMap contributors — https://www.openstreetmap.org/copyright
"""

import argparse
import sys
import os
import json
import pickle
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.osm import fetch_pois, nc_bbox
from lib.refer import (
    load_anchors, build_poi_block, bulk_append_blocks,
    poi_stats, CATEGORY_MAP, _ANCHOR_CACHE, _RECORDS_CACHE
)
from lib.card import find_card

OSM_CACHE = "/tmp/osm_nc_all.json"
BATCH_SIZE = 500   # records per fetch chunk (Overpass rate limiting)


def fetch_all_nc_pois(verbose=False):
    """Fetch all NC POIs via Overpass, return list of {name, amenity, lat, lon}."""
    bbox = nc_bbox()
    categories = list({CATEGORY_MAP[k] for k in CATEGORY_MAP})

    print(f"Fetching NC POIs from OpenStreetMap (Overpass API)...")
    print(f"  bbox: {bbox}")
    print(f"  categories: {categories}")

    pois = fetch_pois(bbox, categories)

    print(f"  Fetched {len(pois)} named POI nodes")

    with open(OSM_CACHE, 'w') as f:
        json.dump(pois, f)
    print(f"  Cached to {OSM_CACHE}")

    return pois


def load_osm_cache():
    """Load POIs from local cache."""
    if not os.path.exists(OSM_CACHE):
        raise FileNotFoundError(
            f"OSM cache not found: {OSM_CACHE}\n"
            "Run with --fetch-only first."
        )
    with open(OSM_CACHE) as f:
        return json.load(f)


def build_records(pois, anchors, verbose=False):
    """
    Build patched POI blocks from OSM nodes using category-matched donors.

    Returns:
        list of (name, amenity, block_bytes) tuples
    """
    records = []
    skipped = 0

    for poi in pois:
        category = CATEGORY_MAP.get(poi.get('amenity', ''))
        if not category:
            skipped += 1
            continue

        donor = anchors.get(category)
        if donor is None:
            skipped += 1
            continue

        name = poi.get('name', '').strip()
        if not name:
            skipped += 1
            continue

        try:
            block = build_poi_block(donor, name)
            records.append((name, category, block))
        except Exception as e:
            if verbose:
                print(f"  [WARN] Could not build block for '{name}': {e}")
            skipped += 1

    return records, skipped


def run(args):
    # Step 1: Fetch or load OSM data
    if args.fetch_only or not os.path.exists(OSM_CACHE):
        pois = fetch_all_nc_pois(verbose=args.verbose)
    else:
        pois = load_osm_cache()
        print(f"Loaded {len(pois)} POIs from cache ({OSM_CACHE})")

    if args.fetch_only:
        print("\nFetch complete. Run without --fetch-only to apply to card.")
        return

    # Step 2: Load donor anchors
    try:
        anchors = load_anchors()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Loaded {len(anchors)} donor anchor categories")

    # Step 3: Build blocks
    print(f"\nBuilding POI blocks...")
    records, skipped = build_records(pois, anchors, verbose=args.verbose)
    print(f"  Built: {len(records)}  Skipped: {skipped}")

    # Category breakdown
    by_cat = {}
    for name, cat, _ in records:
        by_cat[cat] = by_cat.get(cat, 0) + 1
    for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {count}")

    # Save records cache for reference
    with open(_RECORDS_CACHE, 'wb') as f:
        pickle.dump([{'name': n, 'category': c} for n, c, _ in records], f)

    if args.dry_run:
        print(f"\n[DRY RUN] Would append {len(records)} blocks to POINT047.DAT.cmp")
        return

    # Step 4: Apply to card
    refer_dir = args.refer_dir
    if refer_dir is None:
        card = find_card()
        if card is None:
            print("ERROR: Nav card not mounted. Pass --refer-dir or insert card.")
            sys.exit(1)
        refer_dir = os.path.join(card, "REFER001")

    cmp_path = os.path.join(refer_dir, "POINT047.DAT.cmp")
    ind_path = os.path.join(refer_dir, "POINT047.DAT.ind")

    if not os.path.exists(cmp_path):
        print(f"ERROR: {cmp_path} not found")
        sys.exit(1)

    before = poi_stats(cmp_path)
    print(f"\nCurrent .cmp: {before['file_size_mb']}MB")

    print(f"Appending {len(records)} blocks...")
    blocks = [b for _, _, b in records]
    written = bulk_append_blocks(cmp_path, ind_path, blocks, dry_run=False)

    after = poi_stats(cmp_path)
    print(f"\n✅ Done")
    print(f"  Appended: {written:,} bytes ({written/1e6:.1f}MB)")
    print(f"  .cmp size: {before['file_size_mb']}MB → {after['file_size_mb']}MB")
    print(f"  {len(records)} POI blocks added")
    print(f"  Visible via: Nearby Places → category")
    print(f"  NOT visible via: Search by Name (trie unchanged)")


def main():
    parser = argparse.ArgumentParser(
        description="Update POINT047 POI database with NC OSM data"
    )
    parser.add_argument('--refer-dir', type=str, default=None,
                        help="Path to REFER001 directory (default: auto-detect card)")
    parser.add_argument('--fetch-only', action='store_true',
                        help="Only fetch/cache OSM data, don't apply to card")
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()
