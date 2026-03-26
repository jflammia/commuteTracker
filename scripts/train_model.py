#!/usr/bin/env python3
"""Train the baseline transport mode classification model.

Usage:
    python scripts/train_model.py                          # Train on all labels
    python scripts/train_model.py --max-depth 8            # Limit tree depth
    python scripts/train_model.py --evaluate               # Just evaluate, don't train

Requires labeled commute data (use the Label Commute dashboard page).
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import DATABASE_URL, DERIVED_DATA_DIR
from src.storage.database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="Train baseline ML model")
    parser.add_argument("--max-depth", type=int, default=10, help="Max tree depth (default: 10)")
    parser.add_argument("--test-fraction", type=float, default=0.2, help="Hold-out fraction (default: 0.2)")
    parser.add_argument("--output", type=str, help="Model output path (default: derived/model/baseline.json)")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate current classifier accuracy against labels")
    args = parser.parse_args()

    db = Database(DATABASE_URL)
    db.create_tables()

    if args.evaluate:
        from src.ml.trainer import evaluate_classifier_accuracy
        results = evaluate_classifier_accuracy(db)
        print(json.dumps(results, indent=2))
        return

    from src.ml.trainer import train_from_labels

    try:
        model, metrics = train_from_labels(
            db,
            model_path=args.output,
            max_depth=args.max_depth,
            test_fraction=args.test_fraction,
        )
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"\nTraining complete!")
    print(f"  Accuracy: {metrics.accuracy:.1%}")
    print(f"  Samples:  {metrics.sample_count}")
    print(f"\nPer-class metrics:")
    for mode, stats in metrics.per_class.items():
        print(f"  {mode:12s}  precision={stats['precision']:.3f}  "
              f"recall={stats['recall']:.3f}  f1={stats['f1']:.3f}  "
              f"support={stats['support']}")

    print(f"\nTop features:")
    sorted_features = sorted(metrics.feature_importances.items(), key=lambda x: -x[1])
    for feat, imp in sorted_features[:5]:
        print(f"  {feat:30s}  {imp:.4f}")


if __name__ == "__main__":
    main()
