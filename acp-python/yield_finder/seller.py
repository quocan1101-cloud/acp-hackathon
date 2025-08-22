import threading
import time
import json
import os, time
from broker import aggregate_quotes, pick_best
from renderers import render_yield_result_table
from services import build_deliverable_for_service
from collections import deque
from typing import Optional

from dotenv import load_dotenv

from virtuals_acp import VirtualsACP, ACPJob, ACPJobPhase, ACPMemo, IDeliverable
from virtuals_acp.env import EnvSettings

load_dotenv(override=True)

#------ helper

def _list_jobs_snapshot(job_queue):
    # show a stable snapshot without mutating the queue
    snapshot = list(job_queue)
    if not snapshot:
        print("\n(No incoming jobs yet)")
        return []
    print("\n=== Incoming Jobs ===")
    for i, (j, m) in enumerate(snapshot, start=1):
        service = getattr(j, "service_name", None)
        print(f"[{i}] Job {j.id}  | phase={j.phase}  | service={service}")
    return snapshot

def _pop_by_index(job_queue, idx: int):
    # pop a specific index from the deque while preserving order
    buf = []
    for _ in range(len(job_queue)):
        item = job_queue.popleft()
        buf.append(item)
    if 0 <= idx < len(buf):
        chosen = buf.pop(idx)
    else:
        chosen = None
    for item in buf:
        job_queue.append(item)
    return chosen

import json  # at top if not already

def _show_request_summary(job: ACPJob):
    svc = getattr(job, "service_name", None)
    req = getattr(job, "service_requirement", None)
    print(f"\n=== Incoming Request ===")
    print(f"Job ID: {job.id}")
    print(f"Phase : {job.phase}")
    if svc:
        print(f"Service: {svc}")
    if req:
        # pretty print if JSON
        try:
            obj = json.loads(req) if isinstance(req, str) else req
            print("Requirement:", json.dumps(obj, indent=2))
        except Exception:
            print("Requirement:", req)

#----- main

def seller(use_thread_lock: bool = True):
    env = EnvSettings()

    if env.WHITELISTED_WALLET_PRIVATE_KEY is None:
        raise ValueError("WHITELISTED_WALLET_PRIVATE_KEY is not set")
    if env.SELLER_AGENT_WALLET_ADDRESS is None:
        raise ValueError("SELLER_AGENT_WALLET_ADDRESS is not set")
    if env.SELLER_ENTITY_ID is None:
        raise ValueError("SELLER_ENTITY_ID is not set")

    job_queue = deque()
    job_queue_lock = threading.Lock()
    job_event = threading.Event()

    def safe_append_job(job, memo_to_sign: Optional[ACPMemo] = None):
        if use_thread_lock:
            print(f"[safe_append_job] Acquiring lock to append job {job.id}")
            with job_queue_lock:
                print(f"[safe_append_job] Lock acquired, appending job {job.id} to queue")
                job_queue.append((job, memo_to_sign))
        else:
            job_queue.append((job, memo_to_sign))

    def safe_pop_job():
        if use_thread_lock:
            print(f"[safe_pop_job] Acquiring lock to pop job")
            with job_queue_lock:
                if job_queue:
                    job, memo_to_sign = job_queue.popleft()
                    print(f"[safe_pop_job] Lock acquired, popped job {job.id}")
                    return job, memo_to_sign
                else:
                    print("[safe_pop_job] Queue is empty after acquiring lock")
        else:
            if job_queue:
                job, memo_to_sign = job_queue.popleft()
                print(f"[safe_pop_job] Popped job {job.id} without lock")
                return job, memo_to_sign
            else:
                print("[safe_pop_job] Queue is empty (no lock)")
        return None, None

    def job_worker():
        while True:
            job_event.wait()
            while True:
                job, memo_to_sign = safe_pop_job()
                if not job:
                    break

                # For REQUEST, prompt inline (no extra thread)
                if job.phase == ACPJobPhase.REQUEST:
                    _show_request_summary(job)
                    # only prompt if this is the negotiation memo
                    if memo_to_sign is not None and memo_to_sign.next_phase == ACPJobPhase.NEGOTIATION:
                        choice = input("Accept (a) / Reject (r) / Skip (s)? ").strip().lower()
                        if choice == "a":
                            print(f"[process_job] Accepting job {job.id}")
                            job.respond(True)
                        elif choice == "r":
                            reason = input("Reason to reject: ").strip() or f"Job {job.id} rejected"
                            print(f"[process_job] Rejecting job {job.id}")
                            job.respond(False, reason=reason)
                        else:
                            print(f"[process_job] Skipping job {job.id}")
                            # put back so you can act later when a new event arrives
                            safe_append_job(job, memo_to_sign)
                    else:
                        # Not a negotiation memo yet; requeue
                        safe_append_job(job, memo_to_sign)

                else:
                    # Non-interactive phases can be processed in a background thread
                    threading.Thread(
                        target=handle_job_with_delay, args=(job, memo_to_sign), daemon=True
                    ).start()

            # clear event only if queue is empty
            if use_thread_lock:
                with job_queue_lock:
                    if not job_queue:
                        job_event.clear()
            else:
                if not job_queue:
                    job_event.clear()


    def handle_job_with_delay(job, memo_to_sign):
        try:
            process_job(job, memo_to_sign)
            time.sleep(2)
        except Exception as e:
            print(f"\u274c Error processing job: {e}")

    def on_new_task(job: ACPJob, memo_to_sign: Optional[ACPMemo] = None):
        print(f"[on_new_task] Received job {job.id} (phase: {job.phase})")
        safe_append_job(job, memo_to_sign)
        job_event.set()

    def process_job(job, memo_to_sign: Optional[ACPMemo] = None):
        if (
            job.phase == ACPJobPhase.TRANSACTION and
            memo_to_sign is not None and
            memo_to_sign.next_phase == ACPJobPhase.EVALUATION
        ):
            print(f"Delivering job {job.id}")
            # Parse requirement payload
            buyer_req_raw = job.service_requirement
            try:
                req = json.loads(buyer_req_raw) if isinstance(buyer_req_raw, str) else (buyer_req_raw or {})
            except Exception:
                req = {"raw": buyer_req_raw}

            # Inputs (coerce gently)
            asset = str(req.get("asset", "USDC"))
            try:
                amount = float(str(req.get("amount", 0)).replace(",", ""))
            except Exception:
                amount = 0.0
            duration_days = int(req.get("duration_days", 30))
            # Your deliverable schema uses "risk level" (with a space)
            risk_level = str(req.get("risk level", req.get("risk_level", "medium")))
            notes = str(req.get("notes", ""))

            mode = str(req.get("mode", "public")).lower()
            print(f"[seller] inputs: asset={asset} amount={amount} duration_days={duration_days} risk='{risk_level}' mode={mode}")
            print(f"[seller] provider flags: Beefy={os.getenv('YIELD_ENABLE_BEEFY')} Yearn={os.getenv('YIELD_ENABLE_YEARN')}")

            # Call broker (this prints its own progress)
            quotes = aggregate_quotes(
                mode=mode,
                asset=asset,
                amount=amount,
                duration_days=duration_days,
                timeout_sec=8
            )
            winner = pick_best(quotes)

            # quotes must be array of STRINGS (per your schema)
            quotes_strs = [f"{prov}: {apr*100:.2f}% — {note}" for (prov, apr, note) in (quotes or [])]

            if winner:
                best_obj = {"provider": winner[0], "apr": float(winner[1]), "notes": winner[2]}
                summary = f"Best APR: {winner[0]} at {winner[1]*100:.2f}%"
            else:
                best_obj = {"provider": "", "apr": 0.0, "notes": "No winner"}
                summary = "No quotes available"

            payload = {
                "service": "Find Yields",
                "inputs": {
                    "asset": asset,
                    "amount": amount,
                    "duration_days": duration_days,
                    "risk level": risk_level,  # key with a space
                    "notes": notes
                },
                "quotes": quotes_strs,
                "best": best_obj,
                "summary": summary
            }

            print(f"[seller] summary: {summary}")
            deliverable = IDeliverable(type="json", value=json.dumps(payload))
            job.deliver(deliverable)
            print(f"[seller] Delivered job {job.id}")

        elif job.phase == ACPJobPhase.TRANSACTION and memo_to_sign and memo_to_sign.next_phase == ACPJobPhase.EVALUATION:
            print(f"Delivering job {job.id}")
            # … build deliverable …
            job.deliver(deliverable)
            print(f"[seller] Delivered job {job.id}, stopping after this delivery.")
            return  # or even os._exit(0) if you want to kill the process


    threading.Thread(target=job_worker, daemon=True).start()

    # Initialize the ACP client
    VirtualsACP(
        wallet_private_key=env.WHITELISTED_WALLET_PRIVATE_KEY,
        agent_wallet_address=env.SELLER_AGENT_WALLET_ADDRESS,
        on_new_task=on_new_task,
        entity_id=env.SELLER_ENTITY_ID
    )

    print("Waiting for new task...")
    threading.Event().wait()


if __name__ == "__main__":
    seller()