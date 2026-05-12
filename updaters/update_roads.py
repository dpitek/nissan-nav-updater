#!/usr/bin/env python3
"""
update_roads.py — Bulk-inject OSM roads missing from MAPAL tiles (NC-wide).

Pipeline per tile:
  1. Fetch OSM road ways via Overpass API  (parallel, max 2 concurrent)
  2. Compare OSM nodes against existing MAPAL nodes (proximity threshold)
  3. Build compressed records for unmatched segments
  4. Batch-write all records to tile in one disk pass

Usage:
    python updaters/update_roads.py --all-nc          # full NC (creates missing tiles)
    python updaters/update_roads.py                   # existing NC tiles only
    python updaters/update_roads.py \
        [--tile-dir /Volumes/485-1929-00/MAPAL001] \
        [--tiles B20R0B0R.DAT,B28R0A8R.DAT] \
        [--match-threshold-m 50] \
        [--workers 4] \
        [--dry-run] [--verbose]

Limitations:
  - Map display layer only (not routable, not address-searchable)
  - Existing tiles near capacity are skipped when full
  - New tiles (4 MB) created via --all-nc have ample room

All road data derived from OpenStreetMap (ODbL).
© OpenStreetMap contributors — https://www.openstreetmap.org/copyright
"""

import argparse
import sys
import os
import time
import math
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.mapal import (
    read_tile, find_records, _decompress, get_last_link_id,
    get_free_space, build_road_record, batch_write_records,
    scan_nodes, HDR_SIZE, ensure_tile_exists,
)
from lib.tiles import tile_base, nc_mapal_tiles, nc_all_tile_paths, to_tile_rel
from lib.osm import fetch_roads, tile_bbox, segment_length_km
from lib.card import find_card


MATCH_THRESHOLD_DEFAULT = 50   # meters
OVERPASS_MAX_CONCURRENT = 2    # Overpass API allows 2 simultaneous connections
DEFAULT_WORKERS = 4            # total threads; only 2 hit Overpass at once

_overpass_sem: threading.Semaphore | None = None
_print_lock = threading.Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def is_matched(lat, lon, mapal_nodes, threshold_m):
    for mn in mapal_nodes:
        if haversine_m(lat, lon, mn['lat'], mn['lon']) <= threshold_m:
            return True
    return False


def nearest_sub_tile(data):
    for pos in reversed(find_records(data)):
        try:
            raw, _ = _decompress(data, pos)
            hdr = struct.unpack_from('>22H', raw[:HDR_SIZE])
            if hdr[2] == 25:
                return hdr[0]
        except Exception:
            continue
    return 0x905c


def process_tile(tile_path: str, threshold_m: int, dry_run: bool,
                 verbose: bool, idx: int, total: int) -> tuple[str, int, int, str | None]:
    """
    Full pipeline for one tile: fetch OSM → build records → batch write.
    Safe to call from multiple threads simultaneously (different tile paths).
    Returns (fname, injected, skipped, error_msg).
    """
    fname = os.path.basename(tile_path)
    lat_base, lon_base = tile_base(fname)
    bbox = tile_bbox(lat_base, lon_base)

    # ── Phase 1: Overpass fetch (rate-limited by semaphore) ──────────────────
    t_fetch = time.time()
    with _overpass_sem:
        try:
            roads = fetch_roads(bbox)
        except Exception as e:
            return fname, 0, 1, f"OSM fetch failed: {e}"
    fetch_ms = int((time.time() - t_fetch) * 1000)

    if not roads:
        _log(f"[{idx}/{total}] {fname}  (no OSM roads in bbox)")
        return fname, 0, 0, None

    # ── Phase 2: Analyze — read tile, scan existing nodes ────────────────────
    data = read_tile(tile_path)
    free = get_free_space(data)

    if free < 70 and not dry_run:
        _log(f"[{idx}/{total}] {fname}  [FULL — {free}B free, skipping]")
        return fname, 0, 0, None

    mapal_nodes = scan_nodes(
        data,
        lat_base - 0.001, lat_base + 1.001,
        lon_base - 0.01,  lon_base + 1.01,
        lat_base, lon_base,
    )

    next_link = get_last_link_id(data) + 1
    sub_tile  = nearest_sub_tile(data)

    compressed_records: list[bytes] = []

    for road in roads:
        nodes = road['nodes']
        if len(nodes) < 2:
            continue
        for i in range(len(nodes) - 1):
            lat_f, lon_f = nodes[i]['lat'],     nodes[i]['lon']
            lat_t, lon_t = nodes[i + 1]['lat'], nodes[i + 1]['lon']

            # Both endpoints must be inside this tile
            if not (lat_base <= lat_f < lat_base + 1.0 and
                    lon_base <= lon_f < lon_base + 1.0):
                continue
            if not (lat_base <= lat_t < lat_base + 1.0 and
                    lon_base <= lon_t < lon_base + 1.0):
                continue

            # Skip if both endpoints already present in MAPAL
            if is_matched(lat_f, lon_f, mapal_nodes, threshold_m) and \
               is_matched(lat_t, lon_t, mapal_nodes, threshold_m):
                continue

            # Skip trivially short segments
            if segment_length_km(lat_f, lon_f, lat_t, lon_t) * 1000 < 5:
                continue

            try:
                fr = to_tile_rel(lat_f, lon_f, lat_base, lon_base)
                tr = to_tile_rel(lat_t, lon_t, lat_base, lon_base)
            except AssertionError:
                continue

            rec = build_road_record(
                link_id=next_link,
                sub_tile=sub_tile,
                from_lat_rel=fr[0], from_lon_rel=fr[1],
                to_lat_rel=tr[0],   to_lon_rel=tr[1],
            )
            compressed_records.append(rec)
            next_link += 1

            if verbose:
                name = road['name'] or road['highway']
                seg_m = segment_length_km(lat_f, lon_f, lat_t, lon_t) * 1000
                _log(f"  +link {next_link-1}: {name} [{seg_m:.0f}m] "
                     f"({lat_f:.5f},{lon_f:.5f})→({lat_t:.5f},{lon_t:.5f})")

    if not compressed_records:
        _log(f"[{idx}/{total}] {fname}  (0 new roads from {len(roads)} OSM ways, "
             f"fetch={fetch_ms}ms)")
        return fname, 0, 0, None

    # ── Phase 3: Batch write (single disk pass) ───────────────────────────────
    written, space_skipped = batch_write_records(tile_path, compressed_records, dry_run)

    tag = "[DRY RUN] " if dry_run else ""
    _log(f"[{idx}/{total}] {fname}  {tag}+{written} roads  "
         f"({space_skipped} skipped/full)  fetch={fetch_ms}ms")

    return fname, written, space_skipped, None


def run(args):
    global _overpass_sem
    _overpass_sem = threading.Semaphore(OVERPASS_MAX_CONCURRENT)

    # ── Resolve tile directory ────────────────────────────────────────────────
    tile_dir = args.tile_dir
    if tile_dir is None:
        card = find_card()
        if card is None:
            print("ERROR: Nav card not mounted. Insert card or pass --tile-dir.")
            sys.exit(1)
        tile_dir = os.path.join(card, "MAPAL001")

    if not os.path.isdir(tile_dir):
        print(f"ERROR: Tile directory not found: {tile_dir}")
        sys.exit(1)

    # ── Resolve tile list (and create missing tiles if --all-nc) ─────────────
    if args.tiles:
        tile_paths = [os.path.join(tile_dir, t.strip()) for t in args.tiles.split(',')]
        for p in tile_paths:
            if not os.path.exists(p) and not args.dry_run:
                ensure_tile_exists(p, tile_dir)
                print(f"  [NEW TILE] {os.path.basename(p)}")
    elif args.all_nc:
        all_tiles = nc_all_tile_paths(tile_dir)
        created = 0
        for path, lat_b, lon_b, exists in all_tiles:
            if not exists and not args.dry_run:
                ensure_tile_exists(path, tile_dir)
                created += 1
                print(f"  [NEW TILE] {os.path.basename(path)} lat={lat_b} lon={lon_b}")
        if created:
            print(f"  Created {created} new tile files\n")
        tile_paths = [p for p, _, _, _ in all_tiles]
    else:
        tile_paths = nc_mapal_tiles(tile_dir)

    total = len(tile_paths)
    print(f"Tiles : {total}")
    print(f"Thresh: {args.match_threshold_m}m  |  Workers: {args.workers}  "
          f"|  Overpass concurrency: {OVERPASS_MAX_CONCURRENT}")
    print(f"Dir   : {tile_dir}")
    if args.all_nc:
        print("Mode  : ALL-NC (missing tiles created)")
    if args.dry_run:
        print("Mode  : DRY RUN — no writes")
    print()

    # ── Parallel execution ────────────────────────────────────────────────────
    total_injected = 0
    total_skipped  = 0
    errors         = []
    start          = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                process_tile,
                path, args.match_threshold_m, args.dry_run, args.verbose,
                idx, total,
            ): path
            for idx, path in enumerate(tile_paths, 1)
        }
        for fut in as_completed(futures):
            try:
                fname, injected, skipped, err = fut.result()
                total_injected += injected
                total_skipped  += skipped
                if err:
                    errors.append(f"  {fname}: {err}")
            except Exception as e:
                errors.append(f"  {futures[fut]}: unexpected {e}")

    elapsed = time.time() - start
    print(f"\n── Summary ─────────────────────────────")
    print(f"  Done in    : {elapsed:.0f}s  ({elapsed/60:.1f} min)")
    print(f"  Injected   : {total_injected} road segments")
    print(f"  Skipped    : {total_skipped} (no space / errors)")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for e in errors:
            print(e)
    if args.dry_run:
        print("  [DRY RUN — nothing written]")


def main():
    parser = argparse.ArgumentParser(
        description="Bulk-inject missing OSM roads into MAPAL tiles (NC-wide)"
    )
    parser.add_argument('--tile-dir', type=str, default=None,
                        help="Path to MAPAL001 directory (auto-detected if omitted)")
    parser.add_argument('--tiles', type=str, default=None,
                        help="Comma-sep tile filenames (default: existing NC tiles)")
    parser.add_argument('--all-nc', action='store_true',
                        help="Process ALL 40 expected NC tiles, creating missing ones (4MB each)")
    parser.add_argument('--match-threshold-m', type=int, default=MATCH_THRESHOLD_DEFAULT,
                        help=f"Match distance in meters (default: {MATCH_THRESHOLD_DEFAULT})")
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS,
                        help=f"Thread pool size (default: {DEFAULT_WORKERS}; "
                             f"Overpass concurrency capped at {OVERPASS_MAX_CONCURRENT})")
    parser.add_argument('--dry-run', action='store_true',
                        help="Fetch and analyze but do not write to card")
    parser.add_argument('--verbose', '-v', action='store_true',
                        help="Log every road segment injected")
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()
