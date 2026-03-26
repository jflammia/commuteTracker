"""Training orchestrator: builds training data from labeled commutes and trains models.

This connects the labeled data (from LabelStore) with the feature engineering
(from features.py) and the model (from model.py).

Usage:
    from src.ml.trainer import train_from_labels
    metrics = train_from_labels(db)  # Uses all labeled data
    metrics = train_from_labels(db, commute_ids=["2026-03-26-morning"])
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from src.ml.features import FEATURE_COLUMNS, build_training_set, extract_point_features
from src.ml.model import BaselineModel, ModelMetrics
from src.storage.derived_store import DerivedStore
from src.storage.label_store import LabelStore

logger = logging.getLogger(__name__)


def train_from_labels(
    db,
    derived_dir: str | Path | None = None,
    model_path: str | Path | None = None,
    commute_ids: list[str] | None = None,
    max_depth: int = 10,
    test_fraction: float = 0.2,
) -> tuple[BaselineModel, ModelMetrics]:
    """Train a baseline model from labeled commute data.

    Loads labeled commutes from the DerivedStore, extracts features,
    applies label corrections, and trains the model.

    Args:
        db: Database instance (for LabelStore access).
        derived_dir: Path to derived Parquet files.
        model_path: Where to save the trained model. If None, saves to
                     derived_dir/model/baseline.json.
        commute_ids: If specified, only use these commutes for training.
        max_depth: Maximum decision tree depth.
        test_fraction: Hold-out fraction for evaluation.

    Returns:
        (model, metrics) tuple.
    """
    store = DerivedStore(derived_dir)
    label_store = LabelStore(db)
    corrections = label_store.get_corrections_map()

    if not corrections:
        raise ValueError(
            "No labeled data found. Use the Label Commute dashboard page "
            "to correct or confirm segment classifications first."
        )

    # Determine which commutes have labels
    labeled_commute_ids = set(cid for cid, _ in corrections.keys())
    if commute_ids:
        labeled_commute_ids = labeled_commute_ids.intersection(commute_ids)

    if not labeled_commute_ids:
        raise ValueError("No labeled commutes match the specified IDs.")

    # Load and prepare training data
    all_dfs = []
    for cid in sorted(labeled_commute_ids):
        try:
            points = store.get_commute_points(cid)
        except Exception:
            logger.warning(f"Could not load points for {cid}, skipping")
            continue

        if points.is_empty():
            continue

        # Extract features
        points = extract_point_features(points)

        # Apply label corrections to transport_mode
        if "transport_mode" in points.columns and "segment_id" in points.columns:
            modes = points["transport_mode"].to_list()
            seg_ids = points["segment_id"].to_list()
            for i, (mode, sid) in enumerate(zip(modes, seg_ids)):
                corrected = corrections.get((cid, sid))
                if corrected:
                    modes[i] = corrected
            points = points.with_columns(pl.Series("transport_mode", modes))

        all_dfs.append(points)

    if not all_dfs:
        raise ValueError("No valid training data after loading commutes.")

    training_df = pl.concat(all_dfs)
    logger.info(f"Training data: {len(training_df)} points from {len(all_dfs)} commutes")

    # Build feature matrix and labels
    features, labels = build_training_set(training_df)

    if features.is_empty():
        raise ValueError("No labeled points found in the training data.")

    logger.info(f"Feature matrix: {features.shape}, label distribution: "
                f"{labels.value_counts().to_dict()}")

    # Train
    model = BaselineModel()
    metrics = model.train(
        features, labels,
        max_depth=max_depth,
        test_fraction=test_fraction,
    )

    # Save
    if model_path is None:
        from src.config import DERIVED_DATA_DIR
        model_path = Path(derived_dir or DERIVED_DATA_DIR) / "model" / "baseline.json"

    model.save(model_path)

    return model, metrics


def evaluate_classifier_accuracy(
    db,
    derived_dir: str | Path | None = None,
) -> dict:
    """Compare current classifier output against user labels.

    Returns accuracy metrics showing how well the ensemble performs
    against human ground truth. Useful for tracking improvement over time.
    """
    store = DerivedStore(derived_dir)
    label_store = LabelStore(db)
    corrections = label_store.get_corrections_map()

    if not corrections:
        return {"error": "No labels found"}

    correct = 0
    total = 0
    confusion: dict[tuple[str, str], int] = {}

    labeled_commute_ids = set(cid for cid, _ in corrections.keys())

    for cid in sorted(labeled_commute_ids):
        try:
            points = store.get_commute_points(cid)
        except Exception:
            continue

        if points.is_empty() or "transport_mode" not in points.columns:
            continue

        modes = points["transport_mode"].to_list()
        seg_ids = points["segment_id"].to_list()

        for mode, sid in zip(modes, seg_ids):
            corrected = corrections.get((cid, sid))
            if corrected is None:
                continue

            total += 1
            if mode == corrected:
                correct += 1

            key = (mode or "none", corrected)
            confusion[key] = confusion.get(key, 0) + 1

    if total == 0:
        return {"error": "No matching points found"}

    return {
        "accuracy": round(correct / total, 4),
        "correct": correct,
        "total": total,
        "confusion": {f"{pred}->{actual}": count for (pred, actual), count in sorted(confusion.items())},
    }
