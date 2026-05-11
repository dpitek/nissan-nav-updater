"""
Tile coordinate utilities for Clarion CLA-NAVI06-01 (Zenrin/NAVTEQ) format.

Tile naming convention: B{B_hex}R{R_hex}R.DAT
  B_hex = (lat_base - 30) * 8    (1 tile = 1.0 degree latitude)
  R_hex = (lon_base + 100) / 2   (1 tile = 2.0 degrees longitude)

  lat_base = B_hex / 8 + 30
  lon_base = R_hex * 2 - 100

  Filename format: B{B:02X}R0{R:X}0R.DAT
    The literal "0" before and after {R:X} are separator chars, not part of R.

Within each tile, coordinates are tile-relative u16 (big-endian):
  lat_rel = int((lat - tile_lat_base) * 65536)
  lon_rel = int((lon - tile_lon_base) * 65536)
  Range: 0..65535 per axis → spans ~1.0 degree lat, ~2.0 degrees lon
  Resolution: ~1.7m lat, ~1.4m lon at 35N
"""

SCALE = 65536


def tile_name(lat: float, lon: float) -> str:
    """Return the MAPAL tile filename for a given lat/lon (e.g. 'B20R0B0R.DAT').

    Each tile covers 1.0 degree lat × 2.0 degrees lon.
    B = (lat_base - 30) * 8  where lat_base = floor(lat - 30) + 30
    """
    B = int(lat - 30) * 8      # floor to 1-degree boundary, scale by 8
    R = int((lon + 100) / 2)
    return f"B{B:02X}R0{R:X}0R.DAT"


def tile_base(filename: str) -> tuple[float, float]:
    """Return (lat_base, lon_base) for a tile filename."""
    # Strip path and extension
    name = filename.split("/")[-1].replace(".DAT", "")
    # Format: B{B:02X}R0{R:X}0R  e.g. B20R0B0R
    # b_part: hex between 'B' and first 'R'
    # r_part: hex between first 'R0' and last '0R' (strip the literal 0 delimiters)
    b_part = name[1:name.index("R")]
    raw_r = name[name.index("R") + 1:name.rindex("R")]
    # raw_r is "0{R:X}0" — strip one leading and one trailing char (the literal zeros)
    r_part = raw_r[1:-1]
    B = int(b_part, 16)
    R = int(r_part, 16)
    lat_base = B / 8 + 30
    lon_base = R * 2 - 100
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
    """Return list of MAPAL tile files covering North Carolina (lat 33-37, lon -85 to -75)."""
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


def find_tile_for_coord(mapal_dir: str, lat: float, lon: float) -> str | None:
    """Find the MAPAL tile file that contains a given lat/lon."""
    import os
    for fname in os.listdir(mapal_dir):
        if not fname.endswith(".DAT"):
            continue
        try:
            lat_base, lon_base = tile_base(fname)
            # Each tile spans 0.125 deg lat, 2 deg lon
            if lat_base <= lat < lat_base + 1.0 and lon_base <= lon < lon_base + 2.0:
                return os.path.join(mapal_dir, fname)
        except Exception:
            continue
    return None
