import os
import json
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional, List

from dotenv import load_dotenv
from web3 import Web3

from virtuals_acp.client import VirtualsACP
from virtuals_acp.env import EnvSettings
from virtuals_acp.memo import ACPMemo
from virtuals_acp.job import ACPJob
from virtuals_acp.models import (
    ACPJobPhase,
    ACPMemoStatus,
    ACPAgentSort,
    ACPGraduationStatus,
    ACPOnlineStatus,
    IACPAgent,
)
from virtuals_acp.offering import ACPJobOffering
from virtuals_acp.configs import DEFAULT_CONFIG


# ---------- helpers

def seller_accepted(job: ACPJob) -> bool:
    """True when the NEGOTIATION memo is signed/approved by the seller."""
    for m in job.memos:
        if m.next_phase == ACPJobPhase.NEGOTIATION and m.status == ACPMemoStatus.APPROVED:
            return True
    return False

from typing import Any, Dict, List, Optional, Tuple

# ---------- Formatting & display helpers ----------

def _fmt_bool(x: Optional[bool]) -> str:
    return "Yes" if x else "No"

def _fmt_price_native(price: Any) -> str:
    try:
        return f"{float(price):.4f}"
    except Exception:
        return str(price)

def _fmt_price_usd(price_usd: Any) -> str:
    try:
        return f"${float(price_usd):.2f}"
    except Exception:
        return f"${price_usd}"

def _safe_len(x) -> int:
    try:
        return len(x) if x else 0
    except Exception:
        return 0

def _truncate(s: Optional[str], n: int = 60) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"

def display_agents(agents: List[Any]) -> None:
    if not agents:
        print("No agents found for that keyword.")
        return
    print("\n=== Relevant Agents ===")
    for idx, a in enumerate(agents, start=1):
        name = getattr(a, "name", "(no name)")
        desc = getattr(a, "description", "")
        wa = getattr(a, "wallet_address", "")
        tw = getattr(a, "twitter_handle", "")
        metrics = getattr(a, "metrics", {}) or {}
        success_cnt = metrics.get("successfulJobCount")
        success_rate = metrics.get("successRate")
        online = metrics.get("isOnline", False)
        last_online_min = metrics.get("minsFromLastOnlineTime")

        offerings = getattr(a, "offerings", []) or []
        offerings_summary = ", ".join([getattr(o, "name", "Unnamed") for o in offerings]) or "—"

        print(f"\n[{idx}] {name}")
        if desc:
            print(f"    {_truncate(desc, 100)}")
        print(f"    Wallet: {wa}")
        if tw:
            print(f"    Twitter: @{tw}")
        print(f"    Online: {_fmt_bool(online)} | Last active: {last_online_min} min ago")
        print(f"    Jobs: {success_cnt} | Success rate: {success_rate}%")
        print(f"    Offerings ({_safe_len(offerings)}): {offerings_summary}")

def display_offerings(agent: Any) -> None:
    offerings = getattr(agent, "offerings", []) or []
    if not offerings:
        print("This agent has no offerings.")
        return
    print(f"\n=== Offerings by {getattr(agent, 'name', '(Agent)')} ===")
    for idx, o in enumerate(offerings, start=1):
        name = getattr(o, "name", "Unnamed offering")
        price = getattr(o, "price", None)
        price_usd = getattr(o, "price_usd", None)
        schema = getattr(o, "requirement_schema", {}) or {}
        required = schema.get("required", [])
        print(f"\n  ({idx}) {name}")
        print(f"      Price (native): {_fmt_price_native(price)} | Price (USD): {_fmt_price_usd(price_usd)}")
        print(f"      Required fields: {', '.join(required) if required else '—'}")

# ---------- Input helpers ----------

def _prompt_keyword() -> str:
    return input("\nEnter a keyword to search for agents: ").strip()

def _select_index(prompt: str, max_idx: int) -> int:
    while True:
        raw = input(f"{prompt} [1-{max_idx}] (or 0 to cancel): ").strip()
        if raw.isdigit():
            val = int(raw)
            if val == 0:
                return 0
            if 1 <= val <= max_idx:
                return val
        print("Invalid selection. Try again.")

def _coerce_to_type(raw: str, ftype) -> object:
    """
    Coerce a string 'raw' into the JSON Schema 'type'.
    Supports type being a string or a list (e.g., ["string","integer"]).
    """
    def _as_int(x: str):
        return int(x.replace(",", "").strip())
    def _as_float(x: str):
        return float(x.replace(",", "").strip())
    def _as_bool(x: str):
        # accept common truthy/falsey inputs
        y = x.strip().lower()
        if y in ("true", "t", "yes", "y", "1"): return True
        if y in ("false", "f", "no", "n", "0"): return False
        raise ValueError("not a bool")
    def _as_json(x: str):
        import json
        return json.loads(x)

    # type may be a list (union). Try in order.
    types = ftype if isinstance(ftype, list) else [ftype]

    last_err = None
    for t in types:
        try:
            if t == "integer":
                return _as_int(raw)
            if t == "number":
                return _as_float(raw)
            if t == "boolean":
                return _as_bool(raw)
            if t == "array" or t == "object":
                # expect JSON
                return _as_json(raw)
            # default: string
            return raw
        except Exception as e:
            last_err = e
            continue
    # If all attempts fail, fall back to raw string
    return raw

def _collect_inputs_from_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prompts for inputs and returns a payload that matches the JSON Schema types:
      - integer -> int
      - number  -> float
      - boolean -> bool (accepts yes/no/true/false/0/1)
      - array/object -> parsed from JSON (e.g. '[1,2]' or '{"k":"v"}')
      - union types (e.g. ["string","integer"]) -> tries each in order
    Also respects 'required' and 'enum' if present.
    """
    if not schema:
        return {}

    props = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    payload: Dict[str, Any] = {}

    print("\nPlease provide the required fields for this service.")
    for field, spec in props.items():
        spec = spec or {}
        ftype = spec.get("type", "string")          # may be a string or list
        enum = spec.get("enum")                     # optional enum constraint
        is_req = field in required

        # Helpful hint for arrays/objects
        hint = ""
        if ftype in ("array", "object") or (isinstance(ftype, list) and any(t in ("array","object") for t in ftype)):
            hint = "  (enter JSON, e.g. [1,2] or {\"k\":\"v\"})"
        elif ftype == "number":
            hint = "  (number, e.g. 123.45)"
        elif ftype == "integer":
            hint = "  (integer, e.g. 90)"
        elif ftype == "boolean":
            hint = "  (true/false, yes/no, 1/0)"

        while True:
            display_type = ftype if isinstance(ftype, str) else "/".join(ftype)
            enum_text = f" one of {enum}" if enum else ""
            val = input(f"  - {field} ({display_type}){enum_text}{' [required]' if is_req else ''}{hint}: ").strip()

            if not val:
                if is_req:
                    print("    This field is required.")
                    continue
                # optional & empty -> omit
                break

            # enum check (compare as string)
            if enum and val not in [str(e) for e in enum]:
                print(f"    Must be one of {enum}.")
                continue

            # coerce to schema type
            coerced = _coerce_to_type(val, ftype)

            # enum check again after coercion (e.g., 90 vs "90")
            if enum and coerced not in enum:
                # try string version if enum given as strings
                if str(coerced) not in [str(e) for e in enum]:
                    print(f"    Must be one of {enum}.")
                    continue

            payload[field] = coerced
            break

    return payload


# ---------- Main interactive browse/select ----------

def interactive_browse_and_select_offering(
    acp: Any,
    *,
    default_top_k: int = 10,
    sort_by_success: bool = True
):
    keyword = _prompt_keyword()
    if not keyword:
        print("Cancelled: empty keyword.")
        return None

    try:
        sort_vec = [ACPAgentSort.SUCCESSFUL_JOB_COUNT] if sort_by_success else []
        agents = acp.browse_agents(
            keyword=keyword,
            sort_by=sort_vec,
            top_k=default_top_k,
            graduation_status=ACPGraduationStatus.ALL,
            online_status=ACPOnlineStatus.ALL
        )
    except Exception as e:
        print(f"Error browsing agents: {e}")
        return None

    display_agents(agents)
    if not agents:
        return None

    sel_agent_idx = _select_index("Select an agent", len(agents))
    if sel_agent_idx == 0:
        print("Cancelled.")
        return None
    agent = agents[sel_agent_idx - 1]

    display_offerings(agent)
    offerings = getattr(agent, "offerings", []) or []
    if not offerings:
        return None

    sel_offering_idx = _select_index("Select a service", len(offerings))
    if sel_offering_idx == 0:
        print("Cancelled.")
        return None
    offering = offerings[sel_offering_idx - 1]

    schema = getattr(offering, "requirement_schema", {}) or {}
    payload = _collect_inputs_from_schema(schema) if schema else {}

    return agent, offering, payload

# ---------- main

def buyer(use_thread_lock: bool = True):
    load_dotenv(override=True)
    env = EnvSettings()

    # Required
    if env.WHITELISTED_WALLET_PRIVATE_KEY is None:
        raise ValueError("WHITELISTED_WALLET_PRIVATE_KEY is not set")
    if env.BUYER_AGENT_WALLET_ADDRESS is None:
        raise ValueError("BUYER_AGENT_WALLET_ADDRESS is not set")
    if env.BUYER_ENTITY_ID is None:
        raise ValueError("BUYER_ENTITY_ID is not set (must be an integer)")

    SELLER_WALLET = os.getenv("SELLER_AGENT_WALLET_ADDRESS", "").strip()
    if not SELLER_WALLET:
        raise ValueError("SELLER_AGENT_WALLET_ADDRESS is not set in .env")

    # Thread infra
    job_queue: deque[tuple[ACPJob, Optional[ACPMemo]]] = deque()
    job_queue_lock = threading.Lock()
    initiate_job_lock = threading.Lock()
    job_event = threading.Event()

    def safe_append_job(job: ACPJob, memo_to_sign: Optional[ACPMemo] = None):
        if use_thread_lock:
            with job_queue_lock:
                job_queue.append((job, memo_to_sign))
        else:
            job_queue.append((job, memo_to_sign))

    def safe_pop_job():
        if use_thread_lock:
            with job_queue_lock:
                if job_queue:
                    return job_queue.popleft()
        else:
            if job_queue:
                return job_queue.popleft()
        return None, None

    def on_new_task(job: ACPJob, memo_to_sign: Optional[ACPMemo] = None):
        print(f"[on_new_task] job {job.id} phase={job.phase}")
        if job.memos:
            latest = job.memos[-1]
            print(f"  latest memo: next_phase={latest.next_phase} status={latest.status}")
            if latest.content:
                print("  content:", latest.content)
        safe_append_job(job, memo_to_sign)
        job_event.set()

    def on_evaluate(job: ACPJob):
        # Seller delivered → sign acceptance
        try:
            print(f"[on_evaluate] accepting delivery for job {job.id}")
            job.evaluate(True, "Delivery accepted")
        except Exception as e:
            print(f"[on_evaluate] evaluate() failed: {e}")

    def process_job(job: ACPJob, memo_to_sign: Optional[ACPMemo] = None):
        """
        NO-BYPASS flow (requires USDC):
        REQUEST -> (seller respond True) -> NEGOTIATION approved -> TRANSACTION -> pay() -> EVALUATION -> COMPLETED
        """
        try:
            if job.phase in (ACPJobPhase.REQUEST, ACPJobPhase.NEGOTIATION):
                if not seller_accepted(job):
                    print(f"[buyer] job {job.id}: waiting for seller acceptance…")
                    return

                # Seller accepted; there should be a TRANSACTION memo queued by seller.
                tx_memo = next((m for m in job.memos if m.next_phase == ACPJobPhase.TRANSACTION), None)
                if not tx_memo:
                    print(f"[buyer] job {job.id}: seller accepted; waiting for TRANSACTION memo…")
                    return

                #    exact on-chain price
                price = getattr(job, "price", 0) or 0
                if price <= 0:
                    print(f"[buyer] job {job.id}: invalid price = {price}, cannot pay.")
                    return

                print(f"[buyer] paying job {job.id} amount={price}")
                job.pay(price)
                print(f"[buyer] job {job.id}: payment submitted. Waiting for delivery…")
                return

            if job.phase == ACPJobPhase.TRANSACTION:
                print(f"[buyer] job {job.id}: TRANSACTION — waiting seller deliver…")
                return

            if job.phase == ACPJobPhase.COMPLETED:
                print(f"[buyer] job {job.id}: COMPLETED ✅")
                return

            if job.phase == ACPJobPhase.REJECTED:
                print(f"[buyer] job {job.id}: REJECTED ❌")
                return

        except Exception as e:
            print(f"[buyer] process_job error: {e}")

    def job_worker():
        while True:
            job_event.wait()
            while True:
                job, memo_to_sign = safe_pop_job()
                if not job:
                    break
                process_job(job, memo_to_sign)
            if use_thread_lock:
                with job_queue_lock:
                    if not job_queue:
                        job_event.clear()
            else:
                if not job_queue:
                    job_event.clear()

    threading.Thread(target=job_worker, daemon=True).start()

    # Init client on Base mainnet
    acp = VirtualsACP(
        wallet_private_key=env.WHITELISTED_WALLET_PRIVATE_KEY,
        entity_id=env.BUYER_ENTITY_ID,
        agent_wallet_address=env.BUYER_AGENT_WALLET_ADDRESS,
        config=DEFAULT_CONFIG,           # Base mainnet
        on_new_task=on_new_task,
        on_evaluate=on_evaluate,
    )
    print("Connected to ACP.")

    # --- Interactive: keyword -> pick agent -> pick service -> fill schema ---
    selection = interactive_browse_and_select_offering(acp, default_top_k=10, sort_by_success=True)
    if not selection:
        print("No selection made. Exiting.")
        return

    chosen_agent, chosen_job_offering, service_requirement = selection

    # Prefer on-chain price from the selected offering; fall back to env if missing.
    try:
        OFFERING_PRICE = float(getattr(chosen_job_offering, "price", None) or os.getenv("OFFERING_PRICE_USDC", "0.01"))
    except Exception:
        OFFERING_PRICE = float(os.getenv("OFFERING_PRICE_USDC", "0.01"))

    print("\nYou selected:")
    print(f"  Agent: {getattr(chosen_agent, 'name', '(no name)')}")
    print(f"  Service: {getattr(chosen_job_offering, 'name', '(no name)')}")
    print(f"  Inputs: {service_requirement}")
    print(f"  Price: {OFFERING_PRICE}")

    # --- Initiate job with user-provided requirement payload ---
    with initiate_job_lock:
        job_id = chosen_job_offering.initiate_job(
            service_requirement=service_requirement,
            evaluator_address=getattr(env, "EVALUATOR_AGENT_WALLET_ADDRESS", None),
            expired_at=datetime.now() + timedelta(days=1),
        )
        print(f"Initial memo for job {job_id} created.")
        print(f"Job {job_id} initiated.")


    # Seed the queue initially (in case socket is slow)
    j = acp.get_job_by_onchain_id(job_id)
    safe_append_job(j, None)
    job_event.set()

    # Light poller so we progress even if we miss a socket event
    def poll_job_loop(jid: int):
        while True:
            try:
                jj = acp.get_job_by_onchain_id(jid)
                safe_append_job(jj, None)
                job_event.set()

                # stop condition
                if jj.phase in (ACPJobPhase.COMPLETED, ACPJobPhase.REJECTED):
                    print(f"[poller] Job {jid} is {jj.phase.name}, stopping poller.")
                    break

            except Exception as e:
                print("[buyer:poller] warning:", e)
                break  # exit on repeated error if you want

            time.sleep(3)


    threading.Thread(target=poll_job_loop, args=(job_id,), daemon=True).start()

    print("Listening for next steps…")
    threading.Event().wait()


if __name__ == "__main__":
    buyer()
