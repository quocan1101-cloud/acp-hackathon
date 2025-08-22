# renderers.py
from typing import List, Tuple

Quote = Tuple[str, float, str]

def _fmt_money(x: float) -> str:
    return f"{x:,.2f}"

def _fmt_pct(x: float) -> str:
    return f"{x*100:.2f}%"

def render_yield_result_table(
    *,
    title: str,
    inputs: dict,
    quotes: List[Quote],
    best: Quote | None
) -> str:
    lines = []
    lines.append(title)
    lines.append("-" * 72)
    lines.append("INPUTS")
    lines.append("-" * 72)
    for k in ["asset", "amount", "duration_days", "risk_level", "notes"]:
        if k in inputs and inputs[k] not in (None, "", []):
            v = inputs[k]
            if k == "amount":
                v = f"${_fmt_money(float(v))}"
            lines.append(f"{k:20} | {v}")
    lines.append("")
    lines.append("QUOTES")
    lines.append("-" * 72)
    if not quotes:
        lines.append("No quotes available.")
    else:
        lines.append(f"{'Provider':20} | {'APR':>8} | Notes")
        for p, apr, note in quotes:
            lines.append(f"{p:20} | {_fmt_pct(apr):>8} | {note}")
    lines.append("")
    lines.append("BEST")
    lines.append("-" * 72)
    if best:
        p, apr, note = best
        lines.append(f"Winner: {p} at {_fmt_pct(apr)} — {note}")
    else:
        lines.append("No winner.")
    lines.append("-" * 72)
    lines.append("Method: demo aggregation. Public mode uses optional providers; falls back to static quotes.")
    return "\n".join(lines)
