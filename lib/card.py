"""
Nav card detection and validation utilities.

Detects the Nissan/Infiniti nav SD card by volume label and validates
the expected directory structure for CLA-NAVI06-01 format.
"""

import os
import subprocess
import platform


EXPECTED_DIRS = ["MAPAL001", "REFER001", "REFER002", "HOUSE001", "RDSTM001"]
EXPECTED_LABEL = "485-1929-00"
VOLUMES_PATH = "/Volumes"


def find_card(label: str = EXPECTED_LABEL) -> str | None:
    """
    Find the mounted nav card by volume label.

    Returns:
        Mount path (e.g. '/Volumes/485-1929-00') or None if not found.
    """
    if platform.system() != "Darwin":
        raise RuntimeError("Card detection only supported on macOS")

    vol_path = os.path.join(VOLUMES_PATH, label)
    if os.path.isdir(vol_path):
        return vol_path

    # Also check with trailing space variants (e.g. '485-1929-00 1')
    for entry in os.listdir(VOLUMES_PATH):
        if entry.startswith(label):
            return os.path.join(VOLUMES_PATH, entry)

    return None


def validate_card(mount_path: str) -> dict:
    """
    Validate a mounted nav card has the expected structure.

    Returns:
        dict with keys: valid (bool), missing_dirs (list), mapal_tile_count (int)
    """
    result = {"valid": True, "missing_dirs": [], "mapal_tile_count": 0}

    for d in EXPECTED_DIRS:
        if not os.path.isdir(os.path.join(mount_path, d)):
            result["missing_dirs"].append(d)
            result["valid"] = False

    mapal_dir = os.path.join(mount_path, "MAPAL001")
    if os.path.isdir(mapal_dir):
        result["mapal_tile_count"] = len(
            [f for f in os.listdir(mapal_dir) if f.endswith(".DAT")]
        )

    return result


def eject_card(mount_path: str) -> bool:
    """Eject the card using diskutil (macOS)."""
    try:
        result = subprocess.run(
            ["diskutil", "eject", mount_path],
            capture_output=True, text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def card_info(mount_path: str) -> dict:
    """Return basic info about the mounted card."""
    info = {"mount_path": mount_path}
    try:
        stat = os.statvfs(mount_path)
        info["total_gb"] = round(stat.f_blocks * stat.f_frsize / 1e9, 1)
        info["free_gb"] = round(stat.f_bavail * stat.f_frsize / 1e9, 1)
        info["used_gb"] = round((stat.f_blocks - stat.f_bfree) * stat.f_frsize / 1e9, 1)
    except Exception:
        pass

    mapal_dir = os.path.join(mount_path, "MAPAL001")
    if os.path.isdir(mapal_dir):
        tiles = [f for f in os.listdir(mapal_dir) if f.endswith(".DAT")]
        info["mapal_tiles"] = len(tiles)

    return info


def require_card(label: str = EXPECTED_LABEL) -> str:
    """Find card or raise RuntimeError with helpful message."""
    path = find_card(label)
    if not path:
        raise RuntimeError(
            f"Nav card '{label}' not found. Insert the card and try again.\n"
            f"If using a disk image, mount it first: hdiutil attach your-image.img"
        )
    validation = validate_card(path)
    if not validation["valid"]:
        raise RuntimeError(
            f"Card at {path} is missing expected directories: {validation['missing_dirs']}"
        )
    return path
