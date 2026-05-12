"""
OpenStreetMap / Overpass API data fetcher.

Fetches road ways and POI nodes for a given bounding box or region.
All data is ODbL licensed — attribution required:
  © OpenStreetMap contributors (https://www.openstreetmap.org/copyright)
"""

import json
import time
import urllib.request
import urllib.parse
from typing import Generator

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 120  # seconds


def _query(ql: str, retries: int = 3) -> dict:
    """Execute an Overpass QL query and return parsed JSON."""
    data = urllib.parse.urlencode({"data": ql}).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(OVERPASS_URL, data=data)
            req.add_header("User-Agent", "nissan-nav-updater/1.0 (personal nav modification tool)")
            with urllib.request.urlopen(req, timeout=OVERPASS_TIMEOUT) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  Retry {attempt+1}/{retries} after error: {e}")
            time.sleep(5 * (attempt + 1))


def fetch_roads(bbox: tuple[float, float, float, float],
                road_types: list[str] | None = None) -> list[dict]:
    """
    Fetch road ways from OSM within a bounding box.

    Args:
        bbox: (south, west, north, east) in decimal degrees
        road_types: list of OSM highway values to include.
                    Defaults to residential/tertiary/secondary/primary/trunk.

    Returns:
        List of dicts with keys: id, name, highway, nodes (list of {lat,lon})
    """
    if road_types is None:
        road_types = [
            "motorway", "trunk", "primary", "secondary", "tertiary",
            "residential", "unclassified",
            # "service" excluded — driveways/parking lots explode query size in cities
        ]

    south, west, north, east = bbox
    hw_filter = "|".join(road_types)
    # maxsize:200MB prevents runaway downloads on dense urban tiles
    ql = f"""
[out:json][timeout:{OVERPASS_TIMEOUT}][maxsize:209715200];
(
  way["highway"~"^({hw_filter})$"]({south},{west},{north},{east});
);
out body geom;
"""
    result = _query(ql)
    roads = []
    for el in result.get("elements", []):
        if el["type"] != "way":
            continue
        geometry = el.get("geometry", [])
        if len(geometry) < 2:
            continue
        roads.append({
            "id": el["id"],
            "name": el.get("tags", {}).get("name", ""),
            "highway": el.get("tags", {}).get("highway", ""),
            "nodes": [{"lat": g["lat"], "lon": g["lon"]} for g in geometry],
        })
    return roads


def fetch_pois(bbox: tuple[float, float, float, float],
               categories: list[str] | None = None) -> list[dict]:
    """
    Fetch POI nodes from OSM within a bounding box.

    Args:
        bbox: (south, west, north, east)
        categories: list of OSM amenity values. Defaults to common nav POI types.

    Returns:
        List of dicts with keys: id, name, amenity, lat, lon
    """
    if categories is None:
        categories = [
            "restaurant", "fast_food", "fuel", "bank", "cafe",
            "supermarket", "convenience", "pharmacy", "hotel",
        ]

    south, west, north, east = bbox
    cat_filter = "|".join(categories)
    ql = f"""
[out:json][timeout:{OVERPASS_TIMEOUT}];
(
  node["amenity"~"^({cat_filter})$"]["name"]({south},{west},{north},{east});
);
out body;
"""
    result = _query(ql)
    pois = []
    for el in result.get("elements", []):
        if el["type"] != "node":
            continue
        tags = el.get("tags", {})
        name = tags.get("name", "").strip()
        if not name:
            continue
        pois.append({
            "id": el["id"],
            "name": name,
            "amenity": tags.get("amenity", ""),
            "lat": el["lat"],
            "lon": el["lon"],
        })
    return pois


def nc_bbox() -> tuple[float, float, float, float]:
    """Bounding box for North Carolina."""
    return (33.75, -84.35, 36.60, -75.40)


def tile_bbox(lat_base: float, lon_base: float) -> tuple[float, float, float, float]:
    """Bounding box for a single MAPAL tile (1.0° lat × 1.0° lon)."""
    return (lat_base, lon_base, lat_base + 1.0, lon_base + 1.0)


def segment_length_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate distance between two points in km."""
    import math
    dlat = (lat2 - lat1) * 111.0
    dlon = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2)) * 111.0
    return math.sqrt(dlat ** 2 + dlon ** 2)
