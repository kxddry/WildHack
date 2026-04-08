"""Unit tests for the promotion policy decision logic.

``_apply_policy`` is the pure-function core of the shared orchestration —
it maps (policy, is_better) to a pending promotion status without any
HTTP calls. Covering it directly keeps the tests hermetic and fast.
"""

from __future__ import annotations

from app.core.orchestration import PromotionPolicy, _apply_policy


class TestApplyPolicy:
    def test_shadow_if_better_promotes_when_challenger_wins(self):
        assert (
            _apply_policy(PromotionPolicy.SHADOW_IF_BETTER, is_better=True)
            == "needs_shadow"
        )

    def test_shadow_if_better_skips_when_challenger_loses(self):
        assert (
            _apply_policy(PromotionPolicy.SHADOW_IF_BETTER, is_better=False)
            == "skipped"
        )

    def test_force_primary_ignores_is_better_true(self):
        """force_primary always promotes — no A/B gate."""
        assert (
            _apply_policy(PromotionPolicy.FORCE_PRIMARY, is_better=True)
            == "needs_primary"
        )

    def test_force_primary_ignores_is_better_false(self):
        """Even when the challenger is objectively worse, force_primary
        still promotes. This is the whole point of the upload-driven
        refresh: the uploaded data is authoritative for the current
        snapshot, so the model trained on it must be live."""
        assert (
            _apply_policy(PromotionPolicy.FORCE_PRIMARY, is_better=False)
            == "needs_primary"
        )


class TestPromotionPolicyEnum:
    def test_enum_values_stable(self):
        # String values are persisted in retrain_history.details_json.
        # Changing them would break existing audit rows.
        assert PromotionPolicy.SHADOW_IF_BETTER.value == "shadow_if_better"
        assert PromotionPolicy.FORCE_PRIMARY.value == "force_primary"
