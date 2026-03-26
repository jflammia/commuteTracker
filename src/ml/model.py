"""Baseline transport mode classification model.

Uses a decision tree (scikit-learn) as the simplest ML baseline.
The model can be trained on user-labeled data and used as an additional
classifier in the ensemble.

Design:
    - Trains on features from features.py + labels from LabelStore
    - Serialized to a JSON-friendly format (no pickle) for portability
    - Implements the TransportClassifier protocol for ensemble integration
    - Falls back gracefully if scikit-learn is not installed

No deep learning, no complex pipelines. This is the simplest thing
that could work as a starting point for ML-based classification.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from src.ml.features import FEATURE_COLUMNS, extract_point_features
from src.processing.classifiers.base import ModeScores

logger = logging.getLogger(__name__)

TRANSPORT_MODES = ["stationary", "waiting", "walking", "driving", "train"]


@dataclass
class ModelMetrics:
    """Training metrics for the baseline model."""

    accuracy: float = 0.0
    per_class: dict[str, dict[str, float]] = field(default_factory=dict)
    sample_count: int = 0
    feature_importances: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "accuracy": self.accuracy,
            "per_class": self.per_class,
            "sample_count": self.sample_count,
            "feature_importances": self.feature_importances,
        }


class BaselineModel:
    """Decision tree classifier for transport mode.

    Implements TransportClassifier protocol so it can be added to the ensemble.
    """

    def __init__(self):
        self._tree = None
        self._classes: list[str] = []
        self._metrics: ModelMetrics | None = None
        self._is_trained = False

    @property
    def name(self) -> str:
        return "ml_baseline"

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    @property
    def metrics(self) -> ModelMetrics | None:
        return self._metrics

    def train(
        self,
        features: pl.DataFrame,
        labels: pl.Series,
        max_depth: int = 10,
        test_fraction: float = 0.2,
    ) -> ModelMetrics:
        """Train the model on labeled feature data.

        Args:
            features: DataFrame with FEATURE_COLUMNS.
            labels: Series of transport mode strings.
            max_depth: Maximum tree depth (controls overfitting).
            test_fraction: Fraction of data to hold out for evaluation.

        Returns:
            ModelMetrics with accuracy and per-class scores.
        """
        try:
            from sklearn.tree import DecisionTreeClassifier
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import accuracy_score, classification_report
        except ImportError:
            raise ImportError(
                "scikit-learn is required for ML training. "
                "Install it with: pip install scikit-learn"
            )

        X = features.to_pandas()
        y = labels.to_list()

        if len(X) < 10:
            raise ValueError(f"Need at least 10 labeled samples, got {len(X)}")

        # Split
        if test_fraction > 0 and len(X) >= 20:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=test_fraction, random_state=42, stratify=y
            )
        else:
            X_train, X_test, y_train, y_test = X, X, y, y

        # Train
        self._tree = DecisionTreeClassifier(
            max_depth=max_depth,
            random_state=42,
            class_weight="balanced",
        )
        self._tree.fit(X_train, y_train)
        self._classes = list(self._tree.classes_)

        # Evaluate
        y_pred = self._tree.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)

        report = classification_report(y_test, y_pred, output_dict=True, zero_division=0)
        per_class = {}
        for mode in TRANSPORT_MODES:
            if mode in report:
                per_class[mode] = {
                    "precision": round(report[mode]["precision"], 3),
                    "recall": round(report[mode]["recall"], 3),
                    "f1": round(report[mode]["f1-score"], 3),
                    "support": int(report[mode]["support"]),
                }

        # Feature importances
        importances = {}
        for feat, imp in zip(FEATURE_COLUMNS, self._tree.feature_importances_):
            importances[feat] = round(float(imp), 4)

        self._metrics = ModelMetrics(
            accuracy=round(accuracy, 4),
            per_class=per_class,
            sample_count=len(X),
            feature_importances=importances,
        )
        self._is_trained = True

        logger.info(f"Model trained: accuracy={accuracy:.3f}, samples={len(X)}")
        return self._metrics

    def score(self, df: pl.DataFrame) -> list[ModeScores]:
        """Score each row using the trained model.

        Implements TransportClassifier protocol.
        If not trained, returns empty scores (no signal).
        """
        if not self._is_trained or self._tree is None:
            return [ModeScores() for _ in range(len(df))]

        # Extract features if not already present
        if "speed_cv_w10" not in df.columns:
            df = extract_point_features(df)

        # Select feature columns, filling missing with 0
        feature_cols = []
        for col in FEATURE_COLUMNS:
            if col in df.columns:
                feature_cols.append(df[col])
            else:
                feature_cols.append(pl.Series(col, [0.0] * len(df)))

        X = pl.DataFrame(feature_cols).to_pandas()

        # Get probability predictions
        probas = self._tree.predict_proba(X)
        results = []
        for row_proba in probas:
            scores = ModeScores()
            for cls, prob in zip(self._classes, row_proba):
                if hasattr(scores, cls):
                    setattr(scores, cls, float(prob))
            results.append(scores)

        return results

    def predict(self, df: pl.DataFrame) -> list[str]:
        """Predict transport mode for each row."""
        if not self._is_trained or self._tree is None:
            return ["unknown"] * len(df)

        if "speed_cv_w10" not in df.columns:
            df = extract_point_features(df)

        feature_cols = []
        for col in FEATURE_COLUMNS:
            if col in df.columns:
                feature_cols.append(df[col])
            else:
                feature_cols.append(pl.Series(col, [0.0] * len(df)))

        X = pl.DataFrame(feature_cols).to_pandas()
        return list(self._tree.predict(X))

    def save(self, path: str | Path) -> None:
        """Save model: joblib for the tree, JSON sidecar for metrics.

        Writes two files:
            path.joblib  - scikit-learn model (for prediction)
            path.json    - metrics and metadata (human-readable, portable)
        """
        if not self._is_trained or self._tree is None:
            raise ValueError("Model not trained yet")

        import joblib

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Save the sklearn model
        joblib_path = path.with_suffix(".joblib")
        joblib.dump({"tree": self._tree, "classes": self._classes}, joblib_path)

        # Save metrics as JSON sidecar
        meta_path = path.with_suffix(".json")
        meta = {
            "version": 1,
            "classes": self._classes,
            "feature_names": FEATURE_COLUMNS,
            "metrics": self._metrics.to_dict() if self._metrics else None,
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        logger.info(f"Model saved to {joblib_path} + {meta_path}")

    def load(self, path: str | Path) -> None:
        """Load model from joblib + JSON sidecar files."""
        try:
            import joblib
        except ImportError:
            raise ImportError("joblib required to load model (comes with scikit-learn)")

        path = Path(path)
        joblib_path = path.with_suffix(".joblib")

        data = joblib.load(joblib_path)
        self._tree = data["tree"]
        self._classes = data["classes"]

        # Load metrics from JSON sidecar if available
        meta_path = path.with_suffix(".json")
        if meta_path.exists():
            with open(meta_path) as f:
                meta = json.load(f)
            if meta.get("metrics"):
                self._metrics = ModelMetrics(**meta["metrics"])

        self._is_trained = True
        logger.info(f"Model loaded from {joblib_path}")
