from __future__ import annotations

from pathlib import Path

from .db import QuorumDB


def compute_signal_weights(db: QuorumDB) -> dict[str, float]:
    """Compute adjusted signal weights from feedback data.

    For each signal, calculates accuracy rate based on whether the signal's
    candidate matched the approved/corrected answer. Adjusts weight relative
    to baseline (1.0) and clamps to [0.1, 3.0].
    """
    # Get all feedback with associated signals
    rows = db.conn.execute("""
        SELECT f.media_id, f.action, f.original, f.correction,
               s.signal_name, s.candidate, s.confidence
        FROM feedback f
        JOIN signals s ON s.media_id = f.media_id
    """).fetchall()

    if not rows:
        return {}

    # Track per-signal accuracy
    signal_stats: dict[str, dict[str, int]] = {}

    for media_id, action, original, correction, signal_name, candidate, confidence in rows:
        if signal_name not in signal_stats:
            signal_stats[signal_name] = {"correct": 0, "total": 0}
        signal_stats[signal_name]["total"] += 1

        # Determine the "right answer"
        if action == "approve":
            right_answer = original
        elif action == "correct":
            right_answer = correction
        else:
            continue  # reject doesn't tell us what's right

        # Normalize for comparison
        if _normalize(candidate) == _normalize(right_answer):
            signal_stats[signal_name]["correct"] += 1

    # Compute weights
    weights: dict[str, float] = {}
    for signal_name, stats in signal_stats.items():
        if stats["total"] == 0:
            continue
        accuracy = stats["correct"] / stats["total"]
        baseline = 0.5  # expected random accuracy
        weight = max(0.1, min(3.0, accuracy / baseline if baseline > 0 else 1.0))
        weights[signal_name] = round(weight, 2)

    return weights


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    return text.lower().strip().replace(".", "").replace(",", "").replace("  ", " ")


def retune_signals(db: QuorumDB, dry_run: bool = False) -> dict[str, dict]:
    """Recalculate signal weights from feedback and optionally write to config.

    Returns a dict mapping signal names to their old and new weights.
    """
    new_weights = compute_signal_weights(db)
    if not new_weights:
        return {}

    # Read current weights from config (default all 1.0)
    changes: dict[str, dict] = {}
    for signal_name, new_weight in new_weights.items():
        changes[signal_name] = {
            "old": 1.0,  # default baseline
            "new": new_weight,
            "delta": round(new_weight - 1.0, 2),
        }

    if not dry_run:
        # Write weights to a signal_weights.json file
        import json
        weights_path = Path("signal_weights.json")
        weights_path.write_text(
            json.dumps(new_weights, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return changes
