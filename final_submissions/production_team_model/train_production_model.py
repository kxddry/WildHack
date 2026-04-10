#!/usr/bin/env python3
"""Train a hybrid LightGBM model from a local parquet file (no Postgres needed).

Uses the same pipeline as services/retraining-service but reads from disk.
"""

import sys
from pathlib import Path

# Add shared package and retraining-service to path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "shared"))
sys.path.insert(0, str(REPO_ROOT / "services" / "retraining-service"))

import pandas as pd

TRAIN_PATH = REPO_ROOT / "Data" / "raw" / "train_team_track.parquet"
OUTPUT_DIR = Path(__file__).resolve().parent / "models"


def main() -> None:
    if not TRAIN_PATH.exists():
        print(f"ERROR: Training data not found: {TRAIN_PATH}")
        sys.exit(1)

    print(f"Loading {TRAIN_PATH} ...")
    raw_df = pd.read_parquet(TRAIN_PATH)
    print(f"  {len(raw_df)} rows, {len(raw_df.columns)} columns")

    # Use the retraining-service trainer directly
    from app.core.trainer import ModelTrainer

    trainer = ModelTrainer()
    envelope, metrics = trainer.train_from_dataframe(raw_df)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model_path = trainer.save_model(envelope, "local_hybrid", metrics)

    print(f"\nModel saved to {model_path}")
    print(f"  WAPE:     {metrics['wape']:.4f}")
    print(f"  RBias:    {metrics['rbias']:.4f}")
    print(f"  Combined: {metrics['combined_score']:.4f}")
    print(f"  Submodels: {list(metrics.get('submodels', {}).keys())}")


if __name__ == "__main__":
    main()
