"use client";

import { useEffect, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { TrendingUp, GitBranch, Box, Tag } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { KpiCard } from "@/components/kpi-card";
import { TOOLTIP_STYLE, AXIS_STYLE, CHART_COLORS } from "@/lib/chart-theme";
import type { Warehouse, Forecast, ForecastStep } from "@/lib/types";

interface ChartPoint {
  timestamp: string;
  /** Epoch ms for sorting — not displayed */
  _sortKey: number;
  [routeKey: string]: number | string;
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function buildChartData(forecasts: Forecast[]): {
  data: ChartPoint[];
  routeIds: number[];
} {
  if (forecasts.length === 0) return { data: [], routeIds: [] };

  // Find the latest anchor_ts and only keep forecasts from the same
  // prediction batch (within 1 hour of the most recent anchor).
  // A 24-hour window mixes separate model runs whose forecast windows
  // don't overlap, producing a gap in the chart.
  const maxAnchorMs = Math.max(
    ...forecasts.map((f) => new Date(f.anchor_ts).getTime())
  );
  const batchWindowMs = 60 * 60 * 1000; // 1 hour
  const recentForecasts = forecasts.filter(
    (f) => maxAnchorMs - new Date(f.anchor_ts).getTime() < batchWindowMs
  );

  // Use the most recent forecast per route to avoid duplicate sparse data
  const latestByRoute = new Map<number, Forecast>();
  for (const f of recentForecasts) {
    const prev = latestByRoute.get(f.route_id);
    if (!prev || new Date(f.anchor_ts) > new Date(prev.anchor_ts)) {
      latestByRoute.set(f.route_id, f);
    }
  }

  const routeIds = [...latestByRoute.keys()].sort((a, b) => a - b);
  const pointsMap: Record<string, ChartPoint> = {};

  for (const forecast of latestByRoute.values()) {
    const steps: ForecastStep[] = Array.isArray(forecast.forecasts)
      ? (forecast.forecasts as ForecastStep[])
      : [];
    for (const step of steps) {
      const date = new Date(step.timestamp);
      const epochMs = date.getTime();
      const label = formatTime(date);
      const mapKey = String(epochMs);
      if (!pointsMap[mapKey]) {
        pointsMap[mapKey] = { timestamp: label, _sortKey: epochMs };
      }
      const key = `route_${forecast.route_id}`;
      if (pointsMap[mapKey][key] === undefined) {
        pointsMap[mapKey][key] = step.predicted_value;
      }
    }
  }

  const data = Object.values(pointsMap).sort((a, b) => a._sortKey - b._sortKey);

  return { data, routeIds };
}

export default function ForecastsPage() {
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [selectedWarehouse, setSelectedWarehouse] = useState<string>("");
  const [forecasts, setForecasts] = useState<Forecast[]>([]);
  const [loadingWarehouses, setLoadingWarehouses] = useState(true);
  const [loadingForecasts, setLoadingForecasts] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/db/warehouses")
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        const ws: Warehouse[] = data.warehouses ?? [];
        setWarehouses(ws);
        if (ws.length > 0) setSelectedWarehouse(String(ws[0].warehouse_id));
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingWarehouses(false));
  }, []);

  useEffect(() => {
    if (!selectedWarehouse) return;
    setLoadingForecasts(true);
    setError(null);
    fetch(`/api/db/forecasts?warehouse_id=${selectedWarehouse}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        setForecasts(data.forecasts ?? []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingForecasts(false));
  }, [selectedWarehouse]);

  const uniqueRoutes = [...new Set(forecasts.map((f) => f.route_id))].length;
  const avgContainers =
    forecasts.length === 0
      ? 0
      : (
          forecasts
            .flatMap((f) =>
              Array.isArray(f.forecasts)
                ? (f.forecasts as ForecastStep[]).map((s) => s.predicted_value)
                : []
            )
            .reduce((s, v) => s + v, 0) /
          Math.max(
            1,
            forecasts.flatMap((f) =>
              Array.isArray(f.forecasts) ? (f.forecasts as ForecastStep[]) : []
            ).length
          )
        ).toFixed(1);
  const modelVersion =
    forecasts.length > 0 ? forecasts[0].model_version : "—";

  const { data: chartData, routeIds } = buildChartData(forecasts);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Forecasts</h1>
          <p className="text-muted-foreground text-sm mt-1">
            ML demand forecasts by warehouse and route
          </p>
        </div>
        <div className="w-52">
          {loadingWarehouses ? (
            <div className="h-9 bg-muted rounded animate-pulse" />
          ) : (
            <Select
              value={selectedWarehouse}
              onValueChange={setSelectedWarehouse}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select warehouse" />
              </SelectTrigger>
              <SelectContent>
                {warehouses.map((w) => (
                  <SelectItem
                    key={w.warehouse_id}
                    value={String(w.warehouse_id)}
                  >
                    {w.name ?? `Warehouse ${w.warehouse_id}`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      </div>

      {error && <p className="text-red-500 text-sm">Error: {error}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
        <KpiCard
          title="Total Forecasts"
          value={forecasts.length}
          icon={TrendingUp}
        />
        <KpiCard
          title="Unique Routes"
          value={uniqueRoutes}
          icon={GitBranch}
        />
        <KpiCard
          title="Avg Predicted Containers"
          value={avgContainers}
          icon={Box}
        />
        <KpiCard title="Model Version" value={modelVersion} icon={Tag} />
      </div>

      <Card className="overflow-hidden">
        <CardHeader>
          <CardTitle>Predicted Demand by Route</CardTitle>
        </CardHeader>
        <CardContent className="pr-2">
          {loadingForecasts ? (
            <div className="h-64 flex items-center justify-center text-muted-foreground">
              Loading...
            </div>
          ) : chartData.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-muted-foreground">
              No forecast data available
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart
                data={chartData}
                margin={{ top: 4, right: 16, left: 0, bottom: 4 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke={AXIS_STYLE.stroke}
                />
                <XAxis
                  dataKey="timestamp"
                  tick={{ fill: AXIS_STYLE.fill, fontSize: 11 }}
                />
                <YAxis
                  tick={{ fill: AXIS_STYLE.fill, fontSize: 11 }}
                  allowDecimals={false}
                />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Legend
                  wrapperStyle={{ fontSize: 12, color: AXIS_STYLE.fill }}
                />
                {routeIds.map((routeId, idx) => (
                  <Line
                    key={routeId}
                    type="monotone"
                    dataKey={`route_${routeId}`}
                    name={`Route ${routeId}`}
                    stroke={CHART_COLORS[idx % CHART_COLORS.length]}
                    dot={false}
                    strokeWidth={2}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Forecast Records</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Route ID</TableHead>
                <TableHead>Anchor Time</TableHead>
                <TableHead>Model Version</TableHead>
                <TableHead>Steps</TableHead>
                <TableHead>Created At</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {forecasts.map((f) => (
                <TableRow key={f.id}>
                  <TableCell className="font-mono">{f.route_id}</TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {new Date(f.anchor_ts).toLocaleString()}
                  </TableCell>
                  <TableCell>{f.model_version}</TableCell>
                  <TableCell>
                    {Array.isArray(f.forecasts) ? f.forecasts.length : "—"}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {new Date(f.created_at).toLocaleString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
