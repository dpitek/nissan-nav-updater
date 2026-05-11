#!/usr/bin/env python3
"""
update_dealers.py — Correct Nissan/Infiniti dealer coordinates in REFER002/POINT047.

The stock 2015 nav data has wrong coordinates for many NC dealerships (closed locations,
relocated stores, etc.). This script patches existing records in-place using
coordinates sourced from OpenStreetMap or provided manually.

Usage:
    # Show current records (read-only):
    python updaters/update_dealers.py --list \
        [--refer-dir /Volumes/485-1929-00/REFER002]

    # Apply corrections from a JSON file:
    python updaters/update_dealers.py \
        --corrections corrections/nc_dealers.json \
        [--refer-dir /Volumes/485-1929-00/REFER002] \
        [--dry-run]

Corrections JSON format:
    [
        {
            "name": "Leith Nissan Cary",
            "record_index": 12,
            "notes": "moved from old Raleigh location"
        },
        ...
    ]

Note: Coordinate encoding is proprietary Zenrin/NAVTEQ format — coordinates
cannot be directly patched without the vendor SDK. This script handles the
name-string and category patching only. For coord corrections, the donor-block
copy approach (same as REFER001) must be used.

All data from OpenStreetMap (ODbL).
© OpenStreetMap contributors — https://www.openstreetmap.org/copyright
"""

import argparse
import sys
import os
import json
import struct

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.refer import iter_blocks, read_block_header, poi_stats
from lib.card import find_card


def list_records(cmp_path: str, limit: int = 50):
    """Print block metadata from a POINT047.DAT file."""
    with open(cmp_path, 'rb') as f:
        data = f.read()

    blocks = iter_blocks(data)
    print(f"File: {cmp_path}")
    print(f"Size: {len(data):,} bytes ({len(data)/1e6:.1f}MB)")
    print(f"Blocks found: {len(blocks)}")
    print()

    for i, blk in enumerate(blocks[:limit]):
        # Try to extract name from block data
        raw = data[blk['data_start']:blk['data_end']]
        name = _extract_name(raw)
        print(f"  [{i:4d}] offset={hex(blk['offset'])}  "
              f"size={blk['total_size']:6d}  "
              f"records={blk['record_count']:4d}  "
              f"name={name!r}")

    if len(blocks) > limit:
        print(f"  ... ({len(blocks) - limit} more blocks)")


def _extract_name(raw: bytes, max_len=60) -> str:
    """Extract first printable ASCII string from block data."""
    i = 0
    while i < len(raw) - 3:
        if 0x20 <= raw[i] <= 0x7E:
            j = i
            while j < len(raw) and 0x20 <= raw[j] <= 0x7E:
                j += 1
            run = raw[i:j].decode('ascii', errors='replace')
            if len(run) >= 3:
                return run[:max_len]
        i += 1
    return "(no name)"


def apply_corrections(cmp_path: str, corrections: list[dict], dry_run: bool = False):
    """Apply name corrections to specified block indices."""
    with open(cmp_path, 'rb') as f:
        data = bytearray(f.read())

    blocks = iter_blocks(bytes(data))
    changed = 0

    for corr in corrections:
        idx = corr.get('record_index')
        new_name = corr.get('name')
        if idx is None or new_name is None:
            print(f"  [SKIP] Invalid correction entry: {corr}")
            continue

        if idx >= len(blocks):
            print(f"  [SKIP] Block index {idx} out of range (have {len(blocks)} blocks)")
            continue

        blk = blocks[idx]
        raw = data[blk['data_start']:blk['data_end']]
        old_name = _extract_name(bytes(raw))

        # Find and patch name in block
        name_start = _find_name_offset_in_block(bytes(raw))
        if name_start is None:
            print(f"  [SKIP] Could not find name field in block {idx}")
            continue

        abs_start = blk['data_start'] + name_start
        name_end = abs_start
        while name_end < blk['data_end'] and data[name_end] != 0:
            name_end += 1
        capacity = name_end - abs_start

        encoded = new_name[:capacity].encode('ascii', errors='replace')
        for i in range(capacity):
            data[abs_start + i] = encoded[i] if i < len(encoded) else 0

        note = corr.get('notes', '')
        print(f"  [{idx}] '{old_name}' → '{new_name}'"
              + (f"  # {note}" if note else ""))
        changed += 1

    if changed == 0:
        print("No changes made.")
        return

    if dry_run:
        print(f"\n[DRY RUN] Would update {changed} records in {cmp_path}")
        return

    with open(cmp_path, 'wb') as f:
        f.write(bytes(data))

    print(f"\n✅ Updated {changed} records in {cmp_path}")


def _find_name_offset_in_block(raw: bytes) -> int | None:
    """Find first printable ASCII run (len>=3) in block data."""
    i = 0
    while i < len(raw) - 3:
        if 0x20 <= raw[i] <= 0x7E:
            j = i
            while j < len(raw) and 0x20 <= raw[j] <= 0x7E:
                j += 1
            if j - i >= 3:
                return i
        i += 1
    return None


def run(args):
    refer_dir = args.refer_dir
    if refer_dir is None:
        card = find_card()
        if card is None:
            print("ERROR: Nav card not mounted. Pass --refer-dir or insert card.")
            sys.exit(1)
        refer_dir = os.path.join(card, "REFER002")

    cmp_path = os.path.join(refer_dir, "POINT047.DAT")
    if not os.path.exists(cmp_path):
        # REFER002 uses .DAT not .cmp
        cmp_path = os.path.join(refer_dir, "POINT047.DAT")

    if not os.path.exists(cmp_path):
        print(f"ERROR: {cmp_path} not found")
        sys.exit(1)

    if args.list:
        list_records(cmp_path, limit=args.limit)
        return

    if not args.corrections:
        print("ERROR: Provide --corrections <file.json> or --list")
        sys.exit(1)

    with open(args.corrections) as f:
        corrections = json.load(f)

    print(f"Applying {len(corrections)} corrections to {cmp_path}")
    if args.dry_run:
        print("[DRY RUN]")

    apply_corrections(cmp_path, corrections, dry_run=args.dry_run)


def main():
    parser = argparse.ArgumentParser(
        description="Correct dealer names in REFER002/POINT047"
    )
    parser.add_argument('--refer-dir', type=str, default=None)
    parser.add_argument('--list', action='store_true',
                        help="List all blocks (read-only)")
    parser.add_argument('--limit', type=int, default=50,
                        help="Max blocks to show with --list (default: 50)")
    parser.add_argument('--corrections', type=str, default=None,
                        help="Path to corrections JSON file")
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()
