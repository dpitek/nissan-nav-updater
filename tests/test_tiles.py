"""Tests for lib/tiles.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.tiles import tile_name, tile_base, to_tile_rel, from_tile_rel


def test_tile_name_topsail():
    # Topsail Island / Surf City at ~34.4N, -77.6W
    # lat_base = floor(34.4-30)*8 = 4*8=32=0x20; R = floor(22.4/2)=11=0xB
    assert tile_name(34.4, -77.6) == "B20R0B0R.DAT"


def test_tile_name_cary():
    # Cary NC at ~35.8N, -78.8W
    # lat_base = floor(35.8-30)*8 = 5*8=40=0x28; R = floor(21.2/2)=10=0xA
    assert tile_name(35.8, -78.8) == "B28R0A0R.DAT"


def test_tile_name_south_nc():
    # South NC border at ~33.9N
    # lat_base = floor(33.9-30)*8 = 3*8=24=0x18
    assert tile_name(33.9, -80.0).startswith("B18R")


def test_tile_base_b20():
    """Regression: B20R0B0R.DAT should give lat=34.0, lon=-78.0"""
    lat_base, lon_base = tile_base("B20R0B0R.DAT")
    assert lat_base == 34.0, f"lat_base={lat_base}"
    assert lon_base == -78.0, f"lon_base={lon_base}"


def test_tile_base_roundtrip():
    """tile_name → tile_base should recover the tile's base coordinates."""
    for lat in [33.1, 34.4, 35.0, 35.9, 36.5]:
        for lon in [-84.5, -80.0, -77.5]:
            fname = tile_name(lat, lon)
            lat_b, lon_b = tile_base(fname)
            assert lat_b <= lat < lat_b + 1.0, f"lat {lat} not in [{lat_b}, {lat_b+1.0}) via {fname}"
            assert lon_b <= lon < lon_b + 2.0, f"lon {lon} not in [{lon_b}, {lon_b+2.0}) via {fname}"


def test_tile_rel_roundtrip():
    """to_tile_rel → from_tile_rel should be near-lossless (<0.0001 deg)."""
    lat, lon = 34.4004, -77.5780
    lat_base, lon_base = 34.0, -78.0
    lat_rel, lon_rel = to_tile_rel(lat, lon, lat_base, lon_base)
    lat_out, lon_out = from_tile_rel(lat_rel, lon_rel, lat_base, lon_base)
    assert abs(lat_out - lat) < 1e-4, f"lat roundtrip error: {abs(lat_out - lat)}"
    assert abs(lon_out - lon) < 1e-4, f"lon roundtrip error: {abs(lon_out - lon)}"


def test_auger_shell_court_tile():
    """Auger Shell Court (34.4N, -77.58W) must resolve to tile B20R0B0R.DAT"""
    fname = tile_name(34.4004, -77.5780)
    assert fname == "B20R0B0R.DAT", f"Got {fname}"


def test_auger_shell_court_rel():
    """Tile-relative coords for Auger Shell Court insertion point."""
    lat_base, lon_base = tile_base("B20R0B0R.DAT")
    lat_rel, lon_rel = to_tile_rel(34.4004, -77.5780, lat_base, lon_base)
    # Session confirmed vtx[29] = (26241, 27648) — allow ±2 for float rounding
    assert abs(lat_rel - 26241) <= 2, f"lat_rel={lat_rel} expected ~26241"
    assert abs(lon_rel - 27648) <= 10, f"lon_rel={lon_rel} expected ~27648"


if __name__ == '__main__':
    tests = [
        test_tile_name_topsail, test_tile_name_cary, test_tile_name_south_nc,
        test_tile_base_b20, test_tile_base_roundtrip, test_tile_rel_roundtrip,
        test_auger_shell_court_tile, test_auger_shell_court_rel,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
