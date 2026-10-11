"""
Evaluation gate. Responsibilities:
  1. Pull paired production vs. shadow predictions that BOTH now have a
     real true label attached (this is where the delayed-label problem
     finally gets resolved — we wait until ground truth exists).
  2. Compute each model's real error against real outcomes, on the exact
     same requests (paired comparison, not just two separate averages).
  3. Run a statistical test: is the challenger's improvement real, or
     could this just be noise from this particular batch of traffic?
  4. Only if the challenger is BOTH numerically better AND the improvement
     is statistically significant, with enough evidence behind it, write a
     promotion_requests row.

This NEVER promotes anything itself. It only ever creates a pending
request. Promotion happens exclusively through POST /admin/promote,
which requires an explicit human decision.

Run:
    python evaluation/evaluation_gate.py --min-samples 30
"""
import argparse
import os

import numpy as np
import requests
from scipy.stats import wilcoxon

from serving.db import SessionLocal, PredictionLog, PromotionRequest
from llm.explain import explain_promotion

MIN_SAMPLES_DEFAULT = 30
P_VALUE_THRESHOLD = 0.05
API_URL = os.environ.get("SERVING_API_URL", "http://localhost:8000")


def load_paired_data(production_version, shadow_version):
    """
    Pulls production-model rows and shadow-model rows that each have a true
    label, then keeps only the timesteps present in BOTH — a true paired,
    apples-to-apples comparison rather than comparing two separate averages
    over possibly-different requests.
    """
    db = SessionLocal()
    try:
        prod_rows = (
            db.query(PredictionLog)
            .filter(
                PredictionLog.is_shadow == False,  # noqa: E712
                PredictionLog.model_version == production_version,
                PredictionLog.true_label.isnot(None),
            )
            .all()
        )
        shadow_rows = (
            db.query(PredictionLog)
            .filter(
                PredictionLog.is_shadow == True,  # noqa: E712
                PredictionLog.model_version == shadow_version,
                PredictionLog.true_label.isnot(None),
            )
            .all()
        )
    finally:
        db.close()

    prod_by_ts = {r.timestep: r for r in prod_rows if r.timestep is not None}
    shadow_by_ts = {r.timestep: r for r in shadow_rows if r.timestep is not None}
    common_ts = sorted(set(prod_by_ts) & set(shadow_by_ts))

    prod_errors, shadow_errors = [], []
    prod_latencies, shadow_latencies = [], []
    for ts in common_ts:
        true_label = prod_by_ts[ts].true_label
        prod_errors.append(abs(prod_by_ts[ts].prediction - true_label))
        shadow_errors.append(abs(shadow_by_ts[ts].prediction - true_label))
        if getattr(prod_by_ts[ts], "latency_ms", None) is not None:
            prod_latencies.append(prod_by_ts[ts].latency_ms)
        if getattr(shadow_by_ts[ts], "latency_ms", None) is not None:
            shadow_latencies.append(shadow_by_ts[ts].latency_ms)

    prod_lat_mean = float(np.mean(prod_latencies)) if prod_latencies else None
    shadow_lat_mean = float(np.mean(shadow_latencies)) if shadow_latencies else None

    return (
        np.array(prod_errors),
        np.array(shadow_errors),
        common_ts,
        prod_lat_mean,
        shadow_lat_mean,
    )


def run_gate(min_samples):
    status = requests.get(f"{API_URL}/status", timeout=5).json()
    production_version = status["production_version"]
    shadow_version = status["shadow_version"]

    if not shadow_version:
        print("No shadow model is currently deployed. Nothing to evaluate.")
        return None

    # If there's already a pending request for THIS challenger, we UPDATE
    # its numbers as more evidence comes in rather than either (a) creating
    # a duplicate every cycle, which is what happened before this fix, or
    # (b) freezing it forever at whatever sample size first triggered it.
    db = SessionLocal()
    try:
        existing_pending = (
            db.query(PromotionRequest)
            .filter(
                PromotionRequest.challenger_version == shadow_version,
                PromotionRequest.status == "pending",
            )
            .first()
        )
    finally:
        db.close()

    (
        prod_errors,
        shadow_errors,
        common_ts,
        prod_lat,
        shadow_lat,
    ) = load_paired_data(production_version, shadow_version)
    n = len(common_ts)

    print(f"Comparing {production_version} (production) vs {shadow_version} (shadow) "
          f"on {n} paired, labeled requests...")

    if n < min_samples:
        print(f"Not enough paired labeled data yet ({n}/{min_samples}). Send more traffic and re-run.")
        return None

    if existing_pending and n == existing_pending.n_labels_compared:
        print(f"No new paired data since promotion request #{existing_pending.id} was created "
              f"(still {n} samples). Nothing to update.")
        return existing_pending.id

    prod_mae = float(np.mean(prod_errors))
    shadow_mae = float(np.mean(shadow_errors))

    # alternative="greater": tests whether production's errors are
    # systematically GREATER than the challenger's — i.e. whether the
    # challenger is making smaller errors, consistently, not by chance.
    stat, p_value = wilcoxon(prod_errors, shadow_errors, alternative="greater")

    challenger_better = shadow_mae < prod_mae
    significant = p_value < P_VALUE_THRESHOLD
    passes_gate = challenger_better and significant

    print(f"Production MAE  : {prod_mae:.2f}")
    print(f"Challenger MAE  : {shadow_mae:.2f}")
    if prod_lat is not None and shadow_lat is not None:
        print(f"Production Latency: {prod_lat:.2f} ms")
        print(f"Challenger Latency: {shadow_lat:.2f} ms")
    print(f"Wilcoxon p-value: {p_value:.6f} (significant if < {P_VALUE_THRESHOLD})")

    if existing_pending:
        db = SessionLocal()
        try:
            promo = db.query(PromotionRequest).filter(PromotionRequest.id == existing_pending.id).first()
            promo.challenger_mae = shadow_mae
            promo.production_mae = prod_mae
            promo.challenger_latency_ms = shadow_lat
            promo.production_latency_ms = prod_lat
            promo.n_labels_compared = n
            # if more evidence flipped the conclusion, withdraw the request rather
            # than leave a stale "pending" row asking for approval of something
            # that no longer holds up
            if not passes_gate:
                promo.status = "withdrawn"
                print(f"\n>>> With more data, request #{promo.id} no longer passes the gate "
                      f"— marking it withdrawn instead of leaving it pending.")
            else:
                promo.explanation = explain_promotion(
                    shadow_version,
                    production_version,
                    shadow_mae,
                    prod_mae,
                    n,
                    p_value,
                    challenger_latency=shadow_lat,
                    production_latency=prod_lat,
                )
                print(f"\n>>> Updated promotion request #{promo.id} with {n} samples "
                      f"(still passes, awaiting your decision).")
            db.commit()
        finally:
            db.close()
        return existing_pending.id

    if passes_gate:
        explanation = explain_promotion(
            shadow_version,
            production_version,
            shadow_mae,
            prod_mae,
            n,
            p_value,
            challenger_latency=shadow_lat,
            production_latency=prod_lat,
        )
        db = SessionLocal()
        try:
            promo = PromotionRequest(
                challenger_version=shadow_version,
                production_version_at_request=production_version,
                challenger_mae=shadow_mae,
                production_mae=prod_mae,
                challenger_latency_ms=shadow_lat,
                production_latency_ms=prod_lat,
                n_labels_compared=n,
                status="pending",
                explanation=explanation,
            )
            db.add(promo)
            db.commit()
            db.refresh(promo)
        finally:
            db.close()
        print(f"\n>>> GATE PASSED. Promotion request #{promo.id} created — awaiting human approval.")
        print(f">>> Nothing has been promoted. Approve or reject via POST /admin/promote.")
        return promo.id
    else:
        reason = "challenger is not actually better on average" if not challenger_better \
            else "improvement is not statistically significant yet (could be noise)"
        print(f"\nGate not passed: {reason}. No promotion request created.")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES_DEFAULT)
    args = parser.parse_args()
    run_gate(args.min_samples)


if __name__ == "__main__":
    main()
