"""Analyze experiment result JSONs and print thesis evaluation tables.

Usage:
    venv/bin/python analyze_results.py
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

RESULTS_DIR = Path("artifacts/results")
CONTRACTS_DIR = Path("contracts")

SCENARIO_LABELS: dict[str, str] = {
    "basic_call": "basic_call (contract)",
    "basic_call_no_scen": "basic_call (no contract)",
    "hold_resume_call": "hold_resume_call (contract)",
    "hold_resume_call_no_scen": "hold_resume_call (no contract)",
    "blind_transfer": "blind_transfer (contract)",
    "blind_transfer_no_scen": "blind_transfer (no contract)",
    "conference_call": "conference_call (contract)",
}

DEVICE_LABELS: dict[str, str] = {
    "polye100": "Poly E100",
    "polye350": "Poly E350",
    "yealinkt85": "Yealink T85",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mean(values: list) -> float | None:
    return round(statistics.mean(values), 2) if values else None


def _col(s, w: int) -> str:
    return str(s)[:w].ljust(w)


def _valid_runs(result: dict) -> list[dict]:
    return [d for d in result.get("run_details", []) if d.get("outcome") != "skipped"]


def load_results() -> list[dict]:
    results = []
    for f in sorted(RESULTS_DIR.glob("*.json")):
        try:
            results.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"Warning: could not load {f.name}: {exc}")
    return results


# ── Table 1: Reliability per scenario × device ────────────────────────────────


def table1_reliability(results: list[dict]) -> None:
    print("\n=== Table 1: Reliability (contract-based scenarios) ===")
    header = (
        f"{'Scenario':<32} {'Device':<14} {'Runs':>4} "
        f"{'Pass%':>6} {'API/run':>7} {'Time/run':>8} {'Cost/run':>10}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        scenario = r.get("scenario", "")
        if "no_scen" in scenario:
            continue
        dut = r.get("dut_id", "")
        valid = _valid_runs(r)
        if not valid:
            continue
        pass_pct = round((r.get("pass_rate") or 0) * 100, 0)
        api = _mean([d["api_calls"] for d in valid])
        dur = _mean([d["duration_s"] for d in valid])
        cost = _mean([d["cost_usd"] for d in valid])
        label = SCENARIO_LABELS.get(scenario, scenario)
        device = DEVICE_LABELS.get(dut, dut)
        print(
            f"{_col(label, 32)} {_col(device, 14)} {len(valid):>4} "
            f"{pass_pct:>5.0f}% {api:>7} {dur:>7}s ${cost:>9.5f}"
        )


# ── Table 2: Authoring run vs. dispatch runs ──────────────────────────────────


def table2_authoring_vs_dispatch(results: list[dict]) -> None:
    print("\n=== Table 2: First run (authoring) vs. subsequent runs (dispatch) ===")
    header = (
        f"{'Scenario':<32} {'Device':<14} "
        f"{'Run1 API':>8} {'Run1 cost':>10} "
        f"{'Disp API':>8} {'Disp cost':>10}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        scenario = r.get("scenario", "")
        if "no_scen" in scenario:
            continue
        dut = r.get("dut_id", "")
        valid = _valid_runs(r)
        if len(valid) < 2:
            continue
        run1 = valid[0]
        dispatch = valid[1:]
        d_api = _mean([d["api_calls"] for d in dispatch])
        d_cost = _mean([d["cost_usd"] for d in dispatch])
        label = SCENARIO_LABELS.get(scenario, scenario)
        device = DEVICE_LABELS.get(dut, dut)
        print(
            f"{_col(label, 32)} {_col(device, 14)} "
            f"{run1['api_calls']:>8} ${run1['cost_usd']:>9.5f} "
            f"{d_api:>8} ${d_cost:>9.5f}"
        )


# ── Table 3: Contract vs. no-contract ablation ────────────────────────────────


def table3_ablation(results: list[dict]) -> None:
    print("\n=== Table 3: Contract vs. no-contract ablation ===")
    header = (
        f"{'Mode':<36} {'Runs':>4} {'API/run':>7} "
        f"{'Time/run':>8} {'Cost/run':>10} {'Pass%':>6}"
    )
    print(header)
    print("-" * len(header))

    groups: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in results:
        scenario = r.get("scenario", "")
        base = scenario.replace("_no_scen", "")
        mode = "no_contract" if "no_scen" in scenario else "contract"
        # Keep the latest result per base+mode
        groups[base][mode] = r

    for base in sorted(groups):
        for mode in ("contract", "no_contract"):
            r = groups[base].get(mode)
            if r is None:
                continue
            valid = _valid_runs(r)
            if not valid:
                continue
            label = f"{base} ({mode.replace('_', ' ')})"
            api = _mean([d["api_calls"] for d in valid])
            dur = _mean([d["duration_s"] for d in valid])
            cost = _mean([d["cost_usd"] for d in valid])
            pass_pct = round((r.get("pass_rate") or 0) * 100, 0)
            print(
                f"{_col(label, 36)} {len(valid):>4} {api:>7} "
                f"{dur:>7}s ${cost:>9.5f} {pass_pct:>5.0f}%"
            )


# ── Table 4: Per-step API call breakdown ─────────────────────────────────────


def table4_step_breakdown(results: list[dict]) -> None:
    print("\n=== Table 4: Per-step API call breakdown ===")
    found = False
    for r in results:
        scenario = r.get("scenario", "")
        dut = r.get("dut_id", "")
        for run_i, run in enumerate(r.get("run_details", []), 1):
            steps = run.get("step_details")
            if not steps:
                continue
            print(
                f"Scenario: {scenario}  Device: {DEVICE_LABELS.get(dut, dut)}"
                f"  Run: {run_i}  Outcome: {run['outcome']}"
            )
            print(f"  {'#':>3}  {'Type':<24}  {'Step':<26}  {'API calls':>9}")
            print(f"  {'-'*3}  {'-'*24}  {'-'*26}  {'-'*9}")
            for s in steps:
                print(
                    f"  {s['step_index']:>3}  {_col(s['step_type'], 24)}"
                    f"  {_col(s['step_name'], 26)}  {s['api_calls']:>9}"
                )
            print()
            found = True
    if not found:
        print("  No step_details found — run a new experiment first.")


# ── Table 5: Contract implementations per device (the separation artefact) ───


def table5_contract_implementations() -> None:
    print("\n=== Table 5: Contract implementations per device ===")
    try:
        import yaml
    except ImportError:
        print("  pyyaml not installed.")
        return

    devices_seen: list[str] = []
    rows: list[tuple[str, dict[str, str]]] = []  # (contract_name, {device: steps_str})

    for path in sorted(CONTRACTS_DIR.glob("*.yaml")):
        try:
            defn = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        name = defn.get("contract", path.stem)
        impls: dict[str, dict] = defn.get("implementations") or {}
        if not impls:
            continue
        row: dict[str, str] = {}
        for device_id, impl in impls.items():
            if device_id not in devices_seen:
                devices_seen.append(device_id)
            steps = impl.get("steps") or []
            keys = [
                list(s.values())[0] if isinstance(s, dict) else str(s) for s in steps
            ]
            row[device_id] = " → ".join(keys) if keys else "(empty)"
        rows.append((name, row))

    if not rows:
        print("  No contract implementations found.")
        return

    dev_labels = [DEVICE_LABELS.get(d, d) for d in devices_seen]
    col_w = 26
    name_w = 28
    header = (
        _col("Contract", name_w) + "  " + "  ".join(_col(d, col_w) for d in dev_labels)
    )
    print(header)
    print("-" * len(header))
    for name, row in rows:
        cells = [_col(row.get(d, "—"), col_w) for d in devices_seen]
        print(_col(name, name_w) + "  " + "  ".join(cells))


# ── Table 6: Authoring reliability across independent attempts ────────────────


def table6_authoring(results: list[dict]) -> None:
    print("\n=== Table 6: Authoring reliability (independent attempts) ===")
    authoring_results = [r for r in results if r.get("experiment") == "authoring"]
    if not authoring_results:
        print("  No authoring experiment results found.")
        return
    header = (
        f"{'Scenario':<32} {'Device':<14} {'Runs':>4} "
        f"{'Pass%':>6} {'API/run':>7} {'Time/run':>8} {'Cost/run':>10}"
    )
    print(header)
    print("-" * len(header))
    for r in authoring_results:
        scenario = r.get("scenario", "")
        dut = r.get("dut_id", "")
        valid = _valid_runs(r)
        if not valid:
            continue
        pass_pct = round((r.get("pass_rate") or 0) * 100, 0)
        api = _mean([d["api_calls"] for d in valid])
        dur = _mean([d["duration_s"] for d in valid])
        cost = _mean([d["cost_usd"] for d in valid])
        device = DEVICE_LABELS.get(dut, dut)
        print(
            f"{_col(scenario, 32)} {_col(device, 14)} {len(valid):>4} "
            f"{pass_pct:>5.0f}% {api:>7} {dur:>7}s ${cost:>9.5f}"
        )


# ── Main ─────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    results = load_results()
    print(f"Loaded {len(results)} result file(s) from {RESULTS_DIR}/")
    table1_reliability(results)
    table2_authoring_vs_dispatch(results)
    table3_ablation(results)
    table4_step_breakdown(results)
    table5_contract_implementations()
    table6_authoring(results)
