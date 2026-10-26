"""Scenario runner for automated VoIP phone regression testing."""

from __future__ import annotations

import json
import shlex
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from auto_tester.devices.base import BaseDevice
from auto_tester.devices.device_configs import load_config
import auto_tester.tests.claude as claude
from auto_tester.tests.claude import (
    analyze_and_update_behaviors,
    ask_next_step,
    verify_state,
)
from auto_tester.tests.easyocr_test import describe_screen, read_screen

ARTIFACTS_DIR = Path("artifacts")
RUNS_DIR = ARTIFACTS_DIR / "runs"
FAILURES_DIR = ARTIFACTS_DIR / "failures"
# Fixed wait after keypress before reading the next screen.
# Kept small to avoid UI menu timeouts during LLM reasoning.
STEP_DELAY = 0.2
MAX_ACTIONS_PER_STEP = 6
MAX_REPAIR_ACTIONS = 10


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class State:
    """An expected phone state: an optional named key + a human description."""

    name: str | None
    description: str | None


@dataclass
class Step:
    """One entry in a parsed scenario.

    kind="action"      — LLM-driven key selection; value = instruction text.
    kind="contract" — deterministic replay or authoring; value = contract name;
                         args = positional arguments from the scenario file.
    """

    kind: str  # "action" or "contract"
    value: str  # instruction text (action) or contract name (contract)
    args: list[str] = field(default_factory=list)  # contract args (empty for action)
    postcondition: State | None = None


# ── Scenario file parser ──────────────────────────────────────────────────────


# Tags whose remainder is content (description on following lines)
_CONTENT_TAGS = ("action", "state", "precondition", "postcondition")
# Tags that are fully self-contained on one line (no following description)
_INLINE_TAGS = ("contract",)


def _parse_tag(line: str) -> tuple[str, str, list[str]] | None:
    """If *line* is a block tag, return (tag_keyword, name_or_None, extra_args).

    Returns None for plain content lines.
    Tags have the form  [keyword ...]  where keyword is one of the known tags.
    """
    s = line.strip()
    if not (s.startswith("[") and "]" in s):
        return None
    bracket_end = s.index("]")
    inner = s[1:bracket_end]
    try:
        tokens = shlex.split(inner)
    except ValueError:
        tokens = inner.split()
    if not tokens:
        return None
    keyword = tokens[0].lower()
    if keyword not in _CONTENT_TAGS and keyword not in _INLINE_TAGS:
        return None
    name = tokens[1] if len(tokens) > 1 else None
    extra = tokens[2:] if len(tokens) > 2 else []
    return keyword, name, extra


def parse_scenario(path: Path) -> tuple[State | None, list[Step]]:
    """Parse a scenario .txt file into a starting state and an ordered list of Steps.

    Supported block types
    ---------------------
    [action]            LLM-driven key selection.  Description follows on the next lines.
    [contract name]  Deterministic contract, args are quoted strings or phone aliases.
    [state name]        State verification.  Description follows on the next lines.
    [precondition name] Alias for [state] at the top of the file.
    [postcondition name] Alias for [state] at the bottom / as a step postcondition.
    """
    # blocks: (tag_keyword, name | None, extra_args, accumulated_content)
    blocks: list[tuple[str, str | None, list[str], str]] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_tag(line)
        if parsed is not None:
            keyword, name, extra = parsed
            blocks.append((keyword, name, extra, ""))
        else:
            s = line.strip()
            if not s:
                continue
            if not blocks:
                # Bare content at top of file — treat as an action
                blocks.append(("action", None, [], ""))
            tag, name, extra, content = blocks[-1]
            blocks[-1] = (tag, name, extra, (content + "\n" + s).strip())

    starting_state: State | None = None
    steps: list[Step] = []
    pending: tuple[str, str, list[str]] | None = None  # (kind, value, args)

    for tag, name, extra, content in blocks:
        if tag in ("state", "precondition", "postcondition"):
            state = State(name=name, description=content or None)
            if pending is None:
                starting_state = state
            else:
                kind, value, pargs = pending
                steps.append(Step(kind, value, pargs, state))
                pending = None
        elif tag == "action" and content:
            if pending:
                kind, value, pargs = pending
                steps.append(Step(kind, value, pargs, None))
            pending = ("action", content, [])
        elif tag == "contract" and name:
            if pending:
                kind, value, pargs = pending
                steps.append(Step(kind, value, pargs, None))
            # Flush contract immediately — it has no following content
            steps.append(Step("contract", name, extra, None))
            pending = None

    if pending:
        kind, value, pargs = pending
        steps.append(Step(kind, value, pargs, None))

    return starting_state, steps


# ── Scenario runner ───────────────────────────────────────────────────────────


class ScenarioRun:
    """Holds all state for a single scenario execution.

    Shared context (devices, configs, options) lives as attributes so that
    helper methods need only their specific parameters — no long parameter lists.
    """

    def __init__(
        self,
        scenario_path: Path,
        devices: dict[str, BaseDevice],
        use_ocr: bool,
        use_screenshots: bool,
        use_learning: bool = True,
        action_mode: str = "second_opinion",
    ) -> None:
        self.scenario_path = scenario_path
        self.devices = devices
        self.dut_id = list(devices.keys())[0]
        self.use_ocr = use_ocr
        self.use_screenshots = use_screenshots
        self.use_learning = use_learning
        self.action_mode = action_mode

        device_ids = list(devices.keys())
        self.alias_map = {f"Phone{i + 1}": did for i, did in enumerate(device_ids)}

        self.configs: dict[str, dict] = {}
        for did in device_ids:
            try:
                self.configs[did] = load_config(did)
            except FileNotFoundError:
                self.configs[did] = {
                    "device_id": did,
                    "vendor": "Unknown",
                    "capabilities": {},
                    "behaviors": {},
                }

        self.rec_dir = RUNS_DIR / f"{scenario_path.stem}_{self.dut_id}"
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        self.log: list[str] = [
            "test devices",
            ",".join(device_ids),
            "scenario",
            str(scenario_path),
            "action sequence",
        ]
        self.step_count = 0
        self.step_details: list[dict] = []
        self._last_contract_mode: str = "dispatch"
        self._last_authored_keys: list[str] = []

    # ── Screen helpers ────────────────────────────────────────────────────

    def _capture(self) -> dict[str, str]:
        """Fetch fresh screenshots from all devices."""
        return {
            did: str(device.capture_screen(str(ARTIFACTS_DIR / "live")))
            for did, device in self.devices.items()
        }

    def _ocr(self, screens: dict[str, str]) -> dict[str, str] | None:
        """Run OCR on the given screenshot paths. Returns None if OCR is disabled."""
        if not self.use_ocr:
            return None
        return {did: describe_screen(path) for did, path in screens.items()}

    def _log_dut_screenshot(self) -> str:
        """Capture DUT screenshot, save to rec_dir, append screenshot log entry.

        Returns the path of the captured screenshot.
        """
        self.step_count += 1
        img_path = str(
            self.devices[self.dut_id].capture_screen(str(ARTIFACTS_DIR / "live"))
        )
        img_name = f"step_{self.step_count:03d}_{self.dut_id}.png"
        shutil.copy2(img_path, self.rec_dir / img_name)
        self.log.append(f"{self.dut_id},screenshot,{img_name}")
        return img_path

    # ── State verification ────────────────────────────────────────────────

    def verify(self, state: State) -> tuple[bool, str]:
        """Capture the DUT screen and ask Claude whether it matches *state*."""
        screens = self._capture()
        dut_screen = {self.dut_id: screens[self.dut_id]}
        return verify_state(
            state.description or "",
            dut_screen,
            {self.dut_id: self.configs[self.dut_id]},
            self.alias_map,
            ocr_texts=self._ocr(dut_screen),
            include_screenshots=self.use_screenshots,
            state_name=state.name,
        )

    # ──  action loop ───────────────────────────────────────────────

    def _act_until(
        self,
        instruction: str,
        device_id: str,
        postcondition_desc: str | None,
        max_steps: int = MAX_ACTIONS_PER_STEP,
    ) -> tuple[bool, list[str]]:
        """Ask Claude for keypresses until *postcondition_desc* is met.

        Returns (success, keys_pressed).
        - If postcondition_desc is None, stops after the first keypress.
        - Keys are pressed on *device_id* only.
        """
        alias_slice = {k: v for k, v in self.alias_map.items() if v == device_id}
        device = self.devices[device_id]
        alias = next(
            (a for a, r in self.alias_map.items() if r == device_id), device_id
        )

        keys_pressed: list[str] = []
        history: list[str] = []

        for _ in range(max_steps):
            screens = self._capture()
            dev_screen = {device_id: screens[device_id]}
            ocr = self._ocr(dev_screen)

            resp = ask_next_step(
                instruction=instruction,
                configs={device_id: self.configs[device_id]},
                screenshot_paths=dev_screen,
                alias_map=alias_slice,
                step_history=history or None,
                ocr_texts=ocr,
                include_screenshots=self.use_screenshots,
                action_mode=self.action_mode,
            )
            key = resp.get("arg", "")
            label = resp.get("label", "")
            if not key:
                break

            label_note = f" ({label})" if label else ""
            print(f"[scenario]   {alias} KEYPRESS {key}{label_note}")
            device.perform_action("KEYPRESS", key)
            keys_pressed.append(key)
            time.sleep(STEP_DELAY)

            if postcondition_desc is None:
                history.append(f"KEYPRESS {key} — done")
                return True, keys_pressed

            post_screen = {device_id: self._capture()[device_id]}
            post_ocr = self._ocr(post_screen)
            passed, _ = verify_state(
                postcondition_desc,
                post_screen,
                {device_id: self.configs[device_id]},
                self.alias_map,
                ocr_texts=post_ocr,
                include_screenshots=self.use_screenshots,
            )

            # Build a screen summary for the history entry — only when OCR is available.
            # With vision-only mode post_ocr is None; omit the screen note rather than
            # producing a misleading "screen unknown" that causes Claude to avoid keys
            # that are valid on the new screen.
            _screen_note = ""
            if post_ocr is not None:
                try:
                    _labels = json.loads(post_ocr.get(device_id, "{}")).get(
                        "labels", []
                    )
                    _sk = sorted(
                        [(lb["x"], lb["text"]) for lb in _labels if lb["y"] >= 0.85],
                        key=lambda t: t[0],
                    )
                    if _sk:
                        _screen_note = " → softkeys: " + ", ".join(
                            f"SK{i+1}={t}" for i, (_, t) in enumerate(_sk)
                        )
                except Exception:
                    pass

            if passed:
                history.append(
                    f"KEYPRESS {key}{label_note}{_screen_note} — postcondition met"
                )
                return True, keys_pressed

            history.append(
                f"KEYPRESS {key}{label_note}{_screen_note} — postcondition not yet met"
            )

        return False, keys_pressed

    # ── Step execution ────────────────────────────────────────────────────

    def execute_step(
        self,
        instruction: str,
        postcondition: State | None,
    ) -> bool:
        """Execute one [action] step. Returns True on success."""
        postcondition_desc = postcondition.description if postcondition else None
        success, keys_pressed = self._act_until(
            instruction, self.dut_id, postcondition_desc
        )
        if success:
            for key in keys_pressed:
                self.log.append(f"{self.dut_id},action,KEYPRESS,{key}")
            self._log_dut_screenshot()
            if postcondition:
                print("[scenario]   postcondition met")
        else:
            print("[scenario]   step failed")
        return success

    # ── Contract replay ────────────────────────────────────────────────

    def replay_contract(self, name: str, args: list[str]) -> bool:
        """Dispatch or author a contract.

        Dispatch: YAML implementation exists → resolve placeholders → play keys.
        Authoring: no implementation → _act_until → save discovered keys to YAML.
        """
        import auto_tester.tests.contract as sub

        phone_arg = next((a for a in args if a in self.alias_map), None)
        semantic_args = [a for a in args if a not in self.alias_map]
        device_id = self.alias_map.get(phone_arg, self.dut_id)
        device = self.devices.get(device_id)
        if device is None:
            print(f"[scenario] Unknown device '{device_id}' for contract '{name}'")
            return False

        alias = next(
            (a for a, r in self.alias_map.items() if r == device_id), device_id
        )

        defn: dict | None = None
        try:
            defn = sub.load(name)
        except FileNotFoundError:
            pass

        def _resolve_dispatch_params(
            placeholder_names: list[str],
        ) -> dict[str, str] | None:
            if not placeholder_names:
                return {}

            screens = self._capture()
            dev_screen = {device_id: screens[device_id]}
            dev_ocr = self._ocr(dev_screen)
            resolved = claude.resolve_contract_placeholders(
                contract_name=name,
                placeholders=placeholder_names,
                semantic_args=semantic_args,
                configs={device_id: self.configs[device_id]},
                screenshot_paths=dev_screen,
                alias_map={k: v for k, v in self.alias_map.items() if v == device_id},
                ocr_texts=dev_ocr,
                include_screenshots=self.use_screenshots,
            )

            missing = [p for p in placeholder_names if p not in resolved]
            if missing:
                print(
                    f"[scenario] Could not resolve placeholder(s) {missing} "
                    f"for contract '{name}' on '{device_id}'"
                )
                return None
            return resolved

        if defn is not None and sub.has_impl(defn, device_id):
            # ── DISPATCH MODE ─────────────────────────────────────────────
            self._last_contract_mode = "dispatch"
            placeholder_names = sub.placeholders_for(defn, device_id)
            resolved_result = _resolve_dispatch_params(placeholder_names)
            if resolved_result is None:
                return False
            resolved = resolved_result

            try:
                keys = sub.steps_for(defn, device_id, resolved)
            except ValueError as exc:
                print(f"[scenario] {exc}")
                return False

            args_note = f" {semantic_args}" if semantic_args else ""
            resolved_note = f" → {resolved}" if resolved else ""
            print(
                f"[scenario] Replaying '{name}'{args_note}{resolved_note}"
                f" ({len(keys)} step(s) on {alias})"
            )
            for key in keys:
                device.perform_action("KEYPRESS", key)
                self.log.append(f"{device_id},action,KEYPRESS,{key}")
                self._log_dut_screenshot()
                time.sleep(STEP_DELAY)

        else:
            # ── AUTHORING MODE ────────────────────────────────────────────
            self._last_contract_mode = "authoring"
            description = (
                defn.get("description") if defn else None
            ) or f"Perform '{name}'"
            param_names = (defn or {}).get("parameters", [])
            param_map = {
                param_names[i]: semantic_args[i]
                for i in range(min(len(param_names), len(semantic_args)))
            }
            try:
                description = (
                    description.format(**param_map) if param_map else description
                )
            except KeyError:
                pass
            if semantic_args:
                description += f" Target: {', '.join(repr(a) for a in semantic_args)}."

            postcond = (defn or {}).get("postcondition", {}) or {}
            postcond_raw = postcond.get("description") or postcond.get("state") or None
            try:
                postcond_desc = (
                    postcond_raw.format(**param_map)
                    if postcond_raw and param_map
                    else postcond_raw
                )
            except KeyError:
                postcond_desc = postcond_raw

            print(
                f"[scenario] No implementation for '{name}' on '{device_id}'"
                f" — authoring..."
            )

            alias_slice = {k: v for k, v in self.alias_map.items() if v == device_id}
            success, discovered = self._act_until(description, device_id, postcond_desc)

            if not success or not discovered:
                print(f"[scenario] Authoring failed for '{name}' on '{device_id}'")
                return False

            sub.save_impl(name, device_id, discovered, semantic_args)
            self._last_authored_keys = list(discovered)

            # Log each authored keypress + screenshot for behavior analysis
            for key in discovered:
                self.log.append(f"{device_id},action,KEYPRESS,{key}")
                self._log_dut_screenshot()

        time.sleep(STEP_DELAY)
        args_csv = "," + ",".join(args) if args else ""
        self.log.append(f"contract,{name}{args_csv}")
        self._log_dut_screenshot()
        return True

    # ── Main run loop ─────────────────────────────────────────────────────

    def run(self, starting_state: State | None, steps: list[Step]) -> bool:
        mode = (
            "+".join(
                filter(
                    None,
                    [
                        "OCR" if self.use_ocr else "",
                        "Vision" if self.use_screenshots else "",
                    ],
                )
            )
            or "None"
        )
        print(
            f"\n[scenario] {self.scenario_path.name} — {len(steps)} step(s) [{mode} mode]"
        )
        print(
            f"[scenario] Devices: {', '.join(f'{a}={r}' for a, r in self.alias_map.items())}\n"
        )

        if starting_state is not None:
            passed, reason = self.verify(starting_state)
            if not passed:
                print(f"[scenario] Starting condition not met: {reason}")
                return False
            print("[scenario] Starting condition OK")

        current_state: State | None = starting_state

        for i, step in enumerate(steps, 1):
            if step.kind == "contract":
                args_note = (
                    " " + " ".join(repr(a) for a in step.args) if step.args else ""
                )
                label = f"[contract {step.value}{args_note}]"
            else:
                label = step.value[:80]
            print(f"[scenario] Step {i}/{len(steps)}: {label}")

            from auto_tester.tests import claude as _claude

            _usage_before = _claude.get_usage()

            if step.kind == "contract":
                ok = self.replay_contract(step.value, step.args)
                step_type = f"contract_{self._last_contract_mode}"
                if ok and step.postcondition is not None:
                    passed, reason = self.verify(step.postcondition)
                    if not passed:
                        print(f"[scenario] Step {i} postcondition not met: {reason}")
                        ok = False
                    else:
                        print("[scenario]   postcondition met")
            else:  # "action"
                ok = self.execute_step(
                    step.value,
                    step.postcondition,
                )
                step_type = "action"

            _usage_after = _claude.get_usage()
            detail: dict = {
                "step_index": i,
                "step_type": step_type,
                "step_name": step.value,
                "api_calls": _usage_after["api_calls"] - _usage_before["api_calls"],
                "input_tokens": _usage_after["input_tokens"]
                - _usage_before["input_tokens"],
                "output_tokens": _usage_after["output_tokens"]
                - _usage_before["output_tokens"],
                "passed": ok,
            }
            if step_type == "contract_authoring":
                detail["authored_keys"] = self._last_authored_keys
            self.step_details.append(detail)

            if not ok:
                print(f"[scenario] Step {i} failed — aborting")
                self._save_failure(step_num=i, step_label=label)
                return False

            if step.postcondition is not None:
                current_state = step.postcondition

        self._save_recording(steps)
        return True

    # ── Post-run: save & analyze ──────────────────────────────────────────

    def _save_recording(self, steps: list[Step]) -> None:
        seq_path = self.rec_dir / "sequence.txt"
        seq_path.write_text("\n".join(self.log), encoding="utf-8")
        print(f"\n[scenario] Recording saved: {seq_path}")

        if not self.use_learning:
            return

        action_screenshots = self._build_action_screenshots()
        if not action_screenshots:
            return

        print("[scenario] Running behavior analysis...")
        scenario_desc_lines = []
        for i, s in enumerate(steps, 1):
            if s.kind == "contract":
                state_hint = (
                    f" (device ends in '{s.postcondition.name}' state)"
                    if s.postcondition and s.postcondition.name
                    else ""
                )
                args_note = " " + " ".join(repr(a) for a in s.args) if s.args else ""
                scenario_desc_lines.append(
                    f"{i}. [contract: {s.value}{args_note}{state_hint}]"
                )
            else:
                scenario_desc_lines.append(f"{i}. {s.value}")

        analyze_and_update_behaviors(
            scenario="\n".join(scenario_desc_lines),
            action_screenshots=action_screenshots,
            configs=self.configs,
            alias_map=self.alias_map,
            use_ocr=self.use_ocr,
            use_screenshots=self.use_screenshots,
        )

    def _save_failure(self, step_num: int, step_label: str) -> None:
        """Capture DUT screenshot and save it to the failures directory."""
        FAILURES_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in step_label[:40])
        img_name = (
            f"{self.scenario_path.stem}_{self.dut_id}_step{step_num:02d}_{safe}.png"
        )
        try:
            screens = self._capture()
            dut_path = screens[self.dut_id]
            shutil.copy2(dut_path, FAILURES_DIR / img_name)
            print(f"[scenario] Failure screenshot: {FAILURES_DIR / img_name}")
        except Exception as exc:
            print(f"[scenario] Could not save failure screenshot: {exc}")

    def _build_action_screenshots(self) -> list[dict]:
        """Reconstruct action+screenshot pairs from the log for behavior analysis."""
        result: list[dict] = []
        pending_actions: list[dict] = []
        for entry in self.log:
            parts = entry.split(",")
            if len(parts) >= 3 and parts[1] == "action":
                pending_actions.append(
                    {
                        "device_id": parts[0],
                        "action": parts[2],
                        "arg": parts[3] if len(parts) > 3 else "",
                    }
                )
            elif len(parts) == 3 and parts[1] == "screenshot" and pending_actions:
                img_path = str(self.rec_dir / parts[2])
                for act in pending_actions:
                    act["screenshot"] = img_path
                    if self.use_ocr:
                        ocr = self._ocr({act["device_id"]: img_path})
                        act["ocr_text"] = (ocr or {}).get(
                            act["device_id"], "(no text detected)"
                        )
                result.extend(pending_actions)
                pending_actions = []
        return result

    # ── Replay helpers ────────────────────────────────────────────────────

    def _dispatch_contract(self, name: str, args: list[str]) -> bool:
        """Execute a contract using its saved implementation. No logging or screenshots.

        Returns False if no implementation exists or required contacts cannot be resolved.
        """
        import auto_tester.tests.contract as sub

        phone_arg = next((a for a in args if a in self.alias_map), None)
        semantic_args = [a for a in args if a not in self.alias_map]
        device_id = self.alias_map.get(phone_arg, self.dut_id)
        device = self.devices.get(device_id)
        if device is None:
            print(f"[replay] Unknown device '{device_id}' for contract '{name}'")
            return False
        alias = next(
            (a for a, r in self.alias_map.items() if r == device_id), device_id
        )

        try:
            defn = sub.load(name)
        except FileNotFoundError:
            print(f"[replay] No definition for contract '{name}'")
            return False

        if not sub.has_impl(defn, device_id):
            print(f"[replay] No implementation for '{name}' on '{device_id}'")
            return False

        placeholder_names = sub.placeholders_for(defn, device_id)
        screens = self._capture()
        dev_screen = {device_id: screens[device_id]}
        dev_ocr = self._ocr(dev_screen)
        resolved = claude.resolve_contract_placeholders(
            contract_name=name,
            placeholders=placeholder_names,
            semantic_args=semantic_args,
            configs={device_id: self.configs[device_id]},
            screenshot_paths=dev_screen,
            alias_map={k: v for k, v in self.alias_map.items() if v == device_id},
            ocr_texts=dev_ocr,
            include_screenshots=self.use_screenshots,
        )
        missing = [p for p in placeholder_names if p not in resolved]
        if missing:
            print(
                f"[replay] Could not resolve placeholder(s) {missing} "
                f"for contract '{name}' on '{device_id}'"
            )
            return False

        try:
            keys = sub.steps_for(defn, device_id, resolved)
        except ValueError as exc:
            print(f"[replay] {exc}")
            return False

        resolved_note = f" → {resolved}" if resolved else ""
        print(
            f"[replay] Dispatching '{name}'{resolved_note} ({len(keys)} key(s) on {alias})"
        )
        for key in keys:
            device.perform_action("KEYPRESS", key)
            time.sleep(STEP_DELAY)
        return True

    def _repair_step(
        self,
        unit_step_lines: list[str],
        ref_path: Path,
        prev_state: State,
        next_state: State,
        scenario_step: "Step | None",
    ) -> bool:
        """Use the LLM to repair a failed recording step.

        1. Navigate the DUT back to *prev_state* (recovery).
        2. Re-execute the step toward *next_state* using _act_until.
        3. Update the reference screenshot and contract YAML in-place.
        """
        import auto_tester.tests.contract as sub

        print("[repair] SSIM mismatch — attempting LLM repair")

        def _resolve_desc(state: State) -> str:
            if state.description:
                return state.description
            if state.name:
                return (
                    self.configs.get(self.dut_id, {})
                    .get("states", {})
                    .get(state.name, state.name)
                )
            return ""

        prev_desc = _resolve_desc(prev_state)
        next_desc = _resolve_desc(next_state)

        # 1. Recover to prev_state unless already there
        already_there, _ = self.verify(prev_state)
        if not already_there:
            print(f"[repair] Recovering to: {prev_state.name or prev_desc[:60]}")
            ok, _ = self._act_until(
                prev_desc, self.dut_id, prev_desc, MAX_REPAIR_ACTIONS
            )
            if not ok:
                print("[repair] Could not recover to previous state — aborting")
                return False
            print("[repair] Previous state restored")
        else:
            print("[repair] Already at previous state — skipping recovery")

        # 2. Re-execute toward next_state
        first_line = unit_step_lines[0]
        if first_line.startswith("contract,"):
            parts = first_line.split(",")
            name = parts[1]
            args = parts[2:] if len(parts) > 2 else []
            phone_arg = next((a for a in args if a in self.alias_map), None)
            semantic_args = [a for a in args if a not in self.alias_map]
            device_id = self.alias_map.get(phone_arg, self.dut_id)

            try:
                defn = sub.load(name)
                description = defn.get("description") or f"Perform '{name}'"
            except FileNotFoundError:
                description = f"Perform '{name}'"

            if semantic_args:
                description += f" Target: {', '.join(repr(a) for a in semantic_args)}."

            print(
                f"[repair] Re-authoring '{name}' toward: {next_state.name or next_desc[:60]}"
            )
            ok, discovered = self._act_until(
                description, device_id, next_desc, MAX_REPAIR_ACTIONS
            )
            if not ok or not discovered:
                print(f"[repair] Failed to reach next state for contract '{name}'")
                return False
            sub.save_impl(name, device_id, discovered, semantic_args)
            print(
                f"[repair] Contract updated — '{name}' for '{device_id}': {discovered}"
            )
        else:
            instruction = (
                scenario_step.value
                if scenario_step and scenario_step.kind == "action"
                else f"Reach state: {next_desc}"
            )
            print(
                f"[repair] Re-executing action toward: {next_state.name or next_desc[:60]}"
            )
            ok, _ = self._act_until(
                instruction, self.dut_id, next_desc, MAX_REPAIR_ACTIONS
            )
            if not ok:
                print("[repair] Failed to reach next state for action step")
                return False

        # 3. Update reference screenshot in-place
        live_screens = self._capture()
        live_path = live_screens[self.dut_id]
        shutil.copy2(live_path, ref_path)
        print(f"[repair] Reference screenshot updated: {ref_path.name}")

        # 4. Update device config via behavior analysis
        action_screenshots = []
        for line in unit_step_lines:
            lparts = line.split(",")
            if len(lparts) >= 3 and lparts[1] == "action":
                entry: dict = {
                    "device_id": lparts[0],
                    "action": lparts[2],
                    "arg": lparts[3] if len(lparts) > 3 else "",
                    "screenshot": live_path,
                }
                if self.use_ocr:
                    ocr = self._ocr({lparts[0]: live_path})
                    entry["ocr_text"] = (ocr or {}).get(lparts[0], "(no text detected)")
                action_screenshots.append(entry)

        if action_screenshots and self.use_learning:
            analyze_and_update_behaviors(
                scenario=f"Repair: reach {next_state.name or 'next state'}",
                action_screenshots=action_screenshots,
                configs=self.configs,
                alias_map=self.alias_map,
                use_ocr=self.use_ocr,
                use_screenshots=self.use_screenshots,
            )

        return True

    def replay(self, rec_dir: Path) -> bool:
        """Replay a saved recording with SSIM validation after each step.

        On SSIM failure the LLM is invoked to navigate back to the previous
        state and re-execute the failing step, updating the reference screenshot
        and contract contract in-place.

        Only recordings written in the current format (with a 'scenario' header
        section) are supported.
        """
        from auto_tester.tests.ssim_validate import evaluate
        import auto_tester.tests.contract as sub

        seq_path = rec_dir / "sequence.txt"
        if not seq_path.exists():
            print(f"[replay] No sequence.txt found at {seq_path}")
            return False

        # ── Parse sequence.txt ────────────────────────────────────────────
        recorded_scenario: Path | None = None
        action_lines: list[str] = []
        section: str | None = None

        for raw in seq_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line == "test devices":
                section = "devices"
            elif line == "scenario":
                section = "scenario"
            elif line == "action sequence":
                section = "actions"
            elif section == "scenario":
                recorded_scenario = Path(line)
            elif section == "actions":
                action_lines.append(line)

        if recorded_scenario is None:
            print(
                "[replay] Recording is missing 'scenario' section — "
                "outdated format not supported"
            )
            return False

        # ── Group lines into step units ───────────────────────────────────
        # A unit = (step_lines, ref_screenshot_path | None)
        # step_lines = one contract line  OR  one/more action lines
        units: list[tuple[list[str], Path | None]] = []
        pending_lines: list[str] = []

        for line in action_lines:
            parts = line.split(",")
            if len(parts) >= 2 and parts[1] == "screenshot":
                ref = rec_dir / parts[2] if len(parts) >= 3 else None
                if pending_lines:
                    units.append((list(pending_lines), ref))
                    pending_lines = []
            elif line.startswith("contract,"):
                if pending_lines:
                    units.append((list(pending_lines), None))
                pending_lines = [line]
            else:
                pending_lines.append(line)

        if pending_lines:
            units.append((pending_lines, None))

        # ── Parse original scenario file ──────────────────────────────────
        scenario_steps: list[Step] = []
        starting_state: State | None = None
        if recorded_scenario.exists():
            starting_state, scenario_steps = parse_scenario(recorded_scenario)
        else:
            print(f"[replay] Warning: scenario file not found: {recorded_scenario}")

        # ── Helper: postcondition for a unit ─────────────────────────────
        def _unit_postcondition(idx: int, step_lines: list[str]) -> "State | None":
            if (
                idx < len(scenario_steps)
                and scenario_steps[idx].postcondition is not None
            ):
                return scenario_steps[idx].postcondition
            first = step_lines[0]
            if first.startswith("contract,"):
                name = first.split(",")[1]
                try:
                    defn = sub.load(name)
                    pc = defn.get("postcondition", {}) or {}
                    state_name = pc.get("state")
                    state_desc = pc.get("description")
                    if not state_desc and state_name:
                        state_desc = (
                            self.configs.get(self.dut_id, {})
                            .get("states", {})
                            .get(state_name)
                        )
                    if state_name or state_desc:
                        return State(name=state_name, description=state_desc)
                except FileNotFoundError:
                    pass
            return None

        # ── Replay loop ───────────────────────────────────────────────────
        mode_str = (
            "+".join(
                filter(
                    None,
                    [
                        "OCR" if self.use_ocr else "",
                        "Vision" if self.use_screenshots else "",
                    ],
                )
            )
            or "None"
        )
        print(f"\n[replay] {rec_dir.name} — {len(units)} step(s) [{mode_str} mode]\n")

        current_state: State | None = starting_state

        for i, (step_lines, ref_path) in enumerate(units):
            print(f"[replay] Step {i + 1}/{len(units)}: {step_lines[0][:80]}")

            # Execute step (dispatch only — no LLM authoring during normal replay)
            first = step_lines[0]
            if first.startswith("contract,"):
                parts = first.split(",")
                ok = self._dispatch_contract(
                    parts[1], parts[2:] if len(parts) > 2 else []
                )
            else:
                ok = True
                for line in step_lines:
                    lparts = line.split(",")
                    if len(lparts) >= 4 and lparts[1] == "action":
                        device = self.devices.get(lparts[0])
                        if device:
                            device.perform_action(lparts[2], lparts[3])
                            time.sleep(STEP_DELAY)
                        else:
                            print(f"[replay] Unknown device '{lparts[0]}'")
                            ok = False
                            break

            if not ok:
                print(f"[replay] Step {i + 1} execution failed — aborting")
                return False

            time.sleep(STEP_DELAY)

            # SSIM validation
            if ref_path and ref_path.exists():
                live_screens = self._capture()
                live_path = live_screens[self.dut_id]
                result = evaluate(str(ref_path), live_path)
                print(
                    f"[replay]   SSIM={result.score:.3f} "
                    f"({'PASS' if result.passed else 'FAIL'})"
                )

                if not result.passed:
                    next_state = _unit_postcondition(i, step_lines)
                    sc_step = scenario_steps[i] if i < len(scenario_steps) else None
                    if current_state is None or next_state is None:
                        print(
                            f"[replay] Cannot repair step {i + 1}: "
                            f"prev_state={current_state}, next_state={next_state}"
                        )
                        return False
                    ok = self._repair_step(
                        step_lines, ref_path, current_state, next_state, sc_step
                    )
                    if not ok:
                        print(f"[replay] Repair failed at step {i + 1} — aborting")
                        return False

            # Advance tracked state
            next_st = _unit_postcondition(i, step_lines)
            if next_st is not None:
                current_state = next_st

        print(f"\n[replay] All {len(units)} steps passed")
        return True


# ── Public API ────────────────────────────────────────────────────────────────


def run_scenario(
    scenario_path: Path,
    devices: dict[str, BaseDevice],
    use_ocr: bool = True,
    use_screenshots: bool = False,
    use_learning: bool = True,
    action_mode: str = "second_opinion",
) -> tuple[bool, list[dict]]:
    """Run a scenario against the given devices. Returns (success, step_details)."""
    runner = ScenarioRun(
        scenario_path,
        devices,
        use_ocr,
        use_screenshots,
        use_learning,
        action_mode,
    )
    starting_state, steps = parse_scenario(scenario_path)
    if not steps:
        print("[scenario] No steps found — aborting")
        return False, []
    ok = runner.run(starting_state, steps)
    return ok, runner.step_details
