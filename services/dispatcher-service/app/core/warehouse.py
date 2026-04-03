from __future__ import annotations

from collections import defaultdict
from typing import Any


class WarehouseRegistry:
    """In-memory registry of warehouses and their routes.

    Data is loaded from the PostgreSQL warehouses table on initialisation
    and cached.  Call ``refresh`` to reload from the database.
    """

    def __init__(self) -> None:
        # warehouse_id -> list[route_id]
        self._warehouse_routes: dict[int, list[int]] = {}
        # route_id -> warehouse_id
        self._route_warehouse: dict[int, int] = {}
        # warehouse_id -> metadata dict
        self._warehouses: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_warehouse_routes(self, warehouse_id: int) -> list[int]:
        """Return route IDs belonging to *warehouse_id*."""
        return list(self._warehouse_routes.get(warehouse_id, []))

    def get_all_warehouses(self) -> list[dict]:
        """Return all warehouses with their route_count."""
        return [
            {
                "warehouse_id": wid,
                "route_count": len(self._warehouse_routes.get(wid, [])),
                **{k: v for k, v in meta.items() if k != "warehouse_id"},
            }
            for wid, meta in self._warehouses.items()
        ]

    def get_warehouse_for_route(self, route_id: int) -> int:
        """Return the warehouse_id that owns *route_id*.

        Raises ``KeyError`` if the route is not registered.
        """
        if route_id not in self._route_warehouse:
            raise KeyError(f"Route {route_id} not found in registry")
        return self._route_warehouse[route_id]

    # ------------------------------------------------------------------
    # Loader / refresh
    # ------------------------------------------------------------------

    def _load_rows(self, rows: list[dict[str, Any]]) -> None:
        """Populate the registry from a list of row dicts.

        Expected row shape::

            {"warehouse_id": int, "route_id": int, ...extra_meta...}
        """
        warehouse_routes: dict[int, list[int]] = defaultdict(list)
        route_warehouse: dict[int, int] = {}
        warehouses: dict[int, dict[str, Any]] = {}

        for row in rows:
            wid = int(row["warehouse_id"])
            rid = int(row["route_id"])
            warehouse_routes[wid].append(rid)
            route_warehouse[rid] = wid
            if wid not in warehouses:
                warehouses[wid] = {
                    k: v for k, v in row.items() if k != "route_id"
                }

        self._warehouse_routes = {
            wid: sorted(routes) for wid, routes in warehouse_routes.items()
        }
        self._route_warehouse = dict(route_warehouse)
        self._warehouses = dict(warehouses)

    async def refresh(self, db_session) -> None:
        """Reload warehouse / route data from the database.

        *db_session* is an async SQLAlchemy session (or connection) that
        supports ``execute``.
        """
        from sqlalchemy import text

        result = await db_session.execute(
            text(
                "SELECT w.warehouse_id, w.name, r.route_id "
                "FROM warehouses w "
                "JOIN routes r ON r.warehouse_id = w.warehouse_id "
                "ORDER BY w.warehouse_id, r.route_id"
            )
        )
        rows = [dict(row._mapping) for row in result.fetchall()]
        self._load_rows(rows)
