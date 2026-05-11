"""Tests for lib/mapal.py — MAPAL record encoding/decoding."""
import sys, os, struct, zlib
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.mapal import (
    encode_record, decode_record, build_road_record, _compress, _decompress,
    find_records, get_free_space, MapalRecord,
    ZLIB_MAGIC, WBITS, ZLEVEL, HDR_SIZE, SENTINEL, HDR_CONST
)


def _with_padding(compressed: bytes, pad: int = 4) -> bytes:
    """Add trailing null bytes so _decompress can scan past the record end."""
    return compressed + b'\x00' * pad


def test_zlib_magic():
    """Compressed records must start with 0x58 0x85."""
    raw = b'\x00' * 100
    compressed = _compress(raw)
    assert compressed[:2] == ZLIB_MAGIC, f"Got {compressed[:2].hex()}"


def test_encode_decode_roundtrip():
    """encode_record → decode_record should be lossless."""
    vtx = (
        [SENTINEL] * 11 + [(26, 5)] + [SENTINEL] * 14 +
        [(1, 3)] + [(16385, 2)] + [(11, 65535)] +
        [(26241, 27648)] + [(26522, 27564)]
    )
    rec = MapalRecord(sub_tile=0x905c, link_id=44032, vtx=vtx)
    raw = encode_record(rec)
    decoded = decode_record(raw)

    assert decoded.sub_tile == rec.sub_tile
    assert decoded.link_id == rec.link_id
    assert decoded.vtx == rec.vtx


def test_header_constants():
    """Header constants must match expected values in all encoded records."""
    vtx = (
        [SENTINEL] * 11 + [(26, 5)] + [SENTINEL] * 14 +
        [(1, 3)] + [(16385, 2)] + [(11, 65535)] +
        [(1000, 2000)] + [(3000, 4000)]
    )
    rec = MapalRecord(sub_tile=0x1234, link_id=999, vtx=vtx)
    raw = encode_record(rec)
    hdr = struct.unpack_from('>22H', raw[:HDR_SIZE])

    assert hdr[2] == 25, f"hdr[2]={hdr[2]} expected 25"
    assert hdr[3] == 13434, f"hdr[3]={hdr[3]}"
    assert hdr[4] == 10991, f"hdr[4]={hdr[4]}"
    assert hdr[7] == 65535, f"hdr[7]={hdr[7]}"
    assert hdr[9] == 11, f"hdr[9]={hdr[9]}"
    assert hdr[11] == 65535, "hdr[11] should be 65535 for simple records"
    assert hdr[12] == 0, "hdr[12] should be 0"


def test_build_road_record():
    """build_road_record should produce a valid compressed record."""
    compressed = build_road_record(
        link_id=44032,
        sub_tile=0x905c,
        from_lat_rel=26241, from_lon_rel=27648,
        to_lat_rel=26522, to_lon_rel=27564,
    )
    assert compressed[:2] == ZLIB_MAGIC

    # _decompress needs trailing bytes to scan past the end of the record
    raw, size = _decompress(_with_padding(compressed), 0)
    assert len(raw) >= HDR_SIZE
    hdr = struct.unpack_from('>22H', raw[:HDR_SIZE])
    assert hdr[0] == 0x905c  # sub_tile
    assert hdr[1] == 44032   # link_id
    assert hdr[2] == 25      # required constant


def test_auger_shell_regression():
    """Exact values written to B20R0B0R.DAT for Auger Shell Court."""
    compressed = build_road_record(
        link_id=44032,
        sub_tile=0x905c,
        from_lat_rel=26241, from_lon_rel=27648,
        to_lat_rel=26522, to_lon_rel=27564,
    )
    assert compressed[0] == 0x58 and compressed[1] == 0x85
    assert len(compressed) <= 70, f"Record too large: {len(compressed)}B"

    # Decode and verify node positions
    raw, _ = _decompress(_with_padding(compressed), 0)
    hdr = struct.unpack_from('>22H', raw[:HDR_SIZE])
    vtx_count = (len(raw) - HDR_SIZE) // 4
    assert vtx_count >= 31, f"Need at least 31 vtx, got {vtx_count}"

    from_node = struct.unpack_from('>HH', raw[HDR_SIZE + 29 * 4:HDR_SIZE + 30 * 4])
    to_node   = struct.unpack_from('>HH', raw[HDR_SIZE + 30 * 4:HDR_SIZE + 31 * 4])
    assert from_node == (26241, 27648), f"from_node={from_node}"
    assert to_node   == (26522, 27564), f"to_node={to_node}"


def test_find_records():
    """find_records should locate all 0x5885 magic bytes."""
    rec1 = build_road_record(1, 0x1000, 100, 200, 300, 400)
    rec2 = build_road_record(2, 0x1001, 500, 600, 700, 800)
    data = rec1 + b'\x00\x00' + rec2 + b'\x00\x00'
    positions = find_records(data)
    assert 0 in positions
    assert len(rec1) + 2 in positions


def test_get_free_space():
    """get_free_space should count trailing null bytes."""
    data = b'\x58\x85\xAA\xBB' + b'\x00' * 100
    assert get_free_space(data) == 100

    data2 = b'\x58\x85\xAA\xBB\x00\x00' + b'\x11\x22' + b'\x00' * 50
    assert get_free_space(data2) == 50


def test_compress_decompress_roundtrip():
    """_compress → _decompress should recover original bytes."""
    original = b'\x01\x02\x03' * 50
    compressed = _compress(original)
    assert compressed[:2] == ZLIB_MAGIC
    recovered, _ = _decompress(_with_padding(compressed), 0)
    assert recovered == original


if __name__ == '__main__':
    tests = [
        test_zlib_magic, test_encode_decode_roundtrip, test_header_constants,
        test_build_road_record, test_auger_shell_regression,
        test_find_records, test_get_free_space, test_compress_decompress_roundtrip,
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
