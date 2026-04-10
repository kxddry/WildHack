#!/usr/bin/env python3
"""Wrap the team hybrid LightGBM artifact into the production envelope format.

Reads final_submissions/team/models/lgb_hybrid_mae_full.pkl (a dict of 6
LGBMRegressor instances), extracts per-submodel feature lists from
LGBMRegressor.feature_name_, and writes the envelope to
final_submissions/production_team_model/models/model.pkl.

No DatasetBuilder run needed — feature lists come directly from the fitted models.
"""

import sys
from pathlib import Path

import joblib

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_PATH = REPO_ROOT / "final_submissions" / "team" / "models" / "lgb_hybrid_mae_full.pkl"
OUTPUT_DIR = REPO_ROOT / "final_submissions" / "production_team_model" / "models"
OUTPUT_PATH = OUTPUT_DIR / "model.pkl"

CAT_COLS = [
    "office_from_id", "route_id", "dow", "pod",
    "is_hooliday", "slot", "horizon_step",
]


def main() -> None:
    if not INPUT_PATH.exists():
        print(f"ERROR: Input not found: {INPUT_PATH}")
        sys.exit(1)

    print(f"Loading {INPUT_PATH} ...")
    raw = joblib.load(INPUT_PATH)

    if not isinstance(raw, dict):
        print(f"ERROR: Expected dict, got {type(raw).__name__}")
        sys.exit(1)

    expected_keys = {"step_1", "step_2", "step_3", "step_4", "step_5", "global_6_10"}
    if not expected_keys.issubset(raw.keys()):
        print(f"ERROR: Missing keys. Expected {expected_keys}, got {set(raw.keys())}")
        sys.exit(1)

    # Extract feature lists from fitted models
    # step_1..step_5 were trained WITHOUT horizon_step (02_train_lgb.py:83)
    # global_6_10 was trained WITH horizon_step
    feat_cols_step = list(raw["step_1"].feature_name_)
    feat_cols_global = list(raw["global_6_10"].feature_name_)

    print(f"  feat_cols_step:   {len(feat_cols_step)} features")
    print(f"  feat_cols_global: {len(feat_cols_global)} features")
    print(f"  delta (should be 1 = horizon_step): {len(feat_cols_global) - len(feat_cols_step)}")

    # Verify all step models have the same feature set
    for step in range(1, 6):
        key = f"step_{step}"
        step_feats = list(raw[key].feature_name_)
        if step_feats != feat_cols_step:
            print(f"WARNING: {key} has different features than step_1 ({len(step_feats)} vs {len(feat_cols_step)})")

    # Build submodel metadata
    submodels = {}
    for name, model in sorted(raw.items()):
        submodels[name] = {
            "n_features": model.n_features_,
            "n_estimators": model.n_estimators_,
            "best_iteration": getattr(model, "best_iteration_", model.n_estimators_),
        }

    envelope = {
        "models": raw,
        "feat_cols_step": feat_cols_step,
        "feat_cols_global": feat_cols_global,
        "cat_cols": CAT_COLS,
        "metadata": {
            "model_version": "team_hybrid_wrapped",
            "training_date": "2026-04-08T00:00:00",  # approximate date of team training
            "combined_score": None,  # unknown — this was the submission model
            "wape": None,
            "rbias": None,
            "feature_count": len(feat_cols_global),
            "submodels": submodels,
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(envelope, OUTPUT_PATH)
    print(f"\nWrote envelope to {OUTPUT_PATH}")
    print(f"  Size: {OUTPUT_PATH.stat().st_size / 1e6:.1f} MB")

    # Safety check: round-trip load
    print("\nSafety check: round-trip load...")
    loaded = joblib.load(OUTPUT_PATH)
    assert isinstance(loaded, dict), "Round-trip failed: not a dict"
    assert "models" in loaded, "Round-trip failed: no 'models' key"
    assert "feat_cols_step" in loaded, "Round-trip failed: no 'feat_cols_step' key"
    assert "feat_cols_global" in loaded, "Round-trip failed: no 'feat_cols_global' key"
    assert len(loaded["models"]) == 6, f"Round-trip failed: {len(loaded['models'])} models, expected 6"
    print("  Round-trip OK")
    print("\nDone!")


if __name__ == "__main__":
    main()
