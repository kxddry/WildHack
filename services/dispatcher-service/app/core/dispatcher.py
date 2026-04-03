import math
from collections import defaultdict
from datetime import datetime, timezone


class DispatchCalculator:

    @staticmethod
    def calculate_trucks(
        total_containers: float,
        capacity: int,
        buffer_pct: float,
        min_trucks: int,
    ) -> int:
        if total_containers == 0:
            return 0
        result = math.ceil(total_containers * (1 + buffer_pct) / capacity)
        if result < min_trucks:
            return min_trucks
        return result

    @staticmethod
    def aggregate_forecasts_by_warehouse(
        forecasts: list[dict],
        warehouse_id: int,
    ) -> list[dict]:
        slots: dict[tuple[datetime, datetime], float] = defaultdict(float)

        for forecast in forecasts:
            slot_start: datetime = forecast["time_slot_start"]
            slot_end: datetime = forecast["time_slot_end"]
            containers: float = forecast.get("total_containers", 0.0)
            slots[(slot_start, slot_end)] += containers

        return [
            {
                "time_slot_start": slot_start,
                "time_slot_end": slot_end,
                "total_containers": total,
            }
            for (slot_start, slot_end), total in sorted(slots.items(), key=lambda kv: kv[0][0])
        ]

    @staticmethod
    def generate_dispatch_requests(
        warehouse_id: int,
        aggregated: list[dict],
        config,
    ) -> list[dict]:
        requests = []
        for slot in aggregated:
            total = slot["total_containers"]
            trucks_needed = DispatchCalculator.calculate_trucks(
                total_containers=total,
                capacity=config.truck_capacity,
                buffer_pct=config.buffer_pct,
                min_trucks=config.min_trucks,
            )
            buffered = total * (1 + config.buffer_pct)
            calculation = (
                f"ceil({total} * (1 + {config.buffer_pct}) / {config.truck_capacity})"
                f" = ceil({buffered:.4f} / {config.truck_capacity})"
                f" = {trucks_needed}"
            )
            requests.append(
                {
                    "warehouse_id": warehouse_id,
                    "time_slot_start": slot["time_slot_start"],
                    "time_slot_end": slot["time_slot_end"],
                    "total_containers": total,
                    "truck_capacity": config.truck_capacity,
                    "buffer_pct": config.buffer_pct,
                    "trucks_needed": trucks_needed,
                    "calculation": calculation,
                    "status": "planned",
                }
            )
        return requests

    @staticmethod
    def create_full_dispatch(
        warehouse_id: int,
        forecasts: list[dict],
        config,
    ) -> dict:
        aggregated = DispatchCalculator.aggregate_forecasts_by_warehouse(
            forecasts=forecasts,
            warehouse_id=warehouse_id,
        )
        dispatch_requests = DispatchCalculator.generate_dispatch_requests(
            warehouse_id=warehouse_id,
            aggregated=aggregated,
            config=config,
        )
        return {
            "warehouse_id": warehouse_id,
            "dispatch_requests": dispatch_requests,
            "config": {
                "truck_capacity": config.truck_capacity,
                "buffer_pct": config.buffer_pct,
                "min_trucks": config.min_trucks,
            },
        }
