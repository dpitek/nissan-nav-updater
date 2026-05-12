# nissan-nav-updater

> ⚠️ **READ THE DISCLAIMERS BELOW BEFORE USE.** This project modifies binary data
> on a proprietary navigation SD card. Incorrect use can corrupt your card.
> **Use entirely at your own risk.**

Scripts to update map data on Nissan/Infiniti navigation SD cards using free
[OpenStreetMap](https://www.openstreetmap.org) data. Developed around the
**Clarion CLA-NAVI06-01** nav unit format with NAVTEQ 14Q4 (January 2015) data.

**Tested on:** Infiniti Q60 (navigation SD card, CLA-NAVI06-01 unit)

Compatible cards share the same directory structure:
`MAPAL001/`, `REFER001/`, `REFER002/`, `HOUSE001/`, `RDSTM001/`

---

## What This Does and Does Not Do

**Does:**
- Adds missing roads to the **map display layer** (e.g. new subdivisions built after 2015)
- Updates the **Nearby Places POI database** with current businesses from OpenStreetMap
- Patches dealership coordinates in the dealer POI database

**Does NOT:**
- Enable turn-by-turn routing to new roads (requires RDSTM001 routing graph — not modified)
- Enable address search for new roads (requires HOUSE001 B-tree — not modified)
- Replace or replicate any official Nissan/Infiniti/HERE map update
- Guarantee accuracy, completeness, or fitness for navigation use

---

## ⚠️ Disclaimers

### Use At Your Own Risk
This software is provided **"as is," without warranty of any kind**, express or
implied. The authors assume no liability for damage to navigation hardware,
SD cards, vehicle systems, or any harm resulting from reliance on modified map
data. **Always maintain a verified backup of your original card data before
making any modifications.**

### Not an Official Product
This project is **not affiliated with, endorsed by, or associated with** Nissan,
Infiniti, Clarion, HERE Technologies (formerly NAVTEQ), Zenrin, or any other
company whose products may be referenced. All trademarks, trade names, and brand
names are the property of their respective owners and are used here solely for
descriptive, interoperability, and identification purposes under nominative fair use.

### No Proprietary Data Included
This repository contains **no proprietary map data, firmware, or software**
from any navigation vendor. The scripts operate on binary files already present
on your legally obtained navigation card. No copyrighted data from Nissan,
Clarion, HERE/NAVTEQ, or Zenrin is distributed here.

### Reverse Engineering Notice
The card format was analyzed through black-box reverse engineering of publicly
accessible binary structures (zlib compression headers, big-endian integer
fields, file naming patterns). No proprietary SDKs, leaked documentation, or
confidential information were used or are required.

### OpenStreetMap Attribution
All map data written to the card by these scripts is sourced exclusively from
**OpenStreetMap**, licensed under the Open Database License (ODbL):

> © OpenStreetMap contributors
> [openstreetmap.org/copyright](https://www.openstreetmap.org/copyright)

Any product or derivative using this tool that incorporates OSM data must
include this attribution and comply with the ODbL.

### Legal Jurisdiction
Laws regarding reverse engineering and interoperability vary by jurisdiction.
In the United States, interoperability-focused reverse engineering is generally
permitted under 17 U.S.C. § 1201(f). This project is intended solely for
personal, non-commercial interoperability use. **Consult legal counsel before
using or distributing this software in your jurisdiction.**

---

## Quickstart

### Requirements
- macOS (card detection uses `diskutil`)
- Python 3.10+
- Nav SD card mounted (auto-detected by volume label), or a mounted disk image

### Back up your card first
```bash
# Create a full image backup before ANY modifications:
sudo dd if=/dev/diskN of=~/nav-card-backup-$(date +%Y%m%d).img bs=1m
# Replace diskN with your card's disk identifier (check: diskutil list)
```

### Add a single missing road
```bash
python updaters/add_road.py \
  --from-lat 34.400 --from-lon -77.578 \
  --to-lat 34.405 --to-lon -77.579 \
  --name "Example Road Name"
```

### Interactive road addition (with geocoding)
```bash
python updaters/add_road_interactive.py
```

### Update POI database from OpenStreetMap
```bash
# Fetch OSM data first (no card needed, ~2 min):
python updaters/update_pois.py --fetch-only

# Apply to card:
python updaters/update_pois.py
```

### Bulk road update (SLOW — plan for hours on full state coverage)
```bash
python updaters/update_roads.py --dry-run  # preview first, no writes
python updaters/update_roads.py
```

### Full pipeline
```bash
python run_update.py --stages check,pois,eject
python run_update.py --stages check,pois,roads,eject  # includes road update
```

### Test against a disk image (no physical card needed)
```bash
# Mount a card image read-write:
hdiutil attach your-nav-card-backup.img

# Scripts auto-detect the mounted volume
python updaters/add_road.py --from-lat ...
```

---

## Card Format Reference

> ℹ️ The following is the result of independent reverse engineering.
> Field names and interpretations are inferred, not sourced from vendor documentation.

### MAPAL tile files (`MAPAL001/`)
Map display layer. One tile file per **1.0° lat × 1.0° lon** region.

**Filename format:** `B{B:02X}R0{R:X}{S}R.DAT`
- `B = (lat_base − 30) × 8`  → `lat_base = B / 8 + 30`
- `R = floor((lon_base + 100) / 2)` (half-degree bucket)
- `S = '0'` if lon_base is even  (lon_base = R × 2 − 100)
- `S = '8'` if lon_base is odd   (lon_base = R × 2 − 100 + 1)

Pairs of tiles (S='0' and S='8') together span a 2° lon band.
New tiles can be created for areas not covered by the original card.

**File structure:**
- Bytes 0x0000–0x10ff: header (version strings + spatial index)
- Bytes 0x1100+: compressed road records
- Trailing null bytes: free space for appending

**Record structure (per record):**
- zlib compressed stream (magic bytes `0x58 0x85`, wbits=13, level=6)
- Decompressed: 44-byte header (22 × u16, big-endian) + vtx array (4 bytes each)
- Records are separated by 0 or 2 null bytes; files end with null padding

**Observed header constants (present in all valid records):**
```
hdr[2]=25, hdr[3]=13434, hdr[4]=10991, hdr[7]=65535, hdr[9]=11
```

**Coordinate encoding (tile-relative u16):**
```python
lat_rel = int((lat - lat_base) * 65536)  # 0..65535 → 1.0° lat span
lon_rel = int((lon - lon_base) * 65536)  # 0..65535 → 1.0° lon span
```

### POI database (`REFER001/POINT047.DAT.cmp` + `.ind`)
Proprietary B+ trie structure. Appended blocks are discoverable via **Nearby Places**
spatial scan. Search by Name uses the trie index and will not find appended records.

### Dealer POI database (`REFER002/POINT047.DAT`)
Same general format; contains dealership records.

### Address index (`HOUSE001/`) — NOT modified by this project
Proprietary B-tree, 512-byte pages. Structure is too complex to safely update.
Roads added to MAPAL will appear on the map but cannot be found by address search.

---

## Confirmed Results

Tested on an **Infiniti Q60** with CLA-NAVI06-01 navigation unit:

| Modification | Result | Layer |
|---|---|---|
| New road segment (post-2015 subdivision) | ✅ Visible on map display | MAPAL |
| New tile creation (area not on original card) | ✅ Functional — road visible | MAPAL |
| ~15,000 regional POIs from OSM | ✅ Appear in Nearby Places | REFER001 |
| Dealership coordinate corrections | ✅ Applied | REFER002 |
| Address search for new roads | ❌ Not functional | HOUSE001 (not modified) |
| Turn-by-turn routing to new roads | ❌ Not functional | RDSTM001 (not modified) |

---

## Library Reference

| Module | Purpose |
|---|---|
| `lib/tiles.py` | Tile naming, coordinate math, tile enumeration |
| `lib/mapal.py` | MAPAL record read/decode/encode/build/append/scan |
| `lib/osm.py` | OpenStreetMap Overpass API fetcher (roads + POIs) |
| `lib/card.py` | macOS card detection, validation, ejection |
| `lib/refer.py` | POINT047 POI block reader/writer |

---

## Running Tests
```bash
python3 tests/test_mapal.py
python3 tests/test_tiles.py
```

---

## Contributing

Bug reports and format corrections are welcome. If you have confirmed this works
(or does not work) on a specific vehicle or card part number, please open an issue
with that information.

Pull requests that include proprietary data, leaked vendor documentation, or
copyrighted map content will not be accepted.
