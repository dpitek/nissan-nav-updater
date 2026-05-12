"""
MAPAL tile file reader/writer for Clarion CLA-NAVI06-01 nav format.

Record format (per record):
  - zlib compressed stream (header bytes 0x58 0x85, wbits=13 level=6)
  - Decompressed: 44-byte header (22 x u16 big-endian) + vtx array (4 bytes each)
  - Records separated by 0 or 2 null bytes

Header fields:
  hdr[0]  = sub-tile code (spatial cell identifier)
  hdr[1]  = link_id (unique road link identifier)
  hdr[2]  = 25 (constant)
  hdr[3]  = 13434 (constant internal reference)
  hdr[4]  = 10991 (constant internal reference)
  hdr[9]  = 11 (constant)
  hdr[10] = total vtx count (when hdr[12]==0)
  hdr[11] = 65535 when hdr[12]==0, else hdr[10]+11
  hdr[12] = additional vtx slots (usually 0 for simple records)

Vtx array layout (for hdr[12]==0 records):
  vtx[0..10]  : (65535, 0) sentinels x11
  vtx[11]     : cell anchor (small offset, e.g. (26, 5))
  vtx[12..25] : (65535, 0) sentinels x14
  vtx[26]     : (1, 3) reference
  vtx[27]     : (16385, 2) reference
  vtx[28]     : (11, 65535) reference
  vtx[29]     : FROM node (tile-relative u16 lat, lon)
  vtx[30]     : TO node (tile-relative u16 lat, lon)
  vtx[31+]    : additional shape points (optional)
"""

import zlib
import struct
from dataclasses import dataclass, field

ZLIB_MAGIC = b'\x58\x85'
WBITS = 13
ZLEVEL = 6
HDR_SIZE = 44
VTX_SIZE = 4
SENTINEL = (65535, 0)

# Constants found in all valid records
HDR_CONST = {
    2: 25,
    3: 13434,
    4: 10991,
    5: 0, 6: 0,
    7: 65535,
    8: 0,
    9: 11,
}


@dataclass
class MapalRecord:
    sub_tile: int
    link_id: int
    vtx: list[tuple[int, int]] = field(default_factory=list)
    hdr_extra: list[int] = field(default_factory=list)  # hdr[12..21]

    @property
    def from_node(self) -> tuple[int, int] | None:
        return self.vtx[29] if len(self.vtx) > 29 else None

    @property
    def to_node(self) -> tuple[int, int] | None:
        return self.vtx[30] if len(self.vtx) > 30 else None


def _compress(data: bytes) -> bytes:
    c = zlib.compressobj(level=ZLEVEL, method=zlib.DEFLATED, wbits=WBITS)
    compressed = c.compress(data) + c.flush()
    assert compressed[:2] == ZLIB_MAGIC, f"Wrong zlib header: {compressed[:2].hex()}"
    return compressed


def _decompress(data: bytes, start: int) -> tuple[bytes, int]:
    """Decompress record starting at start. Returns (raw_bytes, compressed_size)."""
    for end in range(start + 4, min(start + 8192, len(data))):
        try:
            dec = zlib.decompress(data[start:end])
            if len(dec) >= HDR_SIZE:
                return dec, end - start
        except Exception:
            continue
    raise ValueError(f"No valid zlib record at offset {hex(start)}")


def find_records(data: bytes) -> list[int]:
    """Return list of offsets where zlib records start."""
    positions = []
    i = 0
    while i < len(data) - 1:
        if data[i] == 0x58 and data[i + 1] == 0x85:
            positions.append(i)
        i += 1
    return positions


def decode_record(raw: bytes) -> MapalRecord:
    """Decode a decompressed record into a MapalRecord."""
    hdr = list(struct.unpack_from('>22H', raw[:HDR_SIZE]))
    vtx_count = (len(raw) - HDR_SIZE) // VTX_SIZE
    vtx = []
    for i in range(vtx_count):
        y, x = struct.unpack_from('>HH', raw[HDR_SIZE + i * VTX_SIZE:HDR_SIZE + (i + 1) * VTX_SIZE])
        vtx.append((y, x))
    return MapalRecord(
        sub_tile=hdr[0],
        link_id=hdr[1],
        vtx=vtx,
        hdr_extra=hdr[12:],
    )


def encode_record(rec: MapalRecord) -> bytes:
    """Encode a MapalRecord to raw (decompressed) bytes."""
    vtx_count = len(rec.vtx)
    hdr = [0] * 22
    hdr[0] = rec.sub_tile
    hdr[1] = rec.link_id
    hdr[2] = HDR_CONST[2]
    hdr[3] = HDR_CONST[3]
    hdr[4] = HDR_CONST[4]
    hdr[5] = hdr[6] = hdr[8] = 0
    hdr[7] = 65535
    hdr[9] = HDR_CONST[9]
    hdr[10] = vtx_count
    hdr[11] = 65535   # no additional section
    hdr[12] = 0
    # hdr[13..21] pattern: 65535,0,65535,0,65535,0,0,65535,0
    pattern = [65535, 0, 65535, 0, 65535, 0, 0, 65535, 0]
    for i, v in enumerate(pattern):
        hdr[13 + i] = v
    raw = struct.pack('>22H', *hdr)
    raw += b''.join(struct.pack('>HH', y, x) for y, x in rec.vtx)
    return raw


def build_road_record(
    link_id: int,
    sub_tile: int,
    from_lat_rel: int, from_lon_rel: int,
    to_lat_rel: int, to_lon_rel: int,
    shape_points: list[tuple[int, int]] | None = None,
) -> bytes:
    """
    Build a compressed road segment record.

    Args:
        link_id: unique link identifier (use next_link_id() to get one)
        sub_tile: spatial cell code (copy from nearest existing record)
        from/to lat/lon_rel: tile-relative u16 coordinates
        shape_points: optional intermediate shape points as (lat_rel, lon_rel) list

    Returns:
        Compressed bytes ready to write to the tile file (without gap prefix).
    """
    vtx = (
        [SENTINEL] * 11 +    # vtx[0..10]
        [(26, 5)] +           # vtx[11] cell anchor
        [SENTINEL] * 14 +    # vtx[12..25]
        [(1, 3)] +            # vtx[26]
        [(16385, 2)] +        # vtx[27]
        [(11, 65535)] +       # vtx[28]
        [(from_lat_rel, from_lon_rel)] +   # vtx[29] FROM
        [(to_lat_rel, to_lon_rel)]         # vtx[30] TO
    )
    if shape_points:
        vtx += shape_points

    rec = MapalRecord(sub_tile=sub_tile, link_id=link_id, vtx=vtx)
    raw = encode_record(rec)
    return _compress(raw)


def read_tile(path: str) -> bytes:
    """Read a tile file."""
    with open(path, 'rb') as f:
        return f.read()


# Header template cache (filled on first call)
_HEADER_TEMPLATE: bytes | None = None
_HEADER_SIZE = 0x1100  # 4352 bytes
_NEW_TILE_SIZE = 4 * 1024 * 1024  # 4 MB — generous free space for dense urban tiles


def _get_header_template(mapal_dir: str) -> bytes:
    """Return a zeroed-spatial-index tile header (4352 bytes) from any existing tile."""
    global _HEADER_TEMPLATE
    if _HEADER_TEMPLATE is not None:
        return _HEADER_TEMPLATE
    import os
    for fname in sorted(os.listdir(mapal_dir)):
        if not fname.endswith(".DAT"):
            continue
        path = os.path.join(mapal_dir, fname)
        try:
            with open(path, 'rb') as f:
                hdr = bytearray(f.read(_HEADER_SIZE))
            if len(hdr) < _HEADER_SIZE:
                continue
            # Verify starts with CLARION UTF-16LE marker
            if hdr[1:2] != b'\x43':
                continue
            # Zero out spatial index at 0x1000-0x10ff
            for i in range(0x1000, 0x1100):
                hdr[i] = 0x00
            _HEADER_TEMPLATE = bytes(hdr)
            return _HEADER_TEMPLATE
        except Exception:
            continue
    raise RuntimeError("No valid tile found to use as header template")


def ensure_tile_exists(tile_path: str, mapal_dir: str, size: int = _NEW_TILE_SIZE) -> bool:
    """Create a minimal empty tile file if it doesn't exist.

    Returns True if a new tile was created, False if it already existed.
    """
    import os
    if os.path.exists(tile_path):
        return False
    hdr = _get_header_template(mapal_dir)
    content = hdr + b'\x00' * (size - len(hdr))
    with open(tile_path, 'wb') as f:
        f.write(content)
    return True


def get_last_link_id(data: bytes) -> int:
    """Return the highest link_id currently in the tile."""
    max_id = 0
    for pos in find_records(data):
        try:
            raw, _ = _decompress(data, pos)
            hdr = struct.unpack_from('>22H', raw[:HDR_SIZE])
            if hdr[2] == 25:  # valid record
                max_id = max(max_id, hdr[1])
        except Exception:
            continue
    return max_id


def get_free_space(data: bytes) -> int:
    """Return number of trailing null bytes available for appending."""
    i = len(data) - 1
    while i >= 0 and data[i] == 0:
        i -= 1
    return len(data) - i - 1


def append_record(path: str, compressed: bytes, dry_run: bool = False) -> int:
    """
    Append a compressed record to a tile file (with 2-byte null gap prefix).

    Returns:
        Offset where the record was written.

    Raises:
        ValueError: if not enough free space.
    """
    data = bytearray(read_tile(path))
    free = get_free_space(data)
    needed = len(compressed) + 2

    if needed > free:
        raise ValueError(
            f"Not enough space in {path}: need {needed}B, have {free}B"
        )

    # Find insert point (right after last record)
    positions = find_records(bytes(data))
    last_pos = positions[-1]
    for end in range(last_pos + 4, min(last_pos + 8192, len(data))):
        try:
            zlib.decompress(bytes(data[last_pos:end]))
            insert_at = end
            break
        except Exception:
            continue

    to_write = b'\x00\x00' + compressed
    record_offset = insert_at + 2

    if not dry_run:
        for i, b in enumerate(to_write):
            data[insert_at + i] = b
        with open(path, 'wb') as f:
            f.write(bytes(data))

    return record_offset


def scan_nodes(data: bytes, lat_lo: float, lat_hi: float, lon_lo: float, lon_hi: float,
               lat_base: float, lon_base: float) -> list[dict]:
    """
    Scan a tile for all road nodes within a geographic bounding box.

    Returns list of dicts with keys: lat, lon, link_id, offset, vtx_idx
    """
    SCALE = 65536
    lat_rel_lo = int((lat_lo - lat_base) * SCALE)
    lat_rel_hi = int((lat_hi - lat_base) * SCALE)
    lon_rel_lo = int((lon_lo - lon_base) * SCALE)
    lon_rel_hi = int((lon_hi - lon_base) * SCALE)

    nodes = []
    for pos in find_records(data):
        try:
            raw, _ = _decompress(data, pos)
            if len(raw) < HDR_SIZE:
                continue
            hdr = struct.unpack_from('>22H', raw[:HDR_SIZE])
            if hdr[2] != 25:
                continue
            vtx_count = (len(raw) - HDR_SIZE) // VTX_SIZE
            for vi in range(vtx_count):
                vy, vx = struct.unpack_from('>HH', raw[HDR_SIZE + vi * VTX_SIZE:HDR_SIZE + (vi + 1) * VTX_SIZE])
                if lat_rel_lo <= vy <= lat_rel_hi and lon_rel_lo <= vx <= lon_rel_hi:
                    nodes.append({
                        'lat': lat_base + vy / SCALE,
                        'lon': lon_base + vx / SCALE,
                        'link_id': hdr[1],
                        'offset': pos,
                        'vtx_idx': vi,
                    })
        except Exception:
            continue
    return nodes
