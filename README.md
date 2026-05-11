# nissan-nav-updater

Scripts to update the map data on Nissan/Infiniti navigation SD cards using
free OpenStreetMap data. Built around the **Clarion CLA-NAVI06-01** format
(NAVTEQ 14Q4, Jan 2015 data vintage) found on:

- Nissan 485-1929-00 SD card
- Infiniti equivalents sharing the same card format

**What this does:**
- Adds missing roads to the map display layer (new subdivisions, etc.)
- Updates POI database with current businesses from OpenStreetMap
- Corrects dealership coordinates in REFER002

**What this does NOT do:**
- Turn-by-turn routing to new roads (requires RDSTM001 routing graph update)
- Address search for new roads (requires HOUSE001 B-tree update)
- Any official map update — this is a DIY hack

---

## Attribution

All map data added by these scripts is sourced from **OpenStreetMap**:

> © OpenStreetMap contributors — [openstreetmap.org/copyright](https://www.openstreetmap.org/copyright)
> Licensed under the Open Database License (ODbL)

---

## Quickstart

### Requirements
- macOS (card detection uses `diskutil`)
- Python 3.10+
- Nav SD card mounted at `/Volumes/485-1929-00` (or disk image)

### Add a single missing road
```bash
python updaters/add_road.py \
  --from-lat 34.4004 --from-lon -77.5780 \
  --to-lat 34.4052 --to-lon -77.5794 \
  --name "Auger Shell Court"
```

### Interactive road addition (with geocoding)
```bash
python updaters/add_road_interactive.py
```

### Update NC POI database
```bash
# Fetch OSM data (no card needed, ~2 min):
python updaters/update_pois.py --fetch-only

# Apply to card:
python updaters/update_pois.py
```

### Bulk NC road update (SLOW — hours)
```bash
python updaters/update_roads.py --dry-run  # preview first
python updaters/update_roads.py
```

### Full pipeline
```bash
python run_update.py --stages check,pois,eject
python run_update.py --stages check,pois,roads,eject  # includes road update
```

### Test against a disk image (no physical card needed)
```bash
# Mount image read-write:
hdiutil attach infiniti-q60-nav-working.img

# Run scripts — they'll auto-detect the mounted volume
python updaters/add_road.py --from-lat ...
```

---

## Card Format Reference

### MAPAL tile files (`MAPAL001/`)
Map display layer. One tile per 1.0° lat × 2.0° lon region.

**Naming:** `B{B:02X}R0{R:X}0R.DAT`
- `B = (lat_base - 30) × 8`  → lat_base = B/8 + 30
- `R = (lon_base + 100) / 2` → lon_base = R×2 - 100

**Example:** `B20R0B0R.DAT` → lat_base=34.0°, lon_base=-78.0° (covers NC coast)

**Record structure:**
- zlib compressed (magic `0x58 0x85`, wbits=13, level=6)
- 44-byte header: 22 × u16 big-endian
- Variable vtx array: 4 bytes each (2 × u16 big-endian, lat/lon tile-relative)

**Header constants (all valid records):**
```
hdr[2]=25, hdr[3]=13434, hdr[4]=10991, hdr[7]=65535, hdr[9]=11
```

**Coordinate encoding:**
```python
lat_rel = int((lat - lat_base) * 65536)  # 0..65535
lon_rel = int((lon - lon_base) * 65536)  # 0..65535
```

### POI database (`REFER001/POINT047.DAT.cmp` + `.ind`)
Proprietary Zenrin B+ trie. Appended blocks are visible via **Nearby Places**
spatial scan but NOT via Search by Name (trie index not updated).

### Dealer database (`REFER002/POINT047.DAT`)
Same format; smaller file with dealership records.

### Address index (`HOUSE001/`) — NOT modified
512-byte B-tree pages, Zenrin format. Too complex to update. New roads
added via MAPAL are not address-searchable.

---

## Confirmed Results (Nissan 485-1929-00, May 2026)

| Change | Status | Method |
|--------|--------|--------|
| Auger Shell Court, Surf City NC | ✅ Visible on map | MAPAL record injection |
| 15,661 NC POIs (OSM statewide) | ✅ Nearby Places | REFER001 block append |
| 137 NC Nissan dealer coord fixes | ✅ Applied | REFER002 patch |
| Address search for new roads | ❌ Not possible | HOUSE001 B-tree too complex |
| Turn-by-turn to new roads | ❌ Not possible | RDSTM001 not modified |

---

## Library Reference

| File | Purpose |
|------|---------|
| `lib/tiles.py` | Tile naming, coordinate math, NC tile enumeration |
| `lib/mapal.py` | MAPAL record read/write/encode/decode/append |
| `lib/osm.py` | OpenStreetMap Overpass API fetcher |
| `lib/card.py` | macOS card detection, validation, ejection |
| `lib/refer.py` | REFER001/002 POINT047 block reader/writer |

---

## Running Tests
```bash
python3 tests/test_mapal.py
python3 tests/test_tiles.py
```

---

## Disclaimer

This is a personal reverse-engineering project. The MAPAL/NAVTEQ format is
proprietary. No Nissan/Clarion/NAVTEQ software or copyrighted data is included.
All map data is from OpenStreetMap (ODbL). Use at your own risk.
