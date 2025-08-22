# services.py
import json
from typing import Dict, Any, Tuple, List

# ---------------------------
# Common small helpers
# ---------------------------

def _fmt_money(x: float) -> str:
    return f"{x:,.2f}"

def _fmt_pct(x: float) -> str:
    return f"{x*100:.2f}%"

def _parse_amount(val) -> float:
    try:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            return float(val.replace(",", "").strip())
    except Exception:
        return 0.0
    return 0.0

def _ask_overrides(current: Dict[str, Any], fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generic override prompt. `fields` specifies name & type for each expected field.
    type can be: 'string' | 'number' | 'integer'
    """
    print("\n--- Confirm or Override Inputs ---")
    updated = dict(current)
    for f in fields:
        name = f["name"]
        typ = f.get("type", "string")
        cur = updated.get(name, "")
        val = input(f"{name} [{cur}]: ").strip()
        if val == "":
            continue
        try:
            if typ == "number":
                updated[name] = float(val.replace(",", ""))
            elif typ == "integer":
                updated[name] = int(val)
            else:
                updated[name] = val
        except Exception:
            print(f"Invalid value for {name} (expected {typ}); keeping {cur}")
            updated[name] = cur
    print("--- Using values:", updated)
    return updated

# ---------------------------
# Service: Find Yields
# ---------------------------

RISK_APY = {"low": 0.04, "medium": 0.08, "high": 0.15}

def _normalize_risk_and_apy(risk_level: str, explicit_apy: float = None) -> float:
    if explicit_apy is not None and explicit_apy > 0:
        return explicit_apy
    return RISK_APY.get((risk_level or "").lower(), 0.06)

def _find_yields_compute_table(asset: str, amount: float, duration_days: int, risk_level: str, apy: float = None, notes: str = "") -> str:
    apy_val = _normalize_risk_and_apy(risk_level, apy)
    t = max(int(duration_days), 0) / 365.0
    projected_yield = amount * apy_val * t
    end_balance = amount + projected_yield
    fee_rate = 0.001
    est_fees = amount * fee_rate

    lines = []
    lines.append("Find Yields — Draft Projection")
    lines.append("-" * 64)
    lines.append("INPUTS")
    lines.append("-" * 64)
    lines.append(f"{'Asset':20} | {asset}")
    lines.append(f"{'Amount':20} | ${_fmt_money(amount)}")
    lines.append(f"{'Duration (days)':20} | {duration_days}")
    lines.append(f"{'Risk level':20} | {risk_level} (APY {_fmt_pct(apy_val)})")
    if notes:
        lines.append(f"{'Notes':20} | {notes}")
    lines.append("")
    lines.append("RESULTS")
    lines.append("-" * 64)
    lines.append(f"{'APY (assumed)':20} | {_fmt_pct(apy_val)}")
    lines.append(f"{'Projected yield':20} | ${_fmt_money(projected_yield)}")
    lines.append(f"{'Est. fees (0.10%)':20} | ${_fmt_money(est_fees)}")
    lines.append(f"{'Projected end balance':20} | ${_fmt_money(end_balance - est_fees)}")
    lines.append("-" * 64)
    lines.append("Method: simple prorated APY, no compounding. Demo only.")
    return "\n".join(lines)

def _handle_find_yields(req: Dict[str, Any], interactive: bool) -> Tuple[str, str]:
    # Parse + defaults
    if isinstance(req, str):
        try: req = json.loads(req)
        except Exception: req = {"raw": req}
    asset = str(req.get("asset", "USDC"))
    amount = _parse_amount(req.get("amount", 0))
    duration_days = int(req.get("duration_days", 30))
    risk_level = str(req.get("risk_level", "medium"))
    apy = None
    try:
        if "apy" in req:
            apy = float(req["apy"])
    except Exception:
        apy = None
    notes = str(req.get("notes", ""))

    # Interactive overrides (specific to this service)
    if interactive:
        fields = [
            {"name": "asset", "type": "string"},
            {"name": "amount", "type": "number"},
            {"name": "duration_days", "type": "integer"},
            {"name": "risk_level", "type": "string"},
            {"name": "apy", "type": "number"},
            {"name": "notes", "type": "string"},
        ]
        updated = _ask_overrides(
            {
                "asset": asset,
                "amount": amount,
                "duration_days": duration_days,
                "risk_level": risk_level,
                "apy": apy,
                "notes": notes,
            },
            fields
        )
        asset = updated["asset"]
        amount = float(updated["amount"])
        duration_days = int(updated["duration_days"])
        risk_level = updated["risk_level"]
        apy = updated.get("apy")
        notes = updated.get("notes", "")

    # Build deliverable text
    text = _find_yields_compute_table(asset, amount, duration_days, risk_level, apy, notes)
    return ("text", text)

# ---------------------------
# Service: Boost League
# ---------------------------

def _handle_boost_league(req: Dict[str, Any], interactive: bool) -> Tuple[str, str]:
    """
    Expect a simple string input like {"leagueboost":"<gamertag or request>"}.
    We return a bespoke confirmation string for testing.
    """
    if isinstance(req, str):
        try: req = json.loads(req)
        except Exception: req = {"leagueboost": req}

    leagueboost = str(req.get("leagueboost", "")).strip()

    if interactive:
        fields = [{"name": "leagueboost", "type": "string"}]
        updated = _ask_overrides({"leagueboost": leagueboost}, fields)
        leagueboost = updated["leagueboost"]

    # Build a simple bespoke deliverable (text)
    lines = []
    lines.append("Boost League — Request Summary")
    lines.append("-" * 64)
    lines.append(f"{'Request':20} | {leagueboost or '(none provided)'}")
    lines.append("-" * 64)
    lines.append("Status: queued for processing (demo)")
    text = "\n".join(lines)
    return ("text", text)

# ---------------------------
# Dispatcher
# ---------------------------

# Map service name -> handler
SERVICE_DISPATCH = {
    "Find Yields": _handle_find_yields,
    "Boost League": _handle_boost_league,
}

def build_deliverable_for_service(service_name: str, requirement: Any, *, interactive: bool = True) -> Tuple[str, str]:
    """
    Returns (deliverable_type, deliverable_value).
    If service isn't known, we echo the requirement as JSON text.
    """
    handler = SERVICE_DISPATCH.get(service_name)
    if handler:
        return handler(requirement, interactive)
    # Fallback: echo input
    try:
        text = json.dumps(requirement if not isinstance(requirement, str) else json.loads(requirement), indent=2)
    except Exception:
        text = str(requirement)
    return ("text", f"Unknown service: {service_name or '(none)'}\nInput:\n{text}")
