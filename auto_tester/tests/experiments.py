"""Thesis experiment runners.

Two experiments are supported:

reliability
    Run the same scenario N times with the same devices.
    Quantifies LLM non-determinism: mean pass rate + std dev.

generalization
    Run the same scenario N times for each DUT model listed.
    Quantifies cross-model portability.

Results are written as JSON to artifacts/results/.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from auto_tester.devices.base import BaseDevice
from auto_tester.devices.device_configs import load_config, make_device
from auto_tester.tests.scenario import ScenarioRun, parse_scenario, run_scenario
import auto_tester.tests.claude as claude

RESULTS_DIR = Path("artifacts/results")
RESET_DELAY_DEFAULT = 2


# ── Helpers ───────────────────────────────────────────────────────────────────


def _stats(results: list[bool | None]) -> dict:
    """Compute pass rate and std dev from a run list. None entries = skipped."""
    valid = [r for r in results if r is not None]
    if not valid:
        return {"pass_rate": None, "std_dev": None, "n_valid": 0}
    values = [1.0 if r else 0.0 for r in valid]
    rate = sum(values) / len(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "pass_rate": round(rate, 4),
        "std_dev": round(std, 4),
        "n_valid": len(valid),
    }


def _check_starting_state(
    scenario_path: Path,
    devices: dict[str, BaseDevice],
    use_ocr: bool,
    use_screenshots: bool,
) -> tuple[bool, str]:
    """Return (ok, reason) for the scenario's starting state, without running the scenario."""
    starting_state, _ = parse_scenario(scenario_path)
    if starting_state is None:
        return True, ""
    run = ScenarioRun(scenario_path, devices, use_ocr, use_screenshots)
    return run.verify(starting_state)


def _save(result: dict, label: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{label}_{ts}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[experiment] Results saved → {path}")
    return path


# ── Reliability experiment ────────────────────────────────────────────────────


def run_reliability(
    scenario_path: Path,
    devices: dict[str, BaseDevice],
    n_runs: int = 10,
    reset_delay: float = RESET_DELAY_DEFAULT,
    use_ocr: bool = True,
    use_screenshots: bool = False,
    use_learning: bool = True,
    action_mode: str = "second_opinion",
    save: bool = True,
) -> dict:
    """Run *scenario_path* N times and collect pass/fail statistics.

    Between runs the function waits *reset_delay* seconds and re-checks the
    scenario's starting condition.  A run is marked 'skipped' (not counted in
    pass_rate / std_dev) when the starting condition is not met after the delay
    — this happens when a failed run leaves a phone in an unrecoverable state.

    Returns a result dict suitable for JSON serialisation.
    """
    dut_id = list(devices.keys())[0]
    print(f"\n[experiment] Reliability — {scenario_path.name} × {n_runs} runs")
    print(f"[experiment] DUT: {dut_id}   reset delay: {reset_delay}s\n")

    run_results: list[str] = []  # "pass" | "fail" | "skipped"
    run_details: list[dict] = []

    for run_i in range(1, n_runs + 1):
        if run_i > 1:
            print(
                f"\n[experiment] Waiting {reset_delay}s for phones to return to idle..."
            )
            time.sleep(reset_delay)

        # Gate: verify starting condition before spending API tokens
        ok, reason = _check_starting_state(
            scenario_path, devices, use_ocr, use_screenshots
        )
        if not ok:
            print(
                f"[experiment] Run {run_i}/{n_runs}: starting condition not met — SKIPPED"
            )
            print(f"             Reason: {reason}")
            run_results.append("skipped")
            run_details.append({"outcome": "skipped", "skip_reason": reason})
            continue

        print(f"\n[experiment] === Run {run_i}/{n_runs} ===")
        claude.reset_usage()
        t0 = time.time()
        passed, step_details = run_scenario(
            scenario_path,
            devices,
            use_ocr=use_ocr,
            use_screenshots=use_screenshots,
            use_learning=use_learning,
            action_mode=action_mode,
        )
        duration = round(time.time() - t0, 1)
        usage = claude.get_usage()
        outcome = "pass" if passed else "fail"
        run_results.append(outcome)
        run_details.append(
            {
                "outcome": outcome,
                "duration_s": duration,
                "api_calls": usage["api_calls"],
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "cost_usd": usage["cost_usd"],
                "step_details": step_details,
            }
        )
        print(
            f"[experiment] Run {run_i}/{n_runs}: {outcome.upper()}  "
            f"({duration}s, {usage['api_calls']} calls, ${usage['cost_usd']:.5f})"
        )

    bool_results = [r == "pass" if r != "skipped" else None for r in run_results]
    stats = _stats(bool_results)

    valid_details = [d for d in run_details if d["outcome"] != "skipped"]
    total_duration = (
        round(sum(d["duration_s"] for d in valid_details), 1) if valid_details else None
    )
    mean_duration = (
        round(total_duration / len(valid_details), 1) if valid_details else None
    )
    total_cost = (
        round(sum(d["cost_usd"] for d in valid_details), 6) if valid_details else None
    )

    result = {
        "experiment": "reliability",
        "scenario": scenario_path.stem,
        "dut_id": dut_id,
        "devices": list(devices.keys()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_runs": n_runs,
        "reset_delay_s": reset_delay,
        "options": {
            "use_ocr": use_ocr,
            "use_screenshots": use_screenshots,
            "use_learning": use_learning,
            "action_mode": action_mode,
        },
        "run_results": run_results,
        **stats,
        "n_skipped": run_results.count("skipped"),
        "total_duration_s": total_duration,
        "mean_duration_s": mean_duration,
        "total_cost_usd": total_cost,
        "run_details": run_details,
    }

    _print_reliability_summary(result)

    if save:
        _save(result, f"reliability_{scenario_path.stem}_{dut_id}")

    return result


def _print_reliability_summary(r: dict) -> None:
    print("\n" + "─" * 50)
    print(f"  Reliability: {r['scenario']}  DUT: {r['dut_id']}")
    print(f"  Runs: {r['n_runs']}   Valid: {r['n_valid']}   Skipped: {r['n_skipped']}")
    if r["pass_rate"] is not None:
        print(f"  Pass rate : {r['pass_rate']:.1%}   Std dev: {r['std_dev']:.3f}")
        if r["mean_duration_s"] is not None:
            print(
                f"  Mean time : {r['mean_duration_s']}s / run   Total: {r['total_duration_s']}s"
            )
        if r["total_cost_usd"] is not None:
            print(f"  Total cost: ${r['total_cost_usd']:.5f} USD")
    else:
        print("  No valid runs — all skipped.")
    print("  Per-run  : " + "  ".join(r["run_results"]))
    print("─" * 50)


# ── Authoring experiment ──────────────────────────────────────────────────────


def _reset_dut(dut_id: str) -> None:
    """Clear contract implementations and learned knowledge for *dut_id*."""
    import json as _json
    import yaml as _yaml

    cfg_path = Path(f"auto_tester/devices/saved_configs/{dut_id}.json")
    if cfg_path.exists():
        cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["behaviors"] = {}
        cfg["states"] = {}
        cfg_path.write_text(_json.dumps(cfg, indent=2), encoding="utf-8")

    contracts_dir = Path("contracts")
    for yf in contracts_dir.glob("*.yaml"):
        defn = _yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
        impls = defn.get("implementations") or {}
        if dut_id in impls:
            impls.pop(dut_id)
            defn["implementations"] = impls
            yf.write_text(
                _yaml.dump(defn, default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )


def run_authoring(
    scenario_path: Path,
    devices: dict[str, BaseDevice],
    n_runs: int = 5,
    reset_delay: float = RESET_DELAY_DEFAULT,
    use_ocr: bool = True,
    use_screenshots: bool = False,
    action_mode: str = "second_opinion",
    reset_between_runs: bool = True,
    save: bool = True,
) -> dict:
    """Run *n_runs* independent authoring attempts for the DUT.

    By default, before each run the DUT's contract implementations and learned
    device knowledge are cleared, so every run starts from a clean authoring
    state. Set *reset_between_runs=False* to preserve learned context across
    runs (useful when preparing reliability runs from authoring output).
    Secondary devices retain their implementations (they dispatch deterministically).

    Measures: authoring success rate, API calls per run, cost per run.
    """
    dut_id = list(devices.keys())[0]
    print(f"\n[experiment] Authoring — {scenario_path.name} × {n_runs} runs")
    print(f"[experiment] DUT: {dut_id}   reset delay: {reset_delay}s\n")

    run_results: list[str] = []
    run_details: list[dict] = []

    for run_i in range(1, n_runs + 1):
        if reset_between_runs:
            print(
                f"\n[experiment] Resetting '{dut_id}' contracts and device knowledge..."
            )
            _reset_dut(dut_id)
        elif run_i == 1:
            print(
                f"\n[experiment] Preserving existing '{dut_id}' learned context and contracts"
            )

        if run_i > 1:
            print(
                f"[experiment] Waiting {reset_delay}s for phones to return to idle..."
            )
            time.sleep(reset_delay)

        ok, reason = _check_starting_state(
            scenario_path, devices, use_ocr, use_screenshots
        )
        if not ok:
            print(
                f"[experiment] Run {run_i}/{n_runs}: starting condition not met — SKIPPED"
            )
            run_results.append("skipped")
            run_details.append({"outcome": "skipped", "skip_reason": reason})
            continue

        print(f"\n[experiment] === Authoring run {run_i}/{n_runs} ===")
        claude.reset_usage()
        t0 = time.time()
        passed, step_details = run_scenario(
            scenario_path,
            devices,
            use_ocr=use_ocr,
            use_screenshots=use_screenshots,
            use_learning=True,
            action_mode=action_mode,
        )
        duration = round(time.time() - t0, 1)
        usage = claude.get_usage()
        outcome = "pass" if passed else "fail"
        run_results.append(outcome)

        contracts_authored = [
            s["step_name"]
            for s in step_details
            if s.get("step_type") == "contract_authoring"
        ]

        run_details.append(
            {
                "outcome": outcome,
                "duration_s": duration,
                "api_calls": usage["api_calls"],
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "cost_usd": usage["cost_usd"],
                "contracts_authored": contracts_authored,
                "step_details": step_details,
            }
        )
        print(
            f"[experiment] Run {run_i}/{n_runs}: {outcome.upper()}  "
            f"({duration}s, {usage['api_calls']} calls, ${usage['cost_usd']:.5f})"
        )
        if contracts_authored:
            authored_lines = []
            for s in step_details:
                if s.get("step_type") == "contract_authoring":
                    keys = s.get("authored_keys") or []
                    keys_str = " → ".join(keys) if keys else "?"
                    authored_lines.append(f"{s['step_name']}({keys_str})")
            print(f"             Authored: {', '.join(authored_lines)}")

    bool_results = [r == "pass" if r != "skipped" else None for r in run_results]
    stats = _stats(bool_results)

    valid_details = [d for d in run_details if d["outcome"] != "skipped"]
    mean_duration = (
        round(sum(d["duration_s"] for d in valid_details) / len(valid_details), 1)
        if valid_details
        else None
    )
    total_cost = (
        round(sum(d["cost_usd"] for d in valid_details), 6) if valid_details else None
    )
    mean_api = (
        round(sum(d["api_calls"] for d in valid_details) / len(valid_details), 1)
        if valid_details
        else None
    )

    result = {
        "experiment": "authoring",
        "scenario": scenario_path.stem,
        "dut_id": dut_id,
        "devices": list(devices.keys()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_runs": n_runs,
        "reset_delay_s": reset_delay,
        "options": {
            "use_ocr": use_ocr,
            "use_screenshots": use_screenshots,
            "action_mode": action_mode,
            "reset_between_runs": reset_between_runs,
        },
        "run_results": run_results,
        **stats,
        "n_skipped": run_results.count("skipped"),
        "mean_duration_s": mean_duration,
        "mean_api_calls": mean_api,
        "total_cost_usd": total_cost,
        "run_details": run_details,
    }

    _print_authoring_summary(result)

    if save:
        _save(result, f"authoring_{scenario_path.stem}_{dut_id}")

    return result


def _print_authoring_summary(r: dict) -> None:
    print("\n" + "─" * 50)
    print(f"  Authoring: {r['scenario']}  DUT: {r['dut_id']}")
    print(f"  Runs: {r['n_runs']}   Valid: {r['n_valid']}   Skipped: {r['n_skipped']}")
    if r["pass_rate"] is not None:
        print(f"  Pass rate : {r['pass_rate']:.1%}   Std dev: {r['std_dev']:.3f}")
        print(f"  Mean time : {r['mean_duration_s']}s / run")
        print(f"  Mean API  : {r['mean_api_calls']} calls / run")
        if r["total_cost_usd"] is not None:
            print(f"  Total cost: ${r['total_cost_usd']:.5f} USD")
    else:
        print("  No valid runs — all skipped.")
    print("  Per-run  : " + "  ".join(r["run_results"]))
    print("─" * 50)


# ── Generalization experiment ─────────────────────────────────────────────────


def run_generalization(
    scenario_path: Path,
    device_groups: list[dict[str, BaseDevice]],
    n_runs: int = 5,
    reset_delay: float = RESET_DELAY_DEFAULT,
    use_ocr: bool = True,
    use_screenshots: bool = False,
    use_learning: bool = True,
    action_mode: str = "second_opinion",
    save: bool = True,
) -> dict:
    """Run the reliability experiment for each group of devices.

    Each group represents one DUT model.  Groups share the same ordered role
    mapping (Phone1 = DUT, Phone2 = secondary caller, …).

    Returns a result dict with per-model breakdowns.
    """
    print(f"\n[experiment] Generalization — {scenario_path.name}")
    print(f"[experiment] {len(device_groups)} model(s) × {n_runs} runs each\n")

    per_model: dict[str, dict] = {}

    for group in device_groups:
        dut_id = list(group.keys())[0]
        print(f"\n{'═' * 50}")
        print(f"  Model: {dut_id}")
        print(f"{'═' * 50}")
        model_result = run_reliability(
            scenario_path,
            group,
            n_runs=n_runs,
            reset_delay=reset_delay,
            use_ocr=use_ocr,
            use_screenshots=use_screenshots,
            use_learning=use_learning,
            action_mode=action_mode,
            save=False,
        )
        per_model[dut_id] = {
            "devices": model_result["devices"],
            "run_results": model_result["run_results"],
            "pass_rate": model_result["pass_rate"],
            "std_dev": model_result["std_dev"],
            "n_valid": model_result["n_valid"],
            "n_skipped": model_result["n_skipped"],
            "mean_duration_s": model_result["mean_duration_s"],
            "total_cost_usd": model_result["total_cost_usd"],
            "run_details": model_result["run_details"],
        }

    result = {
        "experiment": "generalization",
        "scenario": scenario_path.stem,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_runs_per_model": n_runs,
        "reset_delay_s": reset_delay,
        "options": {
            "use_ocr": use_ocr,
            "use_screenshots": use_screenshots,
            "use_learning": use_learning,
            "action_mode": action_mode,
        },
        "models": per_model,
    }

    _print_generalization_summary(result)

    if save:
        _save(result, f"generalization_{scenario_path.stem}")

    return result


def _print_generalization_summary(r: dict) -> None:
    print("\n" + "═" * 60)
    print(f"  Generalization: {r['scenario']}")
    print(f"  {'Model':<20} {'Pass rate':>10} {'Std dev':>10} {'Valid':>6} {'Skip':>5}")
    print("  " + "─" * 56)
    for model_id, m in r["models"].items():
        rate = f"{m['pass_rate']:.1%}" if m["pass_rate"] is not None else "n/a"
        std = f"{m['std_dev']:.3f}" if m["std_dev"] is not None else "n/a"
        print(
            f"  {model_id:<20} {rate:>10} {std:>10} {m['n_valid']:>6} {m['n_skipped']:>5}"
        )
    print("═" * 60)
