#!/usr/bin/env python3
"""
update_roads.py — Bulk-inject OSM roads missing from MAPAL tiles (NC-wide).

Pipeline:
  1. For each MAPAL tile in NC coverage, fetch OSM road ways via Overpass API
  2. Compare OSM nodes against existing MAPAL nodes (proximity threshold)
  3. For roads with no matching MAPAL nodes, inject new road records
  4. Skip if insufficient free space in tile (log and continue)

Usage:
    python updaters/update_roads.py \
        [--tile-dir /Volumes/485-1929-00/MAPAL001] \
        [--tiles B20R0B0R.DAT,B21R0B0R.DAT]  # comma-sep subset \
        [--match-threshold-m 50] \
        [--dry-run] \
        [--verbose]

Limitations:
  - Map display layer only (not routable, not address-searchable)
  - Tiles with <80 bytes free space are skipped
  - Long roads spanning multiple tiles are split at tile boundaries
  - OSM data is fetched tile by tile (rate limited, may take hours for full NC)

All road data derived from OpenStreetMap (ODbL).
© OpenStreetMap contributors — https://www.openstreetmap.org/copyright
"""

import argparse
import sys
import os
import time
import math
import struct

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.mapal import (
    read_tile, find_records, _decompress, get_last_link_id,
    get_free_space, build_road_record, append_record, scan_nodes, HDR_SIZE,
    ensure_tile_exists,
)
from lib.tiles import tile_base, nc_mapal_tiles, nc_all_tile_paths, to_tile_rel
from lib.osm import fetch_roads, tile_bbox, segment_length_km
from lib.card import find_card


MATCH_THRESHOLD_DEFAULT = 50  # meters — OSM nodes within this distance of existing MAPAL nodes are considered matched


def haversine_m(lat1, lon1, lat2, lon2):
    """Distance between two points in meters."""
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def osm_nodes_in_tile(roads):
    """Flatten all road geometry nodes from OSM fetch result."""
    nodes = []
    for road in roads:
        for node in road['nodes']:
            nodes.append((node['lat'], node['lon'], road['name'], road['highway']))
    return nodes


def is_matched(lat, lon, mapal_nodes, threshold_m):
    """Return True if an OSM node is close enough to an existing MAPAL node."""
    for mn in mapal_nodes:
        if haversine_m(lat, lon, mn['lat'], mn['lon']) <= threshold_m:
            return True
    return False


def nearest_sub_tile(data):
    """Return sub_tile from last valid record."""
    positions = find_records(data)
    for pos in reversed(positions):
        try:
            raw, _ = _decompress(data, pos)
            hdr = struct.unpack_from('>22H', raw[:HDR_SIZE])
            if hdr[2] == 25:
                return hdr[0]
        except Exception:
            continue
    return 0x9060


def clip_to_tile(nodes, lat_base, lon_base):
    """Return only nodes within this tile's coordinate range (with small margin)."""
    result = []
    for node in nodes:
        lat, lon = node['lat'], node['lon']
        if lat_base - 0.001 <= lat < lat_base + 1.001 and lon_base - 0.01 <= lon < lon_base + 1.01:
            result.append(node)
    return result


def process_tile(tile_path, threshold_m, dry_run, verbose):
    """Process a single MAPAL tile. Returns (injected_count, skipped_count)."""
    fname = os.path.basename(tile_path)
    lat_base, lon_base = tile_base(fname)
    bbox = tile_bbox(lat_base, lon_base)

    if verbose:
        print(f"\n[{fname}] lat={lat_base:.3f} lon={lon_base:.1f}")

    # Fetch OSM roads for this tile's bbox
    try:
        roads = fetch_roads(bbox)
    except Exception as e:
        print(f"  [SKIP] OSM fetch failed: {e}")
        return 0, 1

    if not roads:
        if verbose:
            print(f"  No OSM roads in bbox")
        return 0, 0

    # Read tile and scan existing nodes
    data = read_tile(tile_path)
    free = get_free_space(data)
    mapal_nodes = scan_nodes(
        data,
        lat_base - 0.001, lat_base + 1.001,
        lon_base - 0.01, lon_base + 1.01,
        lat_base, lon_base
    )

    injected = 0
    skipped = 0
    next_link = get_last_link_id(data) + 1
    sub_tile = nearest_sub_tile(data)

    for road in roads:
        nodes = road['nodes']
        if len(nodes) < 2:
            continue

        # Process each segment of the road
        for i in range(len(nodes) - 1):
            n_from = nodes[i]
            n_to = nodes[i + 1]

            # Check both endpoints are in tile range
            lat_f, lon_f = n_from['lat'], n_from['lon']
            lat_t, lon_t = n_to['lat'], n_to['lon']

            in_tile_f = (lat_base <= lat_f < lat_base + 1.0 and
                         lon_base <= lon_f < lon_base + 1.0)
            in_tile_t = (lat_base <= lat_t < lat_base + 1.0 and
                         lon_base <= lon_t < lon_base + 1.0)

            if not (in_tile_f and in_tile_t):
                continue

            # Skip if both endpoints already have nearby MAPAL nodes
            from_matched = is_matched(lat_f, lon_f, mapal_nodes, threshold_m)
            to_matched = is_matched(lat_t, lon_t, mapal_nodes, threshold_m)

            if from_matched and to_matched:
                continue

            # Skip very short segments (< 5m) — likely duplicates
            seg_len = segment_length_km(lat_f, lon_f, lat_t, lon_t) * 1000
            if seg_len < 5:
                continue

            # Build and inject record
            try:
                from_rel = to_tile_rel(lat_f, lon_f, lat_base, lon_base)
                to_rel = to_tile_rel(lat_t, lon_t, lat_base, lon_base)
            except AssertionError:
                continue

            compressed = build_road_record(
                link_id=next_link,
                sub_tile=sub_tile,
                from_lat_rel=from_rel[0], from_lon_rel=from_rel[1],
                to_lat_rel=to_rel[0], to_lon_rel=to_rel[1],
            )

            needed = len(compressed) + 2
            if needed > free:
                if verbose:
                    print(f"  [SKIP] No space for link {next_link} ({needed}B needed, {free}B free)")
                skipped += 1
                continue

            if not dry_run:
                try:
                    append_record(tile_path, compressed, dry_run=False)
                    # Reload for updated free space tracking
                    data = read_tile(tile_path)
                    free = get_free_space(data)
                except ValueError as e:
                    print(f"  [ERROR] {e}")
                    skipped += 1
                    continue

            road_name = road['name'] or road['highway']
            if verbose:
                print(f"  +link {next_link}: {road_name} [{seg_len:.0f}m]  "
                      f"({lat_f:.5f},{lon_f:.5f})→({lat_t:.5f},{lon_t:.5f})")

            injected += 1
            next_link += 1

    return injected, skipped


def run(args):
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

    # Resolve tile list
    if args.tiles:
        tile_paths = [os.path.join(tile_dir, t.strip()) for t in args.tiles.split(',')]
        # Create any explicitly-specified tiles that don't exist yet
        for p in tile_paths:
            if not os.path.exists(p):
                if not args.dry_run:
                    ensure_tile_exists(p, tile_dir)
                    print(f"  [NEW TILE] Created {os.path.basename(p)}")
    elif args.all_nc:
        # All expected NC tiles — create missing ones first
        all_tiles = nc_all_tile_paths(tile_dir)
        created = 0
        for path, lat_b, lon_b, exists in all_tiles:
            if not exists and not args.dry_run:
                ensure_tile_exists(path, tile_dir)
                created += 1
                print(f"  [NEW TILE] Created {os.path.basename(path)} "
                      f"(lat={lat_b}, lon={lon_b})")
        if created:
            print(f"  Created {created} new tile files\n")
        tile_paths = [p for p, _, _, _ in all_tiles]
    else:
        tile_paths = nc_mapal_tiles(tile_dir)

    print(f"Tiles to process: {len(tile_paths)}")
    print(f"Match threshold: {args.match_threshold_m}m")
    print(f"Tile dir: {tile_dir}")
    if args.all_nc:
        print("[ALL-NC mode: includes newly created tiles for missing coverage areas]")
    if args.dry_run:
        print("[DRY RUN — no writes]")
    print()

    total_injected = 0
    total_skipped = 0
    start = time.time()

    for i, tile_path in enumerate(tile_paths, 1):
        fname = os.path.basename(tile_path)
        print(f"[{i}/{len(tile_paths)}] {fname}", end='', flush=True)

        injected, skipped = process_tile(
            tile_path,
            threshold_m=args.match_threshold_m,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        total_injected += injected
        total_skipped += skipped

        if not args.verbose:
            status = f"  +{injected} roads" if injected else "  (no new roads)"
            if skipped:
                status += f"  ({skipped} skipped)"
            print(status)

        # Rate limit: ~1 request/sec to be polite to Overpass
        if i < len(tile_paths):
            time.sleep(1.5)

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s")
    print(f"  Injected: {total_injected} road segments")
    print(f"  Skipped:  {total_skipped} (space/error)")
    if args.dry_run:
        print("  [DRY RUN — nothing written]")


def main():
    parser = argparse.ArgumentParser(
        description="Bulk-inject missing OSM roads into MAPAL tiles (NC-wide)"
    )
    parser.add_argument('--tile-dir', type=str, default=None)
    parser.add_argument('--tiles', type=str, default=None,
                        help="Comma-sep tile filenames to process (default: existing NC tiles)")
    parser.add_argument('--all-nc', action='store_true',
                        help="Process ALL expected NC tiles, creating missing ones (4MB each)")
    parser.add_argument('--match-threshold-m', type=int, default=MATCH_THRESHOLD_DEFAULT,
                        help=f"Distance threshold for 'already exists' check (default: {MATCH_THRESHOLD_DEFAULT}m)")
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()
