#!/usr/bin/env python3
"""
add_road_interactive.py — Interactive wizard for adding a single road segment.

Geocodes an address using Nominatim (free, no API key), then injects it.

Usage:
    python updaters/add_road_interactive.py

Example session:
    Address: 171 Auger Shell Court, Surf City, NC
    → Geocoded: 34.4047, -77.5794
    → Tile: B20R0B0R.DAT
    → From: 34.4004, -77.5780  (enter manually or use geocoded)
    → To:   34.4052, -77.5794
    → Inject? [y/N]
"""

import sys
import os
import urllib.request
import urllib.parse
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.tiles import tile_name, tile_base
from lib.card import find_card


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def geocode(address: str) -> tuple[float, float] | None:
    """Geocode an address via Nominatim. Returns (lat, lon) or None."""
    params = urllib.parse.urlencode({
        'q': address,
        'format': 'json',
        'limit': 1,
    })
    url = f"{NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'nissan-nav-updater/1.0 (personal nav modification tool)')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read())
        if results:
            return float(results[0]['lat']), float(results[0]['lon'])
    except Exception as e:
        print(f"  Geocode error: {e}")
    return None


def prompt_float(label: str, default: float | None = None) -> float:
    while True:
        hint = f" [{default:.6f}]" if default is not None else ""
        raw = input(f"  {label}{hint}: ").strip()
        if not raw and default is not None:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  Invalid number. Try again.")


def main():
    print("=" * 60)
    print("  Nissan Nav — Add Road Segment (Interactive)")
    print("=" * 60)
    print()

    # Detect card
    card = find_card()
    if card:
        tile_dir = os.path.join(card, "MAPAL001")
        print(f"Card detected: {card}")
    else:
        tile_dir = input("Card not found. Enter MAPAL001 path: ").strip()

    if not os.path.isdir(tile_dir):
        print(f"ERROR: {tile_dir} not found")
        sys.exit(1)

    print()

    # Optional: geocode the road
    address = input("Road address to geocode (or press Enter to skip): ").strip()
    geo_lat = geo_lon = None
    if address:
        print("  Geocoding...", end='', flush=True)
        result = geocode(address)
        time.sleep(1)  # Nominatim rate limit
        if result:
            geo_lat, geo_lon = result
            print(f" {geo_lat:.6f}, {geo_lon:.6f}")
        else:
            print(" Not found. Enter coordinates manually.")

    print()
    print("Enter FROM endpoint (road start):")
    from_lat = prompt_float("lat", geo_lat)
    from_lon = prompt_float("lon", geo_lon)

    print("\nEnter TO endpoint (road end):")
    to_lat = prompt_float("lat", geo_lat)
    to_lon = prompt_float("lon", geo_lon)

    road_name = input("\nRoad name (optional, for logging): ").strip()

    # Preview
    fname = tile_name(from_lat, from_lon)
    print(f"\nTile:  {fname}")
    print(f"FROM:  {from_lat:.6f}, {from_lon:.6f}")
    print(f"TO:    {to_lat:.6f}, {to_lon:.6f}")
    if road_name:
        print(f"Name:  {road_name}")

    print()
    confirm = input("Inject? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("Cancelled.")
        sys.exit(0)

    # Delegate to add_road.py
    cmd_parts = [
        sys.executable, 'updaters/add_road.py',
        '--from-lat', str(from_lat),
        '--from-lon', str(from_lon),
        '--to-lat', str(to_lat),
        '--to-lon', str(to_lon),
        '--tile-dir', tile_dir,
    ]
    if road_name:
        cmd_parts += ['--name', road_name]

    import subprocess
    result = subprocess.run(cmd_parts, cwd=os.path.dirname(os.path.dirname(__file__)))
    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
