import math
from collections import defaultdict
from datetime import datetime


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
    def compute_adaptive_buffer(
        total_containers: float,
        min_buffer: float = 0.05,
        max_buffer: float = 0.25,
        scale_threshold: float = 50.0,
    ) -> float:
        """Adaptive buffer: higher buffer for small forecasts (more uncertain), lower for large.

        Uses a simple inverse scaling: buffer = max_buffer - (max_buffer - min_buffer) * min(total / threshold, 1.0)
        """
        if total_containers <= 0:
            return max_buffer
        ratio = min(total_containers / scale_threshold, 1.0)
        return max_buffer - (max_buffer - min_buffer) * ratio

    @staticmethod
    def generate_dispatch_requests(
        warehouse_id: int,
        aggregated: list[dict],
        config,
    ) -> list[dict]:
        requests = []
        for slot in aggregated:
            total = slot["total_containers"]

            # Use adaptive buffer if enabled
            if getattr(config, 'adaptive_buffer', False):
                buffer = DispatchCalculator.compute_adaptive_buffer(
                    total,
                    min_buffer=getattr(config, 'min_buffer_pct', 0.05),
                    max_buffer=getattr(config, 'max_buffer_pct', 0.25),
                )
            else:
                buffer = config.buffer_pct

            trucks_needed = DispatchCalculator.calculate_trucks(
                total_containers=total,
                capacity=config.truck_capacity,
                buffer_pct=buffer,
                min_trucks=config.min_trucks,
            )
            buffered = total * (1 + buffer)
            calculation = (
                f"ceil({total} * (1 + {buffer:.2f}) / {config.truck_capacity})"
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
                    "buffer_pct": buffer,
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
