"""
Optional LLM-generated plain-English explanations, layered on top of the
existing statistical drift/promotion logic. This is a real feature, not
window dressing: it takes the exact per-feature KS results and MAE/p-value
numbers the pipeline already computes, and turns them into a short summary
a non-technical stakeholder can read at a glance next to the numbers.

Design choices, on purpose:
  - Uses Claude Haiku: this is a short, low-stakes summarization task over
    numbers the pipeline already trusts, not something that needs a
    frontier model.
  - NEVER blocks or breaks the pipeline. If GROQ_API_KEY isn't set, or
    the API call fails for any reason (network, rate limit, whatever),
    this falls back to a deterministic, template-built explanation instead
    of raising. Drift detection, retraining, and the evaluation gate all
    worked before this feature existed and must keep working identically
    if the LLM call fails — this module is additive, never load-bearing.
  - The model is only ever asked to explain numbers the pipeline already
    computed and hands it directly in the prompt — never to invent its own
    analysis or pull in numbers from nowhere.

Setup: set the GROQ_API_KEY environment variable. Without it, every
function here silently returns its template fallback — nothing breaks,
you just get the plain (non-LLM) version of the explanation.
"""
import os
import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.1-8b-instant"
REQUEST_TIMEOUT = 15


def _call_claude(prompt: str, max_tokens: int = 200):
    """Returns Claude's text response, or None if the key is missing or the call fails."""
    if not GROQ_API_KEY:
        return None
    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        return text or None
    except Exception as e:
        print(f"[llm] Claude call failed, using template explanation instead: {e}")
        return None


def explain_drift(per_feature: dict, n_drifted: int, overall_score: float) -> str:
    """Plain-English explanation of a drift-monitor result. Only called for triggered events."""
    drifted = [f"{name} (KS={v['ks_statistic']})" for name, v in per_feature.items() if v["drifted"]]

    fallback = (
        f"Drift detected in {n_drifted} feature(s): {', '.join(drifted)}. These columns now "
        f"look statistically different from the data the current production model was "
        f"trained on (overall drift score {overall_score})."
    )

    prompt = (
        "You are writing a 1-2 sentence, plain-English note for a non-technical stakeholder "
        "reviewing an ML monitoring dashboard. A statistical drift check just flagged these "
        f"features as significantly shifted vs. training data:\n\n{chr(10).join(drifted)}\n\n"
        f"Overall drift score: {overall_score}\n\n"
        "Explain what this likely means for the model's predictions going forward. Use ONLY "
        "the numbers given above — do not invent any figures. Max 2 sentences."
    )
    return _call_claude(prompt) or fallback


def explain_promotion(challenger_version, production_version, challenger_mae,
                       production_mae, n_samples, p_value,
                       challenger_latency=None, production_latency=None) -> str:
    """Plain-English recommendation summary for a pending promotion request."""
    pct_improvement = ((production_mae - challenger_mae) / production_mae * 100) if production_mae else 0

    latency_note = ""
    if challenger_latency is not None and production_latency is not None:
        latency_note = f" (latency: {challenger_latency:.1f}ms vs prod {production_latency:.1f}ms)"

    fallback = (
        f"Challenger {challenger_version} shows a {pct_improvement:.0f}% lower average error "
        f"(${challenger_mae:,.0f} vs ${production_mae:,.0f}){latency_note} than production {production_version}, "
        f"based on {n_samples} paired real predictions (p={p_value:.4f})."
    )

    latency_stats = ""
    if challenger_latency is not None and production_latency is not None:
        latency_stats = (
            f"- Production inference latency: {production_latency:.1f} ms\n"
            f"- Challenger inference latency: {challenger_latency:.1f} ms\n"
        )

    prompt = (
        "You are writing a short, plain-English note for a human who must decide whether to "
        "approve promoting a new ML model to production. Here is the statistical evidence "
        "already computed by the system:\n\n"
        f"- Current production model ({production_version}) average error: ${production_mae:,.0f}\n"
        f"- Challenger model ({challenger_version}) average error: ${challenger_mae:,.0f}\n"
        f"{latency_stats}"
        f"- Based on {n_samples} paired real predictions\n"
        f"- Statistical significance (p-value): {p_value:.6f}\n\n"
        "In 2 sentences max, summarize whether this looks like a safe, well-supported "
        "promotion (noting both accuracy and speed if provided), using ONLY the numbers "
        "given above — do not invent additional statistics."
    )
    return _call_claude(prompt) or fallback
