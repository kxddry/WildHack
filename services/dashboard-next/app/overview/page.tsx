"use client";

import { useEffect, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import {
  Warehouse as WarehouseIcon,
  Route,
  Truck,
  Database,
  Target,
  Gauge,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import type { BusinessMetrics, Warehouse } from "@/lib/types";

export default function OverviewPage() {
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [metrics, setMetrics] = useState<BusinessMetrics | null>(null);
  const [metricsError, setMetricsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/db/warehouses")
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        setWarehouses(data.warehouses ?? []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));

    fetch("/api/metrics/business")
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        setMetrics(data as BusinessMetrics);
      })
      .catch((e) =>
        setMetricsError(e instanceof Error ? e.message : "Metrics unavailable")
      );
  }, []);

  const totalRoutes = warehouses.reduce(
    (s, w) => s + Number(w.route_count),
    0
  );
  const totalTrucks = warehouses.reduce(
    (s, w) => s + Number(w.upcoming_trucks),
    0
  );
  const withForecasts = warehouses.filter(
    (w) => w.latest_forecast_at != null
  ).length;

  const chartData = warehouses.map((w) => ({
    id: `WH${w.warehouse_id}`,
    routes: Number(w.route_count),
  }));

  if (loading) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">Overview</h1>
        <p className="text-muted-foreground">Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">Overview</h1>
        <p className="text-red-500">Error: {error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Overview</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Warehouse and route summary
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
        <KpiCard
          title="Total Warehouses"
          value={warehouses.length}
          icon={WarehouseIcon}
        />
        <KpiCard
          title="Total Routes"
          value={totalRoutes}
          icon={Route}
        />
        <KpiCard
          title="Upcoming Trucks"
          value={totalTrucks}
          subtitle="planned + dispatched"
          icon={Truck}
        />
        <KpiCard
          title="With Forecasts"
          value={withForecasts}
          subtitle={`of ${warehouses.length} warehouses`}
          icon={Database}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Business KPIs (PRD §9.2)</CardTitle>
        </CardHeader>
        <CardContent>
          {metricsError ? (
            <p className="text-sm text-red-500">Error: {metricsError}</p>
          ) : metrics === null ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <KpiCard
                  title="Order accuracy (±1 truck)"
                  value={
                    metrics.n_slots_evaluated > 0
                      ? `${(metrics.order_accuracy * 100).toFixed(1)}%`
                      : "—"
                  }
                  subtitle={`${metrics.n_slots_evaluated} of ${metrics.n_slots_total} slots evaluated`}
                  icon={Target}
                />
                <KpiCard
                  title="Avg truck utilization"
                  value={
                    metrics.n_slots_evaluated > 0
                      ? `${(metrics.avg_truck_utilization * 100).toFixed(1)}%`
                      : "—"
                  }
                  subtitle={
                    metrics.truck_capacity > 0
                      ? `capacity = ${metrics.truck_capacity} units / truck`
                      : "no fulfilment data yet"
                  }
                  icon={Gauge}
                />
              </div>
              {metrics.note && (
                <p className="text-xs text-muted-foreground">{metrics.note}</p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Warehouse Load (Routes per Warehouse)</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={AXIS_STYLE.stroke} />
              <XAxis dataKey="id" tick={{ fill: AXIS_STYLE.fill, fontSize: AXIS_STYLE.fontSize }} />
              <YAxis tick={{ fill: AXIS_STYLE.fill, fontSize: AXIS_STYLE.fontSize }} allowDecimals={false} />
              <Tooltip contentStyle={TOOLTIP_STYLE} />
              <Bar dataKey="routes" fill={CHART_COLORS[0]} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Warehouses</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ID</TableHead>
                <TableHead>Name</TableHead>
                <TableHead className="text-right">Routes</TableHead>
                <TableHead>Latest Forecast</TableHead>
                <TableHead className="text-right">Upcoming Trucks</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {warehouses.map((w) => (
                <TableRow key={w.warehouse_id}>
                  <TableCell className="font-mono">{w.warehouse_id}</TableCell>
                  <TableCell>{w.name ?? "—"}</TableCell>
                  <TableCell className="text-right">{w.route_count}</TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {w.latest_forecast_at
                      ? new Date(w.latest_forecast_at).toLocaleString()
                      : "—"}
                  </TableCell>
                  <TableCell className="text-right">{w.upcoming_trucks}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
