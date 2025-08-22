import os, math
from typing import List, Tuple, Optional

try:
    import requests
except Exception:
    requests = None

Quote = Tuple[str, float, str]  # (provider, apr_decimal, notes)

def _fmt_asset(asset: str) -> str:
    return (asset or "").strip().upper()

def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def beefy_quotes(asset: str, timeout: int = 8) -> List[Quote]:
    if requests is None:
        raise RuntimeError("requests not available")
    base = "https://api.beefy.finance"
    print(f"[broker] → Beefy: GET {base}/apy")
    apy = requests.get(f"{base}/apy", timeout=timeout).json()
    print(f"[broker] → Beefy: GET {base}/vaults")
    vaults = requests.get(f"{base}/vaults", timeout=timeout).json()
    sym = _fmt_asset(asset)
    out: List[Quote] = []
    for v in vaults:
        try:
            vid = v.get("id")
            assets = [str(a).upper() for a in (v.get("assets") or [])]
            if sym in assets and vid in apy:
                apr = _safe_float(apy[vid], 0.0)
                name = v.get("name", vid)
                out.append(("Beefy", apr, name))
        except Exception:
            continue
    print(f"[broker]   Beefy matched {len(out)} vaults for asset={sym}")
    return out

def yearn_quotes(asset: str, chain: str = "ethereum", timeout: int = 8) -> List[Quote]:
    if requests is None:
        raise RuntimeError("requests not available")
    url = f"https://ydaemon.yearn.finance/{chain}/vaults/all"
    print(f"[broker] → Yearn: GET {url}")
    data = requests.get(url, timeout=timeout).json()
    sym = _fmt_asset(asset)
    out: List[Quote] = []
    for v in data:
        try:
            tok = v.get("token", {}) or {}
            underlying = (tok.get("symbol") or "").upper()
            if underlying == sym:
                apy_obj = v.get("apy", {}) or {}
                apr = _safe_float(apy_obj.get("net_apy", 0), 0.0)
                name = v.get("name", v.get("address", "vault"))
                out.append(("Yearn", apr, name))
        except Exception:
            continue
    print(f"[broker]   Yearn matched {len(out)} vaults for asset={sym}")
    return out

def static_quotes(asset: str, amount: float, duration_days: int) -> List[Quote]:
    sym = _fmt_asset(asset)
    base = 0.05 if sym in ("USDC", "USDT", "DAI") else 0.08
    dur_bump = min(max(duration_days, 0), 365) / 365.0 * 0.02
    return [
        ("LendingX", base + 0.00 + dur_bump, "stable lending (static)"),
        ("LPVaultY", base + 0.015 + dur_bump, "lp vault (static)"),
        ("PendleZ",  base + 0.025 + dur_bump, "tokenized yield (static)"),
    ]

def _normalize_and_limit(
    quotes: List[Quote],
    *,
    max_results: int = None,
    max_apr_decimal: float = None,
    treat_percent_threshold: float = None
) -> List[Quote]:
    """
    - Drop non-finite/negative/obviously broken APRs
    - Treat values > treat_percent_threshold as (percent) and divide by 100
    - Clamp to max_apr_decimal
    - Sort desc, limit to top N
    """
    max_results = int(os.getenv("YIELD_MAX_RESULTS", max_results or 10))
    max_apr_decimal = float(os.getenv("YIELD_MAX_APR_DECIMAL", max_apr_decimal or 0.50))  # default 50% cap
    treat_percent_threshold = float(os.getenv("YIELD_TREAT_PERCENT_THRESHOLD", treat_percent_threshold or 2.5))  # 2.5 = 250%

    cleaned: List[Quote] = []
    seen = set()
    for prov, apr, note in quotes:
        if apr is None:
            continue
        apr = _safe_float(apr, 0.0)
        if not math.isfinite(apr) or apr <= 0:
            continue
        # Some sources accidentally return "percentage" numbers (e.g., 12.5) instead of decimals
        if apr > treat_percent_threshold and apr < 1000:
            apr = apr / 100.0
        # Hard cap to drop insane values (or configure via env)
        if apr > max_apr_decimal:
            # skip absurd APRs
            continue
        key = (prov, note)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append((prov, apr, note))

    cleaned.sort(key=lambda q: q[1], reverse=True)
    return cleaned[:max_results]

def aggregate_quotes(
    *,
    mode: str,              # "public" or "mock"
    asset: str,
    amount: float,
    duration_days: int,
    timeout_sec: int = 8
) -> List[Quote]:
    print(f"[broker] Start aggregate (mode={mode}, asset={asset}, amount={amount}, duration={duration_days}d)")
    quotes: List[Quote] = []
    used_real = False

    if mode == "public":
        if os.getenv("YIELD_ENABLE_BEEFY") == "1":
            print("[broker] Trying Beefy…")
            try:
                q = beefy_quotes(asset, timeout=timeout_sec)
                print(f"[broker] Beefy returned {len(q)}")
                quotes += q
                used_real = True
            except Exception as e:
                print("[broker] Beefy error:", e)
        if os.getenv("YIELD_ENABLE_YEARN") == "1":
            print("[broker] Trying Yearn…")
            try:
                q = yearn_quotes(asset, timeout=timeout_sec)
                print(f"[broker] Yearn returned {len(q)}")
                quotes += q
                used_real = True
            except Exception as e:
                print("[broker] Yearn error:", e)

    if not quotes:
        if mode == "public" and not used_real:
            print("[broker] No real providers enabled; falling back to static.")
        else:
            print("[broker] No quotes from providers; falling back to static.")
        quotes = static_quotes(asset, amount, duration_days)

    # Normalize & limit
    quotes = _normalize_and_limit(quotes)
    print(f"[broker] Done aggregate (after clean): {len(quotes)} quotes")
    return quotes

def pick_best(quotes: List[Quote]) -> Optional[Quote]:
    return max(quotes, key=lambda q: q[1]) if quotes else None
