#!/usr/bin/env python3
"""
add_road.py — Inject a single road segment into a MAPAL tile.

Usage:
    python updaters/add_road.py \
        --from-lat 34.4004 --from-lon -77.5780 \
        --to-lat 34.4052 --to-lon -77.5794 \
        [--name "Auger Shell Court"] \
        [--tile-dir /Volumes/485-1929-00/MAPAL001] \
        [--dry-run]

This adds the road segment to the map display layer only.
Limitations:
  - Not routable (RDSTM001 routing graph not updated)
  - Not address-searchable (HOUSE001 B-tree not updated)
  - Visible as a road line on the map display ✅

All coordinate data derived from OpenStreetMap (ODbL).
© OpenStreetMap contributors — https://www.openstreetmap.org/copyright
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.mapal import (
    read_tile, find_records, _decompress, get_last_link_id,
    get_free_space, build_road_record, append_record
)
from lib.tiles import tile_name, tile_base, to_tile_rel, find_tile_for_coord
from lib.card import find_card, EXPECTED_LABEL


def nearest_sub_tile(data: bytes) -> int:
    """Return the sub_tile code from the last record in the tile (safe default)."""
    import struct
    from lib.mapal import HDR_SIZE
    positions = find_records(data)
    for pos in reversed(positions):
        try:
            raw, _ = _decompress(data, pos)
            hdr = struct.unpack_from('>22H', raw[:HDR_SIZE])
            if hdr[2] == 25:
                return hdr[0]
        except Exception:
            continue
    return 0x9060  # fallback


def run(args):
    # Resolve tile directory
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

    # Find the tile containing the FROM point
    tile_path = find_tile_for_coord(tile_dir, args.from_lat, args.from_lon)
    if tile_path is None:
        fname = tile_name(args.from_lat, args.from_lon)
        tile_path = os.path.join(tile_dir, fname)
        if not os.path.exists(tile_path):
            print(f"ERROR: No tile found for {args.from_lat},{args.from_lon}")
            print(f"  Expected: {tile_path}")
            sys.exit(1)

    print(f"Tile: {os.path.basename(tile_path)}")

    lat_base, lon_base = tile_base(os.path.basename(tile_path))
    print(f"  Base: lat={lat_base}, lon={lon_base}")

    # Validate both endpoints are in the same tile
    try:
        from_rel = to_tile_rel(args.from_lat, args.from_lon, lat_base, lon_base)
        to_rel = to_tile_rel(args.to_lat, args.to_lon, lat_base, lon_base)
    except AssertionError as e:
        print(f"ERROR: Endpoint outside tile: {e}")
        print("  Split the segment so both endpoints are in the same tile.")
        sys.exit(1)

    print(f"  FROM: tile-rel {from_rel}  ({args.from_lat:.6f}, {args.from_lon:.6f})")
    print(f"  TO:   tile-rel {to_rel}  ({args.to_lat:.6f}, {args.to_lon:.6f})")

    # Read tile
    data = read_tile(tile_path)
    free = get_free_space(data)
    last_link = get_last_link_id(data)
    sub_tile = nearest_sub_tile(data)
    new_link = last_link + 1

    print(f"  Records: {len(find_records(data))}")
    print(f"  Free space: {free} bytes")
    print(f"  Last link_id: {last_link}  →  New link_id: {new_link}")
    print(f"  Sub-tile: {hex(sub_tile)}")

    # Build shape points if provided
    shape = None
    if args.shape:
        # --shape "lat,lon lat,lon ..."
        shape = []
        for pt in args.shape.split():
            la, lo = pt.split(',')
            shape.append(to_tile_rel(float(la), float(lo), lat_base, lon_base))

    # Build compressed record
    compressed = build_road_record(
        link_id=new_link,
        sub_tile=sub_tile,
        from_lat_rel=from_rel[0], from_lon_rel=from_rel[1],
        to_lat_rel=to_rel[0], to_lon_rel=to_rel[1],
        shape_points=shape,
    )

    needed = len(compressed) + 2
    print(f"  Compressed record: {len(compressed)} bytes  (need {needed} with gap)")

    if needed > free:
        print(f"ERROR: Not enough space. Need {needed}B, have {free}B.")
        sys.exit(1)

    if args.dry_run:
        print("\n[DRY RUN] No changes written.")
        name_label = f" ({args.name})" if args.name else ""
        print(f"  Would append link_id={new_link}{name_label} to {os.path.basename(tile_path)}")
        return

    # Write
    offset = append_record(tile_path, compressed, dry_run=False)
    print(f"\n✅ Written at offset {hex(offset)}")
    if args.name:
        print(f"  Road: {args.name}")
    print(f"  link_id: {new_link}, sub_tile: {hex(sub_tile)}")
    print(f"  Map display only — not routable, not address-searchable")


def main():
    parser = argparse.ArgumentParser(
        description="Add a single road segment to a MAPAL tile"
    )
    parser.add_argument('--from-lat', type=float, required=True)
    parser.add_argument('--from-lon', type=float, required=True)
    parser.add_argument('--to-lat', type=float, required=True)
    parser.add_argument('--to-lon', type=float, required=True)
    parser.add_argument('--name', type=str, default=None,
                        help="Road name (informational only, not written to card)")
    parser.add_argument('--shape', type=str, default=None,
                        help="Intermediate shape points as 'lat,lon lat,lon ...'")
    parser.add_argument('--tile-dir', type=str, default=None,
                        help="Path to MAPAL001 directory (default: auto-detect card)")
    parser.add_argument('--dry-run', action='store_true',
                        help="Simulate without writing")
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()
