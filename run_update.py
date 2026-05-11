#!/usr/bin/env python3
"""
run_update.py — End-to-end Nissan/Infiniti nav card updater orchestrator.

Runs all update stages in order. Safely skips stages that aren't applicable.

Usage:
    python run_update.py [--stages pois,roads] [--dry-run] [--verbose]

Stages:
    check   — Detect and validate nav card
    pois    — Update REFER001 POI database from OSM (NC-wide)
    roads   — Inject missing roads into MAPAL tiles (NC-wide, SLOW)
    eject   — Safely eject the card when done

Default: check, pois, eject  (roads excluded by default — hours-long operation)
"""

import argparse
import sys
import os
import subprocess

sys.path.insert(0, os.path.dirname(__file__))

from lib.card import find_card, require_card, validate_card, card_info, eject_card, EXPECTED_LABEL


STAGE_ORDER = ['check', 'pois', 'roads', 'eject']
DEFAULT_STAGES = ['check', 'pois', 'eject']


def stage_check(card_path, args):
    print("=" * 60)
    print("STAGE: check")
    print("=" * 60)

    info = card_info(card_path)
    validation = validate_card(card_path)

    print(f"  Card:       {card_path}")
    print(f"  Total:      {info.get('total_gb', '?')} GB")
    print(f"  Used:       {info.get('used_gb', '?')} GB")
    print(f"  Free:       {info.get('free_gb', '?')} GB")
    print(f"  MAPAL tiles: {info.get('mapal_tiles', '?')}")
    print(f"  Valid:      {'✅' if validation['valid'] else '❌'}")

    if not validation['valid']:
        print(f"  Missing dirs: {validation['missing_dirs']}")
        print("  Card may be corrupt or wrong format. Aborting.")
        sys.exit(1)

    print()


def stage_pois(card_path, args):
    print("=" * 60)
    print("STAGE: pois")
    print("=" * 60)

    cmd = [sys.executable, 'updaters/update_pois.py']
    cmd += ['--refer-dir', os.path.join(card_path, 'REFER001')]
    if args.dry_run:
        cmd.append('--dry-run')
    if args.verbose:
        cmd.append('--verbose')

    print(f"  Running: {' '.join(cmd)}")
    print()
    result = subprocess.run(cmd, cwd=os.path.dirname(__file__))
    if result.returncode != 0:
        print(f"  [WARN] pois stage exited with code {result.returncode}")
    print()


def stage_roads(card_path, args):
    print("=" * 60)
    print("STAGE: roads  ⚠️  SLOW (hours for full NC)")
    print("=" * 60)

    cmd = [sys.executable, 'updaters/update_roads.py']
    cmd += ['--tile-dir', os.path.join(card_path, 'MAPAL001')]
    if args.dry_run:
        cmd.append('--dry-run')
    if args.verbose:
        cmd.append('--verbose')

    print(f"  Running: {' '.join(cmd)}")
    print()
    result = subprocess.run(cmd, cwd=os.path.dirname(__file__))
    if result.returncode != 0:
        print(f"  [WARN] roads stage exited with code {result.returncode}")
    print()


def stage_eject(card_path, args):
    print("=" * 60)
    print("STAGE: eject")
    print("=" * 60)

    if args.dry_run:
        print("  [DRY RUN] Would eject", card_path)
        return

    ok = eject_card(card_path)
    if ok:
        print(f"  ✅ Card ejected: {card_path}")
    else:
        print(f"  ❌ Eject failed — manually eject before removing card")
    print()


STAGE_FNS = {
    'check': stage_check,
    'pois': stage_pois,
    'roads': stage_roads,
    'eject': stage_eject,
}


def run(args):
    # Resolve stages
    if args.stages:
        stages = [s.strip() for s in args.stages.split(',')]
        invalid = [s for s in stages if s not in STAGE_ORDER]
        if invalid:
            print(f"ERROR: Unknown stages: {invalid}")
            print(f"Valid stages: {STAGE_ORDER}")
            sys.exit(1)
    else:
        stages = DEFAULT_STAGES

    # Detect card
    card_path = find_card(args.label)
    if card_path is None:
        print(f"ERROR: Nav card '{args.label}' not mounted.")
        print("  Insert the card and try again, or mount the disk image:")
        print("    hdiutil attach infiniti-q60-nav-working.img")
        sys.exit(1)

    print(f"Nav card: {card_path}")
    print(f"Stages:   {' → '.join(stages)}")
    if args.dry_run:
        print("Mode:     DRY RUN (no writes)")
    print()

    for stage in stages:
        fn = STAGE_FNS.get(stage)
        if fn:
            fn(card_path, args)
        else:
            print(f"[SKIP] Unknown stage: {stage}")


def main():
    parser = argparse.ArgumentParser(
        description="Nissan/Infiniti nav card updater — full pipeline orchestrator"
    )
    parser.add_argument('--stages', type=str, default=None,
                        help=f"Comma-separated stages to run (default: {','.join(DEFAULT_STAGES)}). "
                             f"All: {','.join(STAGE_ORDER)}")
    parser.add_argument('--label', type=str, default=EXPECTED_LABEL,
                        help=f"Volume label to find (default: {EXPECTED_LABEL})")
    parser.add_argument('--dry-run', action='store_true',
                        help="Simulate all stages without writing")
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()
    run(args)


if __name__ == '__main__':
    main()
