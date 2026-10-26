#!/usr/bin/env python3
"""CLI entry point for thesis experiments.

Usage
-----
Reliability — run one scenario N times with fixed devices:

    python run_experiment.py reliability \\
        --scenario scenarios/basic_call.txt \\
        --devices polye100 polye350 \\
        --runs 10

Generalization — run one scenario N times for each DUT model:

    python run_experiment.py generalization \\
        --scenario scenarios/basic_call.txt \\
        --duts polye100 polye350 yealinkt85 \\
        --secondary polye350 \\
        --runs 5

    Each DUT in --duts becomes Phone1; --secondary is Phone2.
    If a DUT and the secondary are the same physical device, pass the secondary
    ID twice and ensure there are two physical phones with those configs.

Options (both subcommands)
--------------------------
    --runs N          Number of runs  (default 10 for reliability, 5 for generalization)
    --reset-delay N   Seconds between runs while phones return to idle  (default 10)
    --no-ocr          Disable OCR context sent to Claude
    --vision          Send raw screenshots to Claude  (expensive)
    --mode M          dual (default) or fast
    --recovery        Enable Claude-driven recovery on step failure
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on the path when run as a script
sys.path.insert(0, str(Path(__file__).parent))

from auto_tester.devices.device_configs import make_device
from auto_tester.tests.experiments import (
    run_reliability,
    run_generalization,
    run_authoring,
)


def _load_devices(device_ids: list[str]) -> dict:
    """Load and connect devices in order. Phone1=device_ids[0], etc."""
    devices = {}
    for did in device_ids:
        print(f"[setup] Loading device '{did}'...")
        device = make_device(did)
        device.connect()
        devices[did] = device
    return devices


def _disconnect_all(devices: dict) -> None:
    for did, device in devices.items():
        try:
            device.disconnect()
        except Exception:
            pass


def _normalize_action_mode(mode: str) -> str:
    """Map CLI shorthand to internal action mode names."""
    return {
        "dual": "second_opinion",
        "fast": "analysis_only",
        # Backward-compatible aliases
        "second_opinion": "second_opinion",
        "analysis_only": "analysis_only",
    }[mode]


def _common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scenario", required=True, type=Path, help="Path to the scenario .txt file"
    )
    parser.add_argument(
        "--reset-delay",
        type=float,
        default=2,
        metavar="N",
        help="Seconds between runs for phones to return to idle (default: 10)",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Disable OCR context (faster but less accurate)",
    )
    parser.add_argument(
        "--vision",
        action="store_true",
        help="Send raw screenshots to Claude (slow, costs more)",
    )
    parser.add_argument(
        "--mode",
        "--action-mode",
        dest="action_mode",
        choices=["dual", "fast", "second_opinion", "analysis_only"],
        default="dual",
        help="Action selection mode: dual (default) or fast",
    )


def _parse_recording_header(seq_path: Path) -> tuple["Path | None", list[str]]:
    """Extract the scenario path and device IDs from a recording's sequence.txt header."""
    device_ids: list[str] = []
    scenario_path = None
    section = None
    for raw in seq_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "test devices":
            section = "devices"
        elif line == "scenario":
            section = "scenario"
        elif line == "action sequence":
            break
        elif section == "devices":
            device_ids.extend(line.split(","))
        elif section == "scenario":
            scenario_path = Path(line)
    return scenario_path, device_ids


def cmd_replay(args: argparse.Namespace) -> int:
    rec_dir = Path(args.recording)
    seq_path = rec_dir / "sequence.txt"

    if not seq_path.exists():
        print(f"Error: sequence.txt not found in {rec_dir}")
        return 1

    scenario_path, recorded_devices = _parse_recording_header(seq_path)

    if scenario_path is None:
        print(
            "Error: Recording is missing 'scenario' section — outdated format not supported"
        )
        return 1

    if not scenario_path.exists():
        print(f"Error: Scenario file not found: {scenario_path}")
        return 1

    device_ids = args.devices if args.devices else recorded_devices
    if not device_ids:
        print("Error: No devices specified and none found in recording header")
        return 1

    from auto_tester.tests.scenario import ScenarioRun

    devices = _load_devices(device_ids)
    action_mode = _normalize_action_mode(args.action_mode)
    try:
        runner = ScenarioRun(
            scenario_path,
            devices,
            use_ocr=not args.no_ocr,
            use_screenshots=args.vision,
            use_learning=True,
            action_mode=action_mode,
        )
        ok = runner.replay(rec_dir)
        return 0 if ok else 1
    finally:
        _disconnect_all(devices)


def cmd_authoring(args: argparse.Namespace) -> int:
    if not args.scenario.exists():
        print(f"Error: scenario file not found: {args.scenario}")
        return 1

    devices = _load_devices(args.devices)
    action_mode = _normalize_action_mode(args.action_mode)
    try:
        run_authoring(
            scenario_path=args.scenario,
            devices=devices,
            n_runs=args.runs,
            reset_delay=args.reset_delay,
            use_ocr=not args.no_ocr,
            use_screenshots=args.vision,
            action_mode=action_mode,
            reset_between_runs=not args.no_reset,
        )
    finally:
        _disconnect_all(devices)
    return 0


def cmd_reliability(args: argparse.Namespace) -> int:
    if not args.scenario.exists():
        print(f"Error: scenario file not found: {args.scenario}")
        return 1

    devices = _load_devices(args.devices)
    action_mode = _normalize_action_mode(args.action_mode)
    try:
        run_reliability(
            scenario_path=args.scenario,
            devices=devices,
            n_runs=args.runs,
            reset_delay=args.reset_delay,
            use_ocr=not args.no_ocr,
            use_screenshots=args.vision,
            action_mode=action_mode,
        )
    finally:
        _disconnect_all(devices)
    return 0


def cmd_generalization(args: argparse.Namespace) -> int:
    if not args.scenario.exists():
        print(f"Error: scenario file not found: {args.scenario}")
        return 1

    device_groups: list[dict] = []
    for dut_id in args.duts:
        if dut_id == args.secondary:
            print(f"Warning: DUT '{dut_id}' and secondary are the same device ID.")
            print("         Make sure these are two separate physical phones.")
        group_ids = [dut_id, args.secondary] if args.secondary else [dut_id]
        group = _load_devices(group_ids)
        device_groups.append(group)

    try:
        action_mode = _normalize_action_mode(args.action_mode)
        run_generalization(
            scenario_path=args.scenario,
            device_groups=device_groups,
            n_runs=args.runs,
            reset_delay=args.reset_delay,
            use_ocr=not args.no_ocr,
            use_screenshots=args.vision,
            action_mode=action_mode,
        )
    finally:
        for group in device_groups:
            _disconnect_all(group)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run thesis experiments (reliability / generalization)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── authoring ────────────────────────────────────────────────────────
    p_auth = sub.add_parser(
        "authoring",
        help="Measure authoring success rate (resets DUT contracts between runs)",
    )
    _common_args(p_auth)
    p_auth.add_argument(
        "--devices",
        nargs="+",
        required=True,
        metavar="ID",
        help="Device IDs in role order: Phone1 (DUT) first, then Phone2, ...",
    )
    p_auth.add_argument(
        "--runs",
        type=int,
        default=5,
        metavar="N",
        help="Number of authoring attempts (default: 5)",
    )
    p_auth.add_argument(
        "--no-reset",
        action="store_true",
        help=(
            "Do not clear DUT contracts/learned behavior between authoring runs "
            "(useful when preparing reliability from learned context)"
        ),
    )

    # ── reliability ─────────────────────────────────────────────────────
    p_rel = sub.add_parser("reliability", help="Multi-run pass-rate measurement")
    _common_args(p_rel)
    p_rel.add_argument(
        "--devices",
        nargs="+",
        required=True,
        metavar="ID",
        help="Device IDs in role order: Phone1 (DUT) first, then Phone2, ...",
    )
    p_rel.add_argument(
        "--runs", type=int, default=10, metavar="N", help="Number of runs (default: 10)"
    )

    # ── generalization ───────────────────────────────────────────────────
    p_gen = sub.add_parser("generalization", help="Cross-model pass-rate measurement")
    _common_args(p_gen)
    p_gen.add_argument(
        "--duts",
        nargs="+",
        required=True,
        metavar="ID",
        help="DUT device IDs to test (one run-set per model)",
    )
    p_gen.add_argument(
        "--secondary",
        metavar="ID",
        default=None,
        help="Device ID for Phone2 (secondary caller). Omit for single-device scenarios.",
    )
    p_gen.add_argument(
        "--runs", type=int, default=5, metavar="N", help="Runs per model (default: 5)"
    )

    # ── replay ──────────────────────────────────────────────────────────────────
    p_rep = sub.add_parser(
        "replay", help="Replay a saved recording with SSIM validation and LLM repair"
    )
    p_rep.add_argument(
        "--recording",
        required=True,
        type=Path,
        metavar="PATH",
        help="Path to the recording directory (must contain sequence.txt)",
    )
    p_rep.add_argument(
        "--devices",
        nargs="+",
        metavar="ID",
        default=None,
        help="Device IDs (optional; defaults to IDs stored in the recording header)",
    )
    p_rep.add_argument(
        "--no-ocr",
        action="store_true",
        help="Disable OCR context (faster but less accurate)",
    )
    p_rep.add_argument(
        "--vision",
        action="store_true",
        help="Send raw screenshots to Claude (slow, costs more)",
    )
    p_rep.add_argument(
        "--mode",
        "--action-mode",
        dest="action_mode",
        choices=["dual", "fast", "second_opinion", "analysis_only"],
        default="dual",
        help="Action selection mode for repair runs: dual (default) or fast",
    )

    args = parser.parse_args()

    if args.command == "authoring":
        return cmd_authoring(args)
    elif args.command == "reliability":
        return cmd_reliability(args)
    elif args.command == "generalization":
        return cmd_generalization(args)
    elif args.command == "replay":
        return cmd_replay(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
