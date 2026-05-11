"""
REFER001/POINT047 POI block format reader/writer.

The POINT047.DAT.cmp file is a proprietary Zenrin B+ trie (not flat records).
- Search by Name: uses the trie index → only finds original 2015 records
- Nearby Places: does a spatial block scan → appended blocks ARE found ✅

Block structure (each block = variable-length):
  - 4-byte little-endian block size (total_size field)
  - 4-byte little-endian record count
  - Record entries (variable length, null-terminated strings)

Each record entry within a block (donor-copy format):
  Byte offsets are inferred from working donor blocks captured May 2026.
  The coordinate fields are proprietary Zenrin encoding — not decodable
  without the vendor SDK. We copy coordinate bytes from a same-category
  donor record and patch only the name string.

Usage:
    from lib.refer import read_blocks, append_poi_block, donor_for_category

All data added to POINT047.DAT.cmp is derived from OpenStreetMap (ODbL).
Attribution required: © OpenStreetMap contributors
  https://www.openstreetmap.org/copyright
"""

import struct
import os
import pickle
from typing import Any


CATEGORY_MAP = {
    "restaurant":   "restaurant",
    "fast_food":    "fast_food",
    "fuel":         "fuel",
    "bank":         "bank",
    "cafe":         "cafe",
    "supermarket":  "supermarket",
    "convenience":  "convenience",
    "pharmacy":     "pharmacy",
    "grocery":      "supermarket",
    "hotel":        "hotel",
}

# Cached anchor files written by update_pois.py
_ANCHOR_CACHE = "/tmp/nc_cat_anchors.pkl"
_RECORDS_CACHE = "/tmp/nc_records_all.pkl"


def read_block_header(data: bytes, offset: int) -> dict:
    """
    Read a block header from the .cmp file.

    Returns dict with: offset, total_size, record_count, data_start
    """
    if offset + 8 > len(data):
        raise ValueError(f"Truncated block header at {hex(offset)}")
    total_size = struct.unpack_from('<I', data, offset)[0]
    record_count = struct.unpack_from('<I', data, offset + 4)[0]
    return {
        'offset': offset,
        'total_size': total_size,
        'record_count': record_count,
        'data_start': offset + 8,
        'data_end': offset + total_size,
    }


def iter_blocks(data: bytes, start: int = 0) -> list[dict]:
    """
    Iterate over all blocks in a .cmp file.

    Returns list of block header dicts (fast metadata scan, no record parsing).
    """
    blocks = []
    offset = start
    while offset < len(data) - 8:
        try:
            hdr = read_block_header(data, offset)
            if hdr['total_size'] < 8 or hdr['total_size'] > 10_000_000:
                break
            blocks.append(hdr)
            offset += hdr['total_size']
        except Exception:
            break
    return blocks


def find_appended_blocks(cmp_path: str) -> list[dict]:
    """
    Return metadata for all appended blocks (those after the original trie).

    The original trie ends at the first block where total_size == file_continues.
    In practice, appended blocks start where num_blocks in the .ind file points.
    We just return all blocks found by linear scan.
    """
    with open(cmp_path, 'rb') as f:
        data = f.read()
    return iter_blocks(data)


def read_ind(ind_path: str) -> dict:
    """
    Parse a POINT047.DAT.ind index file.

    Returns dict with: num_blocks, total_size (from header fields we've decoded).
    The .ind file is 18,432 bytes with a 512-byte header page followed by
    index entries. We extract num_blocks from offset 0x10 (little-endian u32).
    """
    with open(ind_path, 'rb') as f:
        data = f.read()
    # Offset 0x10 = num_blocks (observed empirically)
    num_blocks = struct.unpack_from('<I', data, 0x10)[0]
    total_size = struct.unpack_from('<I', data, 0x14)[0]
    return {'num_blocks': num_blocks, 'total_size': total_size, 'raw': data}


def write_ind(ind_path: str, ind_data: dict) -> None:
    """Write updated .ind file."""
    raw = bytearray(ind_data['raw'])
    struct.pack_into('<I', raw, 0x10, ind_data['num_blocks'])
    struct.pack_into('<I', raw, 0x14, ind_data['total_size'])
    with open(ind_path, 'wb') as f:
        f.write(bytes(raw))


def load_anchors(cache_path: str = _ANCHOR_CACHE) -> dict[str, bytes]:
    """Load category→donor_block_bytes mapping from pickle cache."""
    if not os.path.exists(cache_path):
        raise FileNotFoundError(
            f"Anchor cache not found: {cache_path}\n"
            "Run update_pois.py --fetch-only to rebuild."
        )
    with open(cache_path, 'rb') as f:
        return pickle.load(f)


def load_records(cache_path: str = _RECORDS_CACHE) -> list[dict]:
    """Load pre-built POI record list from pickle cache."""
    if not os.path.exists(cache_path):
        raise FileNotFoundError(
            f"Records cache not found: {cache_path}\n"
            "Run update_pois.py --fetch-only to rebuild."
        )
    with open(cache_path, 'rb') as f:
        return pickle.load(f)


def build_poi_block(donor_bytes: bytes, name: str, record_count: int = 1) -> bytes:
    """
    Build a POI block by patching the name in a donor block.

    The donor block has a known name at a known offset. We replace it with
    the new name (truncated to fit, null-terminated, zero-padded to same length).

    Args:
        donor_bytes: Raw block bytes from a same-category existing record
        name: New POI name (ASCII, max 50 chars)
        record_count: Number of records to report in header (usually 1)

    Returns:
        New block bytes, same total_size as donor
    """
    block = bytearray(donor_bytes)

    # Patch record_count in header
    struct.pack_into('<I', block, 4, record_count)

    # Find the name string: scan for first null-terminated printable ASCII string
    # after byte 8 (skip 8-byte header)
    name_start = _find_name_offset(bytes(block))
    if name_start is None:
        raise ValueError("Could not locate name field in donor block")

    # Find extent of existing name (up to null terminator)
    name_end = name_start
    while name_end < len(block) and block[name_end] != 0:
        name_end += 1
    name_capacity = name_end - name_start

    # Write new name (truncate if needed, always null-terminate)
    encoded = name[:name_capacity].encode('ascii', errors='replace')
    for i in range(name_capacity):
        block[name_start + i] = encoded[i] if i < len(encoded) else 0

    return bytes(block)


def _find_name_offset(block: bytes) -> int | None:
    """Heuristic: find first printable ASCII run (len>=3) after offset 8."""
    i = 8
    while i < len(block) - 3:
        # Look for a run of printable ASCII (0x20..0x7E) of at least 3 chars
        if 0x20 <= block[i] <= 0x7E:
            j = i
            while j < len(block) and 0x20 <= block[j] <= 0x7E:
                j += 1
            if j - i >= 3:
                return i
        i += 1
    return None


def append_block(cmp_path: str, ind_path: str, block_bytes: bytes,
                 dry_run: bool = False) -> int:
    """
    Append a pre-built block to POINT047.DAT.cmp and update the .ind file.

    Returns:
        Byte offset where block was written.
    """
    with open(cmp_path, 'rb') as f:
        cmp_data = f.read()

    offset = len(cmp_data)

    if not dry_run:
        with open(cmp_path, 'ab') as f:
            f.write(block_bytes)

        # Update .ind num_blocks
        ind = read_ind(ind_path)
        ind['num_blocks'] += 1
        ind['total_size'] = offset + len(block_bytes)
        write_ind(ind_path, ind)

    return offset


def bulk_append_blocks(cmp_path: str, ind_path: str,
                       blocks: list[bytes], dry_run: bool = False) -> int:
    """
    Append multiple pre-built blocks in one pass (faster for large batches).

    Returns:
        Total bytes appended.
    """
    combined = b''.join(blocks)

    if not dry_run:
        with open(cmp_path, 'ab') as f:
            f.write(combined)

        ind = read_ind(ind_path)
        ind['num_blocks'] += len(blocks)
        ind['total_size'] += len(combined)
        write_ind(ind_path, ind)

    return len(combined)


def poi_stats(cmp_path: str) -> dict:
    """Return quick stats on the .cmp file."""
    size = os.path.getsize(cmp_path)
    with open(cmp_path, 'rb') as f:
        data = f.read(64)  # Just read header of first block
    first_block_size = struct.unpack_from('<I', data, 0)[0] if len(data) >= 4 else 0
    first_record_count = struct.unpack_from('<I', data, 4)[0] if len(data) >= 8 else 0
    return {
        'file_size_mb': round(size / 1e6, 1),
        'first_block_size': first_block_size,
        'first_block_records': first_record_count,
    }
