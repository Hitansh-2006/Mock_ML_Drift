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

from serving import metrics
from serving.db import SessionLocal, PredictionLog, PromotionRequest
from llm.explain import explain_promotion

MIN_SAMPLES_DEFAULT = 30
P_VALUE_THRESHOLD = 0.05
API_URL = os.environ.get("SERVING_API_URL", "http://localhost:8000")


def load_paired_data(production_version, shadow_version):
    """
    Pulls production-model rows and shadow-model rows that each have a true
    label, then keeps only the REQUESTS present in both (matched by request_id,
    which /predict gives the production row and its shadow twin). That is a true
    paired, apples-to-apples comparison on identical inputs. Timesteps play no
    part: real traffic has none, and replayed streams repeat them. Legacy rows
    without a request_id can't be paired and are ignored.

    Returns (production_errors, shadow_errors, request_ids, production_latencies,
    shadow_latencies); latencies are the raw per-request values (warm-up rows have none).
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

    prod_by_id = {r.request_id: r for r in prod_rows if r.request_id}
    shadow_by_id = {r.request_id: r for r in shadow_rows if r.request_id}
    common_ids = sorted(set(prod_by_id) & set(shadow_by_id))

    prod_errors, shadow_errors = [], []
    prod_latencies, shadow_latencies = [], []
    for rid in common_ids:
        true_label = prod_by_id[rid].true_label
        prod_errors.append(abs(prod_by_id[rid].prediction - true_label))
        shadow_errors.append(abs(shadow_by_id[rid].prediction - true_label))
        prod_latencies.append(prod_by_id[rid].latency_ms)
        shadow_latencies.append(shadow_by_id[rid].latency_ms)

    return (
        np.array(prod_errors),
        np.array(shadow_errors),
        common_ids,
        prod_latencies,
        shadow_latencies,
    )


def _wilcoxon_p(prod_errors, shadow_errors):
    """
    One-sided Wilcoxon p-value for "production's errors are systematically greater than the
    challenger's". If every paired difference is exactly zero (e.g. a challenger identical to
    production) scipy raises instead of answering; there is no evidence of improvement, so p = 1.
    """
    if np.all(np.asarray(prod_errors) == np.asarray(shadow_errors)):
        return 1.0
    return float(wilcoxon(prod_errors, shadow_errors, alternative="greater")[1])


def run_gate(min_samples):
    """CLI/back-compat entry point: the promotion request id touched this run, or None."""
    return evaluate(min_samples).get("promotion_request_id")


def evaluate(min_samples, status=None):
    """
    One gate pass. Returns a dict the automation loop can report:
      outcome: no_shadow | waiting | no_new_data | request_created | request_updated
               | request_withdrawn | not_passed
    plus n / min_samples / promotion_request_id where they apply.
    `status` is {"production_version", "shadow_version"}; pass it when running
    inside the serving process, otherwise it is fetched from the API.
    """
    if status is None:
        status = requests.get(f"{API_URL}/status", timeout=5).json()
    production_version = status["production_version"]
    shadow_version = status["shadow_version"]

    if not shadow_version:
        print("No shadow model is currently deployed. Nothing to evaluate.")
        return {"outcome": "no_shadow"}

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
        common_ids,
        prod_latencies,
        shadow_latencies,
    ) = load_paired_data(production_version, shadow_version)
    n = len(common_ids)

    print(f"Comparing {production_version} (production) vs {shadow_version} (shadow) "
          f"on {n} paired, labeled requests...")

    if n < min_samples:
        print(f"Not enough paired labeled data yet ({n}/{min_samples}). Send more traffic and re-run.")
        return {"outcome": "waiting", "n": n, "min_samples": min_samples, "challenger_version": shadow_version}

    if existing_pending and n == existing_pending.n_labels_compared:
        print(f"No new paired data since promotion request #{existing_pending.id} was created "
              f"(still {n} samples). Nothing to update.")
        return {"outcome": "no_new_data", "n": n, "min_samples": min_samples,
                "challenger_version": shadow_version, "promotion_request_id": existing_pending.id}

    prod_mae = float(np.mean(prod_errors))
    shadow_mae = float(np.mean(shadow_errors))

    # One-sided: is production's error systematically GREATER than the challenger's,
    # i.e. is the challenger making smaller errors consistently, not by chance?
    p_value = _wilcoxon_p(prod_errors, shadow_errors)

    challenger_better = shadow_mae < prod_mae
    significant = p_value < P_VALUE_THRESHOLD
    passes_gate = challenger_better and significant

    # Inference latency (never HTTP, never model loading): median and 95th percentile
    # over the paired requests. Reported to the human; it does not decide the gate.
    prod_lat, prod_lat_p95 = metrics.latency_percentiles(prod_latencies)
    shadow_lat, shadow_lat_p95 = metrics.latency_percentiles(shadow_latencies)

    print(f"Production MAE  : {prod_mae:.2f}")
    print(f"Challenger MAE  : {shadow_mae:.2f}")
    if prod_lat is not None and shadow_lat is not None:
        print(f"Production inference latency : p50 {prod_lat:.2f} ms, p95 {prod_lat_p95:.2f} ms")
        print(f"Challenger inference latency : p50 {shadow_lat:.2f} ms, p95 {shadow_lat_p95:.2f} ms")
    print(f"Wilcoxon p-value: {p_value:.6f} (significant if < {P_VALUE_THRESHOLD})")

    if existing_pending:
        db = SessionLocal()
        try:
            promo = db.query(PromotionRequest).filter(PromotionRequest.id == existing_pending.id).first()
            promo.challenger_mae = shadow_mae
            promo.production_mae = prod_mae
            promo.challenger_latency_ms = shadow_lat
            promo.production_latency_ms = prod_lat
            promo.challenger_latency_p95_ms = shadow_lat_p95
            promo.production_latency_p95_ms = prod_lat_p95
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
        return {"outcome": "request_updated" if passes_gate else "request_withdrawn", "n": n,
                "min_samples": min_samples, "challenger_version": shadow_version,
                "promotion_request_id": existing_pending.id}

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
                challenger_latency_p95_ms=shadow_lat_p95,
                production_latency_p95_ms=prod_lat_p95,
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
        print(f">>> Nothing has been promoted. A human approves or rejects it in the dashboard.")
        return {"outcome": "request_created", "n": n, "min_samples": min_samples,
                "challenger_version": shadow_version, "promotion_request_id": promo.id}
    else:
        reason = "challenger is not actually better on average" if not challenger_better \
            else "improvement is not statistically significant yet (could be noise)"
        print(f"\nGate not passed: {reason}. No promotion request created.")
        return {"outcome": "not_passed", "n": n, "min_samples": min_samples,
                "challenger_version": shadow_version, "reason": reason, "p_value": float(p_value)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES_DEFAULT)
    args = parser.parse_args()
    run_gate(args.min_samples)


if __name__ == "__main__":
    main()
