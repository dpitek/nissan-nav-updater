"""
Tile coordinate utilities for Clarion CLA-NAVI06-01 (Zenrin/NAVTEQ) format.

Tile naming convention: B{B_hex}R0{R_hex}{SUFFIX}R.DAT
  B_hex  = (lat_base - 30) * 8    (1 tile = 1.0 degree latitude)
  R_hex  = floor((lon_base + 100) / 2)  (R is the half-degree bucket)
  SUFFIX = '0' if lon_base is even  (R×2-100 + 0)
           '8' if lon_base is odd   (R×2-100 + 1)

  Each tile covers exactly 1.0° lat × 1.0° lon.
  Pairs of tiles (suffix '0' and '8') together span a 2° lon band.

  lat_base = B_hex / 8 + 30
  lon_base = R_hex * 2 - 100 + (1 if SUFFIX == '8' else 0)

Within each tile, coordinates are tile-relative u16 (big-endian):
  lat_rel = int((lat - tile_lat_base) * 65536)
  lon_rel = int((lon - tile_lon_base) * 65536)
  Range: 0..65535 per axis → spans exactly 1.0° lat and 1.0° lon
  Resolution: ~1.7m lat at 35N
"""

SCALE = 65536


def tile_name(lat: float, lon: float) -> str:
    """Return the MAPAL tile filename for a given lat/lon (e.g. 'B20R0B0R.DAT').

    Each tile covers 1.0° lat × 1.0° lon.
    B = (lat_base - 30) * 8  where lat_base = floor(lat - 30) + 30
    R = floor((lon + 100) / 2)  (half-degree bucket)
    SUFFIX: '0' if lon falls in even degree, '8' if in odd degree.
    """
    B = int(lat - 30) * 8          # floor to 1-degree lat boundary, scale by 8
    R = int((lon + 100) / 2)       # half-degree bucket
    even_base = R * 2 - 100        # even lon_base for this R value
    suffix = '8' if (lon - even_base) >= 1.0 else '0'
    return f"B{B:02X}R0{R:X}{suffix}R.DAT"


def tile_base(filename: str) -> tuple[float, float]:
    """Return (lat_base, lon_base) for a tile filename.

    Handles both '0' suffix (even lon) and '8' suffix (odd lon) tiles.
    """
    import re
    name = filename.split("/")[-1].replace(".DAT", "")
    # Format: B{B:02X}R0{R:X}{SUFFIX}R  e.g. B20R0B0R or B20R0B8R
    m = re.match(r'B([0-9A-Fa-f]{2})R0([0-9A-Fa-f])([08])R$', name)
    if not m:
        raise ValueError(f"Cannot parse tile filename: {filename!r}")
    B = int(m.group(1), 16)
    R = int(m.group(2), 16)
    suffix = m.group(3)
    lat_base = B / 8 + 30
    lon_base = R * 2 - 100 + (1 if suffix == '8' else 0)
    return lat_base, lon_base


def to_tile_rel(lat: float, lon: float, lat_base: float, lon_base: float) -> tuple[int, int]:
    """Convert absolute lat/lon to tile-relative u16 pair."""
    lat_rel = int((lat - lat_base) * SCALE)
    lon_rel = int((lon - lon_base) * SCALE)
    assert 0 <= lat_rel <= 65535, f"lat {lat} out of tile range (base {lat_base})"
    assert 0 <= lon_rel <= 65535, f"lon {lon} out of tile range (base {lon_base})"
    return lat_rel, lon_rel


def from_tile_rel(lat_rel: int, lon_rel: int, lat_base: float, lon_base: float) -> tuple[float, float]:
    """Convert tile-relative u16 pair to absolute lat/lon."""
    lat = lat_base + lat_rel / SCALE
    lon = lon_base + lon_rel / SCALE
    return lat, lon


def nc_mapal_tiles(mapal_dir: str) -> list[str]:
    """Return list of MAPAL tile files covering North Carolina (lat 33-37, lon -85 to -75).
    Only returns tiles that currently EXIST on disk."""
    import os
    tiles = []
    for fname in os.listdir(mapal_dir):
        if not fname.endswith(".DAT"):
            continue
        try:
            lat_base, lon_base = tile_base(fname)
            if 33.0 <= lat_base <= 36.0 and -85.0 <= lon_base <= -75.0:
                tiles.append(os.path.join(mapal_dir, fname))
        except Exception:
            continue
    return sorted(tiles)


def nc_all_tile_paths(mapal_dir: str) -> list[tuple[str, float, float, bool]]:
    """Return all expected MAPAL tile paths for NC coverage (lat 33-36, lon -85 to -76).

    Returns list of (path, lat_base, lon_base, exists) for every expected tile.
    Covers NC geographic extent including western mountains and the coast.
    """
    import os
    results = []
    # NC tiles: lat_base 33-36, lon_base -85 to -76 (1° steps)
    for lat_b in [33.0, 34.0, 35.0, 36.0]:
        B = int((lat_b - 30) * 8)
        for lon_b in range(-85, -75):  # -85, -84, ..., -76
            R = int((lon_b + 100) / 2)
            suffix = '8' if (lon_b - (R * 2 - 100)) >= 1 else '0'
            fname = f"B{B:02X}R0{R:X}{suffix}R.DAT"
            path = os.path.join(mapal_dir, fname)
            results.append((path, lat_b, float(lon_b), os.path.exists(path)))
    return sorted(results)


def find_tile_for_coord(mapal_dir: str, lat: float, lon: float) -> str | None:
    """Find the MAPAL tile file that contains a given lat/lon."""
    import os
    for fname in os.listdir(mapal_dir):
        if not fname.endswith(".DAT"):
            continue
        try:
            lat_base, lon_base = tile_base(fname)
            # Each tile spans exactly 1.0° lat × 1.0° lon
            if lat_base <= lat < lat_base + 1.0 and lon_base <= lon < lon_base + 1.0:
                return os.path.join(mapal_dir, fname)
        except Exception:
            continue
    return None
