"""Claude interface for VoIP phone test automation.

Public functions:
  ask_next_step                — decide the next key press during a scenario step
  verify_state                 — check if phones are in an expected state
  analyze_and_update_behaviors — learn new behaviors after a scenario run
    resolve_contract_placeholders — map contract placeholders to concrete keys
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading

import anthropic
from auto_tester.devices.device_configs import config_context, save_config

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
_MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Token / cost tracking
# ---------------------------------------------------------------------------

_COST_PER_INPUT_TOKEN = 0.25e-6  # USD per input token  (Haiku)
_COST_PER_OUTPUT_TOKEN = 1.25e-6  # USD per output token (Haiku)

_usage_lock = threading.Lock()
_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "api_calls": 0}


def reset_usage() -> None:
    """Reset accumulated token counters. Call before each experiment run."""
    with _usage_lock:
        _usage["input_tokens"] = 0
        _usage["output_tokens"] = 0
        _usage["api_calls"] = 0


def get_usage() -> dict:
    """Return a copy of the current accumulated usage and estimated USD cost."""
    with _usage_lock:
        inp = _usage["input_tokens"]
        out = _usage["output_tokens"]
        return {
            "input_tokens": inp,
            "output_tokens": out,
            "api_calls": _usage["api_calls"],
            "cost_usd": round(
                inp * _COST_PER_INPUT_TOKEN + out * _COST_PER_OUTPUT_TOKEN, 6
            ),
        }


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_ACTION_SYSTEM = """\
You are the action selector for a VoIP phone test.

You are given:
- A screen analysis produced by a prior reasoning stage.
- The current screen evidence and device context.

Your job:
1. Sanity-check whether the suggested key is reasonable for the visible screen/context.
2. If a clearly better key exists, choose that better key.
3. If you are unsure, follow the suggested key from the analysis.

Important: do not ask for retries and do not output no-op actions.
"""

_REASON_SYSTEM = """\
You analyze a VoIP phone screen to determine the best next action.
Given the current screen, device context, contract goal, and action history:
1. Describe what you see on screen: softkey labels and their left-to-right positions,
   visible contacts and their positions on the line key strip, and current call state.
2. Identify the single next key to press and why it advances the goal.
   Be precise: e.g. "The 3rd button from the left is labeled Transfer — that is SOFTKEY3."
   Count from the leftmost physical button, including any unlabeled slots.
Output plain text only.
"""

_ACTION_OCR_NOTE = """\
Softkey format: SOFTKEY1=<label>, ..., SOFTKEYn=<label>
  Numbers are physical slot positions: SOFTKEY1 = leftmost, SOFTKEYn = rightmost.
  Unlabeled slots show "(no label)". Only some slots may be labeled on a given screen.
  Do NOT renumber: two adjacent labeled keys may be SOFTKEY2 and SOFTKEY3, not 1 and 2.
Screen text: all other visible text on the display.
"""

_VERIFY_SYSTEM = """\
You verify the current state of VoIP phones.
- If a device state definition is provided, treat it as authoritative — do not override it.
- Accept the screen if the described key elements are present. Do not reject for elements not mentioned in the description.
- Reject only if a described element is clearly absent or contradicted by the screen.
- The visible softkeys are context keys — they represent the current state of the device.
  Use them as evidence of what state the phone is in when verifying any claim.
- Transient notification messages confirming a completed action are not interactive menus.
  A completion notice is evidence the action succeeded — do not treat it as a menu being present.
"""

_ANALYZE_SYSTEM = """\
Extract new device behaviors and state descriptions from a completed VoIP test scenario.

You receive interleaved steps and post-action DUT screenshots.

Key rule — assign behaviors to the state BEFORE the action, not the result:
  The screenshot after step N shows the state the action PRODUCED.
  The state the device was IN is shown by the PREVIOUS screenshot.

Steps marked [contract: X (device ends in 'Y' state)] are deterministic replays with no
associated screenshots. They bring the device to the noted end state. Use that end state
as the starting state for the first screenshot that follows.
If no contract precedes step 1, assume the device starts in the home/idle state.

Example: [contract: make_call (device ends in 'active_call' state)] → Phone1 KEYPRESS SOFTKEY1 → screenshot shows held
  → document: active_call: {SOFTKEY1: "Puts call on hold"}

Do not repeat anything already listed under "Existing behaviors" or "Existing states".
State descriptions must be generic — describe screen layout and visible elements, not the specific call participant. Use "the remote party" instead of a contact name.
For every new behavior state in your output, include a states entry for it too.

Output ONLY valid JSON:
{
  "device_id": {
    "behaviors": {"state_name": {"KEY": "description (max 10 words)"}},
    "states": {"state_name": "what the screen shows in this state (1-2 sentences)"}
  }
}
If nothing new, output: {}
"""

_RESOLVE_CONTRACT_PARAMS_SYSTEM = """\
You resolve dynamic contract placeholders for a VoIP phone test.

Inputs include:
- Contract name and target semantic arguments (for example, a contact name).
- The specific placeholder names that must be resolved.
- Current screen evidence (OCR text and/or screenshots).
- Device context, including known behaviors/states and configured key layout.

Rules:
1. Use screen evidence and learned device context.
2. Return one concrete physical key per placeholder.
3. Do not invent placeholders or keys not in the provided valid set.
4. If uncertain, choose the most likely key from the available evidence.
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _image_block(path: str) -> dict:
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
    }


def _annotate_ocr(
    raw_json: str,
    n_softkeys: int | None = None,
    n_left_linekeys: int | None = None,
    n_right_linekeys: int | None = None,
) -> str:
    """Convert raw OCR JSON to softkeys + screen text.

    Line key labels are NOT classified from OCR — they are unreliable across
    phone models (Poly side-panel labels bleed into the centre zone).  Accurate
    line key assignments are provided to Claude via config_context() instead.
    """
    import json as _json

    try:
        data = _json.loads(raw_json)
    except Exception:
        return raw_json

    labels = data.get("labels", [])

    softkey_labels = sorted(
        [(lb["x"], lb["text"]) for lb in labels if lb["y"] >= 0.85],
        key=lambda t: t[0],
    )
    if n_softkeys and n_softkeys > 0:
        sk_entries = [
            (max(1, min(n_softkeys, round(x * n_softkeys + 0.5))), text)
            for x, text in softkey_labels
        ]
        detected_positions = {pos for pos, _ in sk_entries}
        for i in range(1, n_softkeys + 1):
            if i not in detected_positions:
                sk_entries.append((i, "(no label)"))
        sk_entries.sort()
    else:
        sk_entries = [(i + 1, text) for i, (_, text) in enumerate(softkey_labels)]

    # Everything above the softkey strip is screen text — no edge-based line key
    # classification because it produces inaccurate results on Poly devices.
    other = [lb["text"] for lb in labels if lb["y"] < 0.85]

    parts: list[str] = []
    if sk_entries:
        sk = ", ".join(f"SOFTKEY{pos}={text}" for pos, text in sk_entries)
        parts.append(f"Softkeys: {sk}")
    if other:
        parts.append(f"Screen text: {', '.join(other)}")

    return "\n".join(parts) if parts else "(no labels detected)"


def _screen_blocks(
    screenshot_paths: dict[str, str],
    ocr_texts: dict[str, str] | None,
    reverse_map: dict[str, str],
    include_screenshots: bool = False,
    configs: dict[str, dict] | None = None,
) -> list[dict]:
    """Build content blocks for screen data (OCR text and/or images)."""
    blocks: list[dict] = []
    for did, path in screenshot_paths.items():
        name = reverse_map.get(did, did)
        if ocr_texts:
            raw = ocr_texts.get(did, '{"labels": []}')
            caps = (
                configs[did].get("capabilities", {})
                if configs and did in configs
                else {}
            )
            n_sk = caps.get("softkeys")
            n_left_lk = caps.get("left_linekeys")
            n_right_lk = caps.get("right_linekeys")
            blocks.append(
                {
                    "type": "text",
                    "text": f"{name} screen:\n{_annotate_ocr(raw, n_sk, n_left_lk, n_right_lk)}",
                }
            )
        if not ocr_texts or include_screenshots:
            blocks.append(_image_block(path))
    return blocks


def _call(system: str, content: list[dict], max_tokens: int, temperature: float) -> str:
    _log_request(system, content)
    response = _client.messages.create(
        model=_MODEL,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": content}],
    )
    with _usage_lock:
        _usage["input_tokens"] += response.usage.input_tokens
        _usage["output_tokens"] += response.usage.output_tokens
        _usage["api_calls"] += 1
    return " ".join(b.text for b in response.content if b.type == "text").strip()


def _valid_keys(configs: dict[str, dict]) -> list[str]:
    """Build sorted list of all valid key names across all device configs."""
    keys: set[str] = set()
    for cfg in configs.values():
        caps = cfg["capabilities"]
        total_line = caps["left_linekeys"] + caps["right_linekeys"]
        for i in range(1, total_line + 1):
            keys.add(f"LINE{i}")
        for i in range(1, caps["softkeys"] + 1):
            keys.add(f"SOFTKEY{i}")
    keys.update(["UP", "DOWN", "LEFT", "RIGHT", "OK", "CANCEL"])
    keys.update(list("0123456789") + ["*", "#"])

    def _sort_key(k: str) -> tuple[str, int]:
        for i, c in enumerate(k):
            if c.isdigit():
                return k[:i], int(k[i:])
        return k, 0

    return sorted(keys, key=_sort_key)


def _extract_reasoning_key(reasoning: str, valid_keys: list[str]) -> str | None:
    """Extract the recommended key from reasoning text.

    Priority:
    1) Explicit recommendation phrases (e.g. "Press LINE2").
    2) Key mentions after a "Next action" heading.
    3) Final key mention in the full reasoning (usually the recommendation).
    """
    if not reasoning:
        return None

    text = reasoning.upper()
    key_set = set(valid_keys)
    key_pattern = r"(SOFTKEY\d+|LINE\d+|UP|DOWN|LEFT|RIGHT|OK|CANCEL|[0-9#*])"

    # 1) Strong signal: explicit imperative recommendation.
    press_re = re.compile(rf"\bPRESS\s+(?:\*\*)?{key_pattern}(?:\*\*)?\b")
    for m in press_re.finditer(text):
        key = m.group(1)
        if key in key_set:
            return key

    # 2) Look specifically in the "Next action" section if present.
    next_action_pos = text.find("NEXT ACTION")
    if next_action_pos != -1:
        tail = text[next_action_pos:]
        tail_matches = [
            m.group(1)
            for m in re.finditer(rf"\b{key_pattern}\b", tail)
            if m.group(1) in key_set
        ]
        if tail_matches:
            return tail_matches[0]

    # 3) Fallback: choose the last valid key mention in the full reasoning.
    matches = [
        m.group(1)
        for m in re.finditer(rf"\b{key_pattern}\b", text)
        if m.group(1) in key_set
    ]
    return matches[-1] if matches else None


def _log_request(system: str, content: list[dict]) -> None:
    """Print the full text content sent to Claude."""
    print("\n[Claude prompt] system:")
    print(system)
    for block in content:
        if block.get("type") == "text":
            print("[Claude prompt] user text:")
            print(block["text"])
        elif block.get("type") == "image":
            print("[Claude prompt] <image attached>")


def _tool_call(
    system: str, tools: list[dict], content: list[dict], max_tokens: int = 256
) -> dict:
    """Call Claude with tool_choice=any, return the first tool_use input dict."""
    _log_request(system, content)
    response = _client.messages.create(
        model=_MODEL,
        system=system,
        tools=tools,
        tool_choice={"type": "any"},
        max_tokens=max_tokens,
        temperature=0.0,
        messages=[{"role": "user", "content": content}],
    )
    with _usage_lock:
        _usage["input_tokens"] += response.usage.input_tokens
        _usage["output_tokens"] += response.usage.output_tokens
        _usage["api_calls"] += 1
    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    return tool_use.input if tool_use else {}


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def ask_next_step(
    instruction: str,
    configs: dict[str, dict],
    screenshot_paths: dict[str, str],
    alias_map: dict[str, str] | None = None,
    step_history: list[str] | None = None,
    last_failure: str | None = None,
    ocr_texts: dict[str, str] | None = None,
    include_screenshots: bool = False,
    action_mode: str = "second_opinion",
) -> dict:
    """Decide the next action for the current instruction.

    Returns: {device_id, action, arg}
    """
    reverse_map = {v: k for k, v in alias_map.items()} if alias_map else {}

    # Filter screens, OCR, and device context to only the phone named at the
    # start of the instruction. e.g. "Phone1 holds the call." → Phone1 only.
    acting_id: str | None = None
    if alias_map:
        first_token = instruction.split()[0].rstrip(".,:")
        if first_token in alias_map:
            acting_id = alias_map[first_token]
            if acting_id in screenshot_paths:
                screenshot_paths = {acting_id: screenshot_paths[acting_id]}
            if ocr_texts and acting_id in ocr_texts:
                ocr_texts = {acting_id: ocr_texts[acting_id]}

    acting_configs = (
        {acting_id: configs[acting_id]}
        if acting_id and acting_id in configs
        else configs
    )
    valid_names = (
        [reverse_map.get(acting_id, acting_id)]
        if acting_id
        else sorted(alias_map.keys() if alias_map else configs.keys())
    )

    device_ctx = "\n".join(
        config_context(cfg, reverse_map.get(did, did))
        for did, cfg in acting_configs.items()
    )
    history = ", ".join(step_history) if step_history else "none"
    failure = f"\nLast failure: {last_failure}" if last_failure else ""

    base_text = (
        f"Contract goal: {instruction}\n"
        f"Actions taken so far: {history}{failure}\n\n"
        f"Device context:\n{device_ctx}"
    )

    screen_content = _screen_blocks(
        screenshot_paths, ocr_texts, reverse_map, include_screenshots, acting_configs
    )

    # ── Step 1: reasoning call (plain text) ──────────────────────────────
    reason_content = list(screen_content)
    reason_content.append({"type": "text", "text": base_text})
    reason_system = _REASON_SYSTEM + (_ACTION_OCR_NOTE if ocr_texts else "")
    reasoning = _call(reason_system, reason_content, max_tokens=300, temperature=0.0)
    print(f"[Claude reasoning] {reasoning}")

    # Fast experiment mode: parse reasoning output and press that key directly.
    if action_mode == "analysis_only":
        valid_keys = _valid_keys(acting_configs)
        suggested_key = _extract_reasoning_key(reasoning, valid_keys)
        acting_device_alias = (
            reverse_map.get(acting_id, acting_id)
            if acting_id
            else (valid_names[0] if valid_names else "")
        )
        print(
            f'[Claude action:auto] {{"device": {acting_device_alias!r}, "key": {suggested_key!r}, "source": "reasoning_parse"}}'
        )
        if not suggested_key:
            return {"device_id": "", "action": "", "arg": "", "label": ""}
        real_id = (alias_map or {}).get(acting_device_alias, acting_device_alias)
        return {
            "device_id": real_id,
            "action": "KEYPRESS",
            "arg": suggested_key,
            "label": "",
        }

    # ── Step 2: action call (tool) ───────────────────────────────────────
    valid_keys = _valid_keys(acting_configs)
    suggested_key = _extract_reasoning_key(reasoning, valid_keys)
    action_text = (
        f"Screen analysis:\n{reasoning}\n\n"
        f"Suggested key from analysis: {suggested_key or 'UNKNOWN'}\n\n"
        f"{base_text}"
    )
    # Keep action stage lightweight: reason over prior analysis + context only.
    # This preserves a second opinion while avoiding a second full screen parse.
    action_content = [{"type": "text", "text": action_text}]

    tool = {
        "name": "press_key",
        "description": "Select the next keypress after sanity-checking the analysis against current context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "enum": valid_names,
                    "description": "The phone to act on.",
                },
                "key": {
                    "type": "string",
                    "enum": valid_keys,
                    "description": "The key identified in the screen analysis.",
                },
                "label": {
                    "type": "string",
                    "description": "The on-screen label of this key as seen in the screen analysis (e.g. 'More', 'Confrnc', 'Hold'). Empty string if the slot is unlabeled.",
                },
                "decision_mode": {
                    "type": "string",
                    "enum": [
                        "follow_analysis",
                        "better_option",
                        "unsure_follow_analysis",
                    ],
                    "description": "How this key was chosen.",
                },
            },
            "required": ["device", "key", "label", "decision_mode"],
        },
    }

    inp = _tool_call(_ACTION_SYSTEM, [tool], action_content, max_tokens=96)
    model_key = inp.get("key", "")
    decision_mode = inp.get("decision_mode", "")

    # If action stage is unsure, or emits an invalid key, default to analysis key.
    if decision_mode == "unsure_follow_analysis" and suggested_key:
        model_key = suggested_key
    if model_key not in valid_keys and suggested_key:
        model_key = suggested_key

    # Last safety: keep execution flowing with a valid key for this device.
    if model_key not in valid_keys:
        model_key = valid_keys[0] if valid_keys else ""

    print(
        f"[Claude action] {{\"device\": {inp.get('device')!r}, \"key\": {model_key!r}, \"label\": {inp.get('label')!r}, \"decision_mode\": {decision_mode!r}, \"suggested\": {suggested_key!r}}}"
    )

    if not inp:
        return {"device_id": "", "action": "", "arg": "", "label": ""}

    real_id = (alias_map or {}).get(inp["device"], inp["device"])
    return {
        "device_id": real_id,
        "action": "KEYPRESS",
        "arg": model_key,
        "label": inp.get("label", ""),
    }


def verify_state(
    expected_state: str,
    screenshot_paths: dict[str, str],
    configs: dict[str, dict],
    alias_map: dict[str, str] | None = None,
    ocr_texts: dict[str, str] | None = None,
    include_screenshots: bool = False,
    state_name: str | None = None,
) -> tuple[bool, str]:
    """Check if the current screen state matches expected_state.

    Returns: (True, "YES") or (False, reason)
    """
    reverse_map = {v: k for k, v in alias_map.items()} if alias_map else {}

    state_def = ""
    if state_name:
        lines = [
            f"{reverse_map.get(did, did)}: {cfg['states'][state_name]}"
            for did, cfg in configs.items()
            if state_name in cfg.get("states", {})
        ]
        if lines:
            state_def = f"\nDevice state definition for '{state_name}':\n" + "\n".join(
                lines
            )

    user_text = f"Expected state: {expected_state}{state_def}"

    content = _screen_blocks(
        screenshot_paths, ocr_texts, reverse_map, include_screenshots, configs
    )
    content.append({"type": "text", "text": user_text})

    tool = {
        "name": "report_verification",
        "description": "Report whether the current phone screen matches the expected state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["YES", "NO"],
                    "description": "YES if the screen matches the expected state, NO otherwise.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief reason why the state does not match. Empty string if YES.",
                },
            },
            "required": ["verdict", "reason"],
        },
    }

    system = _VERIFY_SYSTEM + (
        "\nScreens are provided as OCR-extracted text listing softkey positions and screen content."
        if ocr_texts
        else "\nBase your answer on the screenshots."
    )
    inp = _tool_call(system, [tool], content)
    print(f"[Claude verify] {inp}")

    if not inp:
        return False, "no response"
    if inp["verdict"] == "YES":
        return True, "YES"
    return False, inp.get("reason") or "state mismatch"


def analyze_and_update_behaviors(
    scenario: str,
    action_screenshots: list[dict],
    configs: dict[str, dict],
    alias_map: dict[str, str] | None = None,
    use_ocr: bool = False,
    use_screenshots: bool = True,
) -> dict[str, dict]:
    """Infer new behaviors from the action+screenshot sequence and persist to JSON.

    Each entry in action_screenshots: {device_id, action, arg, screenshot[, ocr_text]}
    Returns the new behaviors that were written.
    """
    if not action_screenshots:
        return {}

    reverse_map = {v: k for k, v in alias_map.items()} if alias_map else {}
    dut_alias = next(iter(alias_map.keys())) if alias_map else "Phone1"

    existing_behaviors = (
        "\n".join(
            f"{reverse_map.get(did, did)}: {json.dumps(cfg.get('behaviors', {}), indent=2)}"
            for did, cfg in configs.items()
            if cfg.get("behaviors")
        )
        or "None"
    )

    existing_states = (
        "\n".join(
            f"{reverse_map.get(did, did)}: {list(cfg.get('states', {}).keys())}"
            for did, cfg in configs.items()
            if cfg.get("states")
        )
        or "None"
    )

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Scenario: {scenario}\n"
                f"DUT (device shown in screenshots): {dut_alias}\n\n"
                f"Existing behaviors:\n{existing_behaviors}\n\n"
                f"Existing states:\n{existing_states}\n\n"
                "Steps and post-action screens:"
            ),
        }
    ]

    for i, entry in enumerate(action_screenshots, 1):
        alias = reverse_map.get(entry["device_id"], entry["device_id"])
        arg = entry.get("arg", "")
        label = f"Step {i}: {alias} {entry['action']}" + (f" {arg}" if arg else "")
        content.append({"type": "text", "text": label})
        if use_ocr and entry.get("ocr_text"):
            content.append({"type": "text", "text": f"Screen:\n{entry['ocr_text']}"})
        if entry.get("screenshot") and (
            use_screenshots or not (use_ocr and entry.get("ocr_text"))
        ):
            content.append(_image_block(entry["screenshot"]))

    content.append(
        {"type": "text", "text": "What new behaviors and states can be inferred?"}
    )

    uses_ocr = use_ocr and any(e.get("ocr_text") for e in action_screenshots)
    system = _ANALYZE_SYSTEM + (
        "\nScreens are provided as OCR text, not images." if uses_ocr else ""
    )
    raw = _call(system, content, max_tokens=512, temperature=0.0)
    print(f"[Claude analyze] {raw}")

    if raw.startswith("```"):
        raw = raw.split("```", 2)[1].lstrip("json").strip()

    try:
        result: dict = json.loads(raw)
    except json.JSONDecodeError:
        print("[Claude] non-JSON response — skipping")
        return {}

    if not isinstance(result, dict):
        return {}

    # Resolve alias names (Phone1 → polye350) before merging
    resolved = {(alias_map or {}).get(k, k): v for k, v in result.items()}

    written: dict[str, dict] = {}
    for device_id, data in resolved.items():
        if device_id not in configs or not isinstance(data, dict):
            continue
        cfg = configs[device_id]

        for state, keys in data.get("behaviors", {}).items():
            if isinstance(keys, dict):
                cfg.setdefault("behaviors", {}).setdefault(state, {}).update(
                    {k: v for k, v in keys.items() if isinstance(v, str) and v.strip()}
                )

        for state, desc in data.get("states", {}).items():
            if isinstance(desc, str) and desc.strip():
                cfg.setdefault("states", {})[state] = desc.strip()

        # Ensure every behavior state has a states entry
        for state in cfg.get("behaviors", {}):
            cfg.setdefault("states", {}).setdefault(
                state, "State inferred from scenario."
            )

        save_config(cfg)
        written[device_id] = data
        print(f"[Claude] Updated {device_id}")

    return written


def resolve_contract_placeholders(
    contract_name: str,
    placeholders: list[str],
    semantic_args: list[str],
    configs: dict[str, dict],
    screenshot_paths: dict[str, str],
    alias_map: dict[str, str] | None = None,
    ocr_texts: dict[str, str] | None = None,
    include_screenshots: bool = False,
) -> dict[str, str]:
    """Resolve contract placeholders (e.g. {linekey}) to concrete keys via LLM."""
    if not placeholders:
        return {}

    reverse_map = {v: k for k, v in alias_map.items()} if alias_map else {}
    valid_keys = _valid_keys(configs)
    valid_aliases = sorted(reverse_map.get(did, did) for did in configs.keys())
    target = semantic_args[0] if semantic_args else ""

    device_ctx = "\n".join(
        config_context(cfg, reverse_map.get(did, did)) for did, cfg in configs.items()
    )

    prompt = (
        f"Contract: {contract_name}\n"
        f"Target semantic args: {semantic_args or ['']}\n"
        f"Target value: {target}\n"
        f"Placeholders to resolve: {placeholders}\n\n"
        f"Device context:\n{device_ctx}"
    )

    content = _screen_blocks(
        screenshot_paths,
        ocr_texts,
        reverse_map,
        include_screenshots,
        configs,
    )
    content.append({"type": "text", "text": prompt})

    tool = {
        "name": "resolve_placeholders",
        "description": "Resolve each contract placeholder to a concrete key on a specific device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "enum": valid_aliases,
                    "description": "Phone alias to resolve keys on.",
                },
                "mappings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "placeholder": {
                                "type": "string",
                                "enum": placeholders,
                            },
                            "key": {
                                "type": "string",
                                "enum": valid_keys,
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["placeholder", "key", "reason"],
                    },
                    "description": "One mapping per placeholder.",
                },
            },
            "required": ["device", "mappings"],
        },
    }

    inp = _tool_call(_RESOLVE_CONTRACT_PARAMS_SYSTEM, [tool], content, max_tokens=220)
    print(f"[Claude resolve] {inp}")

    if not inp:
        return {}

    out: dict[str, str] = {}
    for item in inp.get("mappings", []):
        if not isinstance(item, dict):
            continue
        ph = item.get("placeholder", "")
        key = item.get("key", "")
        if ph in placeholders and key in valid_keys:
            out[ph] = key

    return out
