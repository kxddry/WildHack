"""Unit tests for DispatchCalculator."""

import math
from datetime import timedelta


from app.core.dispatcher import DispatchCalculator


# ---------------------------------------------------------------------------
# calculate_trucks
# ---------------------------------------------------------------------------

class TestCalculateTrucks:
    def test_calculate_trucks_normal(self):
        # 100 containers, cap=33, buffer=10% → ceil(100*1.1/33) = ceil(110/33) = ceil(3.333) = 4
        result = DispatchCalculator.calculate_trucks(
            total_containers=100,
            capacity=33,
            buffer_pct=0.10,
            min_trucks=1,
        )
        assert result == 4

    def test_calculate_trucks_near_capacity(self):
        # 30 containers, cap=33, buffer=10% → ceil(30*1.1/33) = ceil(33/33) = 1
        result = DispatchCalculator.calculate_trucks(
            total_containers=30,
            capacity=33,
            buffer_pct=0.10,
            min_trucks=1,
        )
        assert result == 1

    def test_calculate_trucks_zero(self):
        result = DispatchCalculator.calculate_trucks(
            total_containers=0,
            capacity=33,
            buffer_pct=0.10,
            min_trucks=1,
        )
        assert result == 0

    def test_calculate_trucks_one_container(self):
        # 1 container → ceil(1*1.1/33) = 1, which equals min_trucks
        result = DispatchCalculator.calculate_trucks(
            total_containers=1,
            capacity=33,
            buffer_pct=0.10,
            min_trucks=1,
        )
        assert result == 1

    def test_calculate_trucks_large_volume(self):
        # 1000 containers, cap=33, buffer=10% → ceil(1000*1.1/33) = ceil(1100/33) = ceil(33.333) = 34
        result = DispatchCalculator.calculate_trucks(
            total_containers=1000,
            capacity=33,
            buffer_pct=0.10,
            min_trucks=1,
        )
        expected = math.ceil(1000 * 1.10 / 33)
        assert result == expected

    def test_calculate_trucks_no_buffer(self):
        # buffer=0 → ceil(total/cap)
        result = DispatchCalculator.calculate_trucks(
            total_containers=99,
            capacity=33,
            buffer_pct=0.0,
            min_trucks=1,
        )
        assert result == math.ceil(99 / 33)

    def test_calculate_trucks_min_trucks_enforced(self):
        # Result below min_trucks should be raised to min_trucks
        result = DispatchCalculator.calculate_trucks(
            total_containers=5,
            capacity=33,
            buffer_pct=0.0,
            min_trucks=3,
        )
        assert result == 3

    def test_calculate_trucks_exact_capacity(self):
        # Exactly fills one truck with buffer → still 1
        result = DispatchCalculator.calculate_trucks(
            total_containers=30,
            capacity=33,
            buffer_pct=0.0,
            min_trucks=1,
        )
        assert result == 1


# ---------------------------------------------------------------------------
# aggregate_forecasts_by_warehouse
# ---------------------------------------------------------------------------

class TestAggregateForecasts:
    def test_aggregate_forecasts_single_slot(self, base_dt):
        end_dt = base_dt + timedelta(hours=2)
        forecasts = [
            {"time_slot_start": base_dt, "time_slot_end": end_dt, "total_containers": 50.0},
            {"time_slot_start": base_dt, "time_slot_end": end_dt, "total_containers": 30.0},
        ]
        result = DispatchCalculator.aggregate_forecasts_by_warehouse(forecasts)
        assert len(result) == 1
        assert result[0]["total_containers"] == 80.0
        assert result[0]["time_slot_start"] == base_dt
        assert result[0]["time_slot_end"] == end_dt

    def test_aggregate_forecasts_multiple_slots(self, base_dt):
        slot1_end = base_dt + timedelta(hours=2)
        slot2_start = slot1_end
        slot2_end = slot2_start + timedelta(hours=2)
        forecasts = [
            {"time_slot_start": base_dt, "time_slot_end": slot1_end, "total_containers": 40.0},
            {"time_slot_start": slot2_start, "time_slot_end": slot2_end, "total_containers": 60.0},
            {"time_slot_start": base_dt, "time_slot_end": slot1_end, "total_containers": 10.0},
        ]
        result = DispatchCalculator.aggregate_forecasts_by_warehouse(forecasts)
        assert len(result) == 2
        # Sorted by start time
        assert result[0]["time_slot_start"] == base_dt
        assert result[0]["total_containers"] == 50.0
        assert result[1]["time_slot_start"] == slot2_start
        assert result[1]["total_containers"] == 60.0

    def test_aggregate_forecasts_missing_containers_defaults_zero(self, base_dt):
        end_dt = base_dt + timedelta(hours=2)
        forecasts = [
            {"time_slot_start": base_dt, "time_slot_end": end_dt},  # no total_containers
        ]
        result = DispatchCalculator.aggregate_forecasts_by_warehouse(forecasts)
        assert result[0]["total_containers"] == 0.0

    def test_aggregate_forecasts_empty(self):
        result = DispatchCalculator.aggregate_forecasts_by_warehouse([])
        assert result == []

    def test_aggregate_forecasts_sorted_by_start(self, base_dt):
        slot_a = base_dt + timedelta(hours=4)
        slot_b = base_dt
        forecasts = [
            {"time_slot_start": slot_a, "time_slot_end": slot_a + timedelta(hours=2), "total_containers": 10.0},
            {"time_slot_start": slot_b, "time_slot_end": slot_b + timedelta(hours=2), "total_containers": 20.0},
        ]
        result = DispatchCalculator.aggregate_forecasts_by_warehouse(forecasts)
        assert result[0]["time_slot_start"] == slot_b
        assert result[1]["time_slot_start"] == slot_a


# ---------------------------------------------------------------------------
# generate_dispatch_requests
# ---------------------------------------------------------------------------

class TestGenerateDispatchRequests:
    def test_generate_dispatch_requests(self, dispatch_config, base_dt):
        end_dt = base_dt + timedelta(hours=2)
        aggregated = [
            {"time_slot_start": base_dt, "time_slot_end": end_dt, "total_containers": 100.0},
        ]
        requests = DispatchCalculator.generate_dispatch_requests(
            warehouse_id=5,
            aggregated=aggregated,
            config=dispatch_config,
        )
        assert len(requests) == 1
        req = requests[0]
        assert req["warehouse_id"] == 5
        assert req["time_slot_start"] == base_dt
        assert req["time_slot_end"] == end_dt
        assert req["total_containers"] == 100.0
        assert req["truck_capacity"] == dispatch_config.truck_capacity
        assert req["buffer_pct"] == dispatch_config.buffer_pct
        assert req["trucks_needed"] == 4  # ceil(100*1.1/33) = 4
        assert req["status"] == "planned"
        assert "calculation" in req

    def test_generate_dispatch_requests_zero_containers(self, dispatch_config, base_dt):
        end_dt = base_dt + timedelta(hours=2)
        aggregated = [
            {"time_slot_start": base_dt, "time_slot_end": end_dt, "total_containers": 0.0},
        ]
        requests = DispatchCalculator.generate_dispatch_requests(
            warehouse_id=1,
            aggregated=aggregated,
            config=dispatch_config,
        )
        assert requests[0]["trucks_needed"] == 0

    def test_generate_dispatch_requests_multiple_slots(self, dispatch_config, base_dt):
        slots = [
            {"time_slot_start": base_dt + timedelta(hours=i*2),
             "time_slot_end": base_dt + timedelta(hours=i*2 + 2),
             "total_containers": 50.0 * (i + 1)}
            for i in range(3)
        ]
        requests = DispatchCalculator.generate_dispatch_requests(
            warehouse_id=2,
            aggregated=slots,
            config=dispatch_config,
        )
        assert len(requests) == 3
        for req in requests:
            assert req["warehouse_id"] == 2
            assert req["status"] == "planned"


# ---------------------------------------------------------------------------
# create_full_dispatch
# ---------------------------------------------------------------------------

class TestCreateFullDispatch:
    def test_create_full_dispatch(self, dispatch_config, base_dt):
        end_dt = base_dt + timedelta(hours=2)
        forecasts = [
            {"time_slot_start": base_dt, "time_slot_end": end_dt, "total_containers": 66.0},
            {"time_slot_start": base_dt, "time_slot_end": end_dt, "total_containers": 34.0},
        ]
        result = DispatchCalculator.create_full_dispatch(
            warehouse_id=7,
            forecasts=forecasts,
            config=dispatch_config,
        )
        assert result["warehouse_id"] == 7
        assert len(result["dispatch_requests"]) == 1
        # 100 containers aggregated: ceil(100*1.1/33) = 4
        assert result["dispatch_requests"][0]["trucks_needed"] == 4
        assert result["dispatch_requests"][0]["total_containers"] == 100.0
        assert result["config"]["truck_capacity"] == dispatch_config.truck_capacity
        assert result["config"]["buffer_pct"] == dispatch_config.buffer_pct
        assert result["config"]["min_trucks"] == dispatch_config.min_trucks

    def test_create_full_dispatch_empty_forecasts(self, dispatch_config):
        result = DispatchCalculator.create_full_dispatch(
            warehouse_id=1,
            forecasts=[],
            config=dispatch_config,
        )
        assert result["warehouse_id"] == 1
        assert result["dispatch_requests"] == []

    def test_create_full_dispatch_config_fields(self, dispatch_config, base_dt):
        end_dt = base_dt + timedelta(hours=2)
        forecasts = [
            {"time_slot_start": base_dt, "time_slot_end": end_dt, "total_containers": 10.0},
        ]
        result = DispatchCalculator.create_full_dispatch(
            warehouse_id=3,
            forecasts=forecasts,
            config=dispatch_config,
        )
        config_out = result["config"]
        assert "truck_capacity" in config_out
        assert "buffer_pct" in config_out
        assert "min_trucks" in config_out


# ---------------------------------------------------------------------------
# apply_antiflap_filter (PRD §7.3 — antiflapping)
# ---------------------------------------------------------------------------


def _slot(warehouse_id: int, base_dt, hour_offset: int, trucks: int) -> dict:
    start = base_dt + timedelta(hours=hour_offset)
    end = start + timedelta(hours=2)
    return {
        "warehouse_id": warehouse_id,
        "time_slot_start": start,
        "time_slot_end": end,
        "trucks_needed": trucks,
        "total_containers": float(trucks) * 33,
        "truck_capacity": 33,
        "buffer_pct": 0.1,
        "calculation": "test",
        "status": "planned",
    }


class TestApplyAntiflapFilter:
    def test_no_previous_request_always_saved(self, base_dt):
        new_req = _slot(1, base_dt, 0, trucks=4)
        to_save, to_skip = DispatchCalculator.apply_antiflap_filter(
            new_requests=[new_req], existing_trucks={}
        )
        assert len(to_save) == 1
        assert to_skip == []

    def test_skips_when_delta_is_zero(self, base_dt):
        new_req = _slot(1, base_dt, 0, trucks=5)
        existing = {(1, new_req["time_slot_start"], new_req["time_slot_end"]): 5}
        to_save, to_skip = DispatchCalculator.apply_antiflap_filter(
            new_requests=[new_req], existing_trucks=existing
        )
        assert to_save == []
        assert len(to_skip) == 1

    def test_skips_when_delta_is_one(self, base_dt):
        new_req = _slot(1, base_dt, 0, trucks=5)
        existing = {(1, new_req["time_slot_start"], new_req["time_slot_end"]): 4}
        to_save, to_skip = DispatchCalculator.apply_antiflap_filter(
            new_requests=[new_req], existing_trucks=existing
        )
        assert to_save == []
        assert len(to_skip) == 1

    def test_skips_when_delta_is_negative_one(self, base_dt):
        new_req = _slot(1, base_dt, 0, trucks=5)
        existing = {(1, new_req["time_slot_start"], new_req["time_slot_end"]): 6}
        to_save, to_skip = DispatchCalculator.apply_antiflap_filter(
            new_requests=[new_req], existing_trucks=existing
        )
        assert to_save == []
        assert len(to_skip) == 1

    def test_saves_when_delta_exceeds_threshold(self, base_dt):
        new_req = _slot(1, base_dt, 0, trucks=7)
        existing = {(1, new_req["time_slot_start"], new_req["time_slot_end"]): 4}
        to_save, to_skip = DispatchCalculator.apply_antiflap_filter(
            new_requests=[new_req], existing_trucks=existing
        )
        assert len(to_save) == 1
        assert to_skip == []

    def test_mixed_batch_partitions_correctly(self, base_dt):
        # First slot: previously 4, new 5 → skip (|delta|=1)
        # Second slot: previously 4, new 8 → save (|delta|=4)
        # Third slot: no previous → save
        first = _slot(1, base_dt, 0, trucks=5)
        second = _slot(1, base_dt, 2, trucks=8)
        third = _slot(2, base_dt, 4, trucks=3)
        existing = {
            (1, first["time_slot_start"], first["time_slot_end"]): 4,
            (1, second["time_slot_start"], second["time_slot_end"]): 4,
        }
        to_save, to_skip = DispatchCalculator.apply_antiflap_filter(
            new_requests=[first, second, third], existing_trucks=existing
        )
        saved_keys = {(r["warehouse_id"], r["time_slot_start"]) for r in to_save}
        skipped_keys = {(r["warehouse_id"], r["time_slot_start"]) for r in to_skip}
        assert (1, second["time_slot_start"]) in saved_keys
        assert (2, third["time_slot_start"]) in saved_keys
        assert (1, first["time_slot_start"]) in skipped_keys

    def test_threshold_override(self, base_dt):
        # With threshold=0, even |delta|=1 should be saved.
        new_req = _slot(1, base_dt, 0, trucks=5)
        existing = {(1, new_req["time_slot_start"], new_req["time_slot_end"]): 4}
        to_save, to_skip = DispatchCalculator.apply_antiflap_filter(
            new_requests=[new_req], existing_trucks=existing, threshold=0
        )
        assert len(to_save) == 1
        assert to_skip == []
