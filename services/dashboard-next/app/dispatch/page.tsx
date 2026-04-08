"use client";

import { useEffect, useState, useCallback } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { Truck, RefreshCw, Package, CheckCircle, XCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { KpiCard } from "@/components/kpi-card";
import { StatusBadge } from "@/components/status-badge";
import { TOOLTIP_STYLE, AXIS_STYLE, STATUS_COLORS } from "@/lib/chart-theme";
import type { Warehouse, TransportRequest } from "@/lib/types";

interface ChartPoint {
  slot: string;
  planned: number;
  dispatched: number;
  completed: number;
  cancelled: number;
}

function buildChartData(requests: TransportRequest[]): ChartPoint[] {
  const map: Record<string, ChartPoint> = {};
  for (const r of requests) {
    const slot = new Date(r.time_slot_start).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    if (!map[slot]) {
      map[slot] = { slot, planned: 0, dispatched: 0, completed: 0, cancelled: 0 };
    }
    map[slot][r.status] = (map[slot][r.status] ?? 0) + r.trucks_needed;
  }
  return Object.values(map).sort((a, b) => (a.slot < b.slot ? -1 : 1));
}

export default function DispatchPage() {
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [selectedWarehouse, setSelectedWarehouse] = useState<string>("");
  const [forecastWindow, setForecastWindow] = useState(6);
  const [requests, setRequests] = useState<TransportRequest[]>([]);
  const [dispatching, setDispatching] = useState(false);
  const [loadingWarehouses, setLoadingWarehouses] = useState(true);
  const [loadingRequests, setLoadingRequests] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRequests = useCallback(
    (warehouseId: string) => {
      if (!warehouseId) return;
      setLoadingRequests(true);
      setError(null);
      fetch(`/api/transport-requests?warehouse_id=${warehouseId}&limit=100`)
        .then((r) => r.json())
        .then((data) => {
          if (data.error) throw new Error(data.error);
          // dispatcher-service returns {items, total}; legacy clients used
          // {requests: [...]}. Accept either so future API changes don't
          // require touching the UI.
          const rows = data.items ?? data.requests ?? [];
          setRequests(rows);
        })
        .catch((e) => setError(e.message))
        .finally(() => setLoadingRequests(false));
    },
    []
  );

  useEffect(() => {
    fetch("/api/warehouses")
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        const ws: Warehouse[] = data.warehouses ?? [];
        setWarehouses(ws);
        if (ws.length > 0) {
          const id = String(ws[0].warehouse_id);
          setSelectedWarehouse(id);
          fetchRequests(id);
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingWarehouses(false));
  }, [fetchRequests]);

  useEffect(() => {
    if (selectedWarehouse) fetchRequests(selectedWarehouse);
  }, [selectedWarehouse, fetchRequests]);

  async function handleDispatch() {
    if (!selectedWarehouse) return;
    setDispatching(true);
    setError(null);
    try {
      // Fetch the latest forecast anchor_ts for this warehouse to build a valid time range
      const fcRes = await fetch(
        `/api/forecasts?warehouse_id=${selectedWarehouse}&limit=500`
      );
      const fcData = await fcRes.json();
      const forecasts: Array<{ anchor_ts: string }> = fcData.forecasts ?? [];

      if (forecasts.length === 0) {
        throw new Error("No forecasts found for this warehouse");
      }

      const anchors = forecasts.map((f) => new Date(f.anchor_ts).getTime());
      const minAnchor = new Date(Math.min(...anchors));
      const maxAnchor = new Date(
        Math.max(...anchors) + forecastWindow * 60 * 60 * 1000
      );

      const res = await fetch("/api/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          warehouse_id: Number(selectedWarehouse),
          time_range_start: minAnchor.toISOString(),
          time_range_end: maxAnchor.toISOString(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? data.error ?? "Dispatch failed");
      fetchRequests(selectedWarehouse);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Dispatch failed");
    } finally {
      setDispatching(false);
    }
  }

  const totalTrucks = requests.reduce((s, r) => s + r.trucks_needed, 0);
  const planned = requests.filter((r) => r.status === "planned").length;
  const dispatched = requests.filter((r) => r.status === "dispatched").length;
  const totalContainers = requests.reduce((s, r) => s + r.total_containers, 0);
  const chartData = buildChartData(requests);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Dispatch</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Run dispatch and manage transport requests
          </p>
        </div>

        <div className="flex items-end gap-4 shrink-0">
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

          <div className="flex flex-col gap-1 w-48">
            <span className="text-xs text-muted-foreground">
              Forecast window: {forecastWindow}h
            </span>
            <Slider
              min={1}
              max={24}
              step={1}
              value={[forecastWindow]}
              onValueChange={([v]) => setForecastWindow(v)}
              className="w-full"
            />
          </div>

          <Button
            onClick={handleDispatch}
            disabled={dispatching || !selectedWarehouse}
            className="shrink-0"
          >
            {dispatching ? (
              <RefreshCw className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <Truck className="h-4 w-4 mr-2" />
            )}
            {dispatching ? "Running..." : "Run Dispatch"}
          </Button>
        </div>
      </div>

      {error && <p className="text-red-500 text-sm">Error: {error}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
        <KpiCard title="Total Trucks" value={totalTrucks} icon={Truck} />
        <KpiCard
          title="Planned"
          value={planned}
          subtitle="requests"
          icon={CheckCircle}
        />
        <KpiCard
          title="Dispatched"
          value={dispatched}
          subtitle="requests"
          icon={XCircle}
        />
        <KpiCard
          title="Total Containers"
          value={totalContainers.toFixed(1)}
          icon={Package}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Trucks by Time Slot</CardTitle>
        </CardHeader>
        <CardContent>
          {loadingRequests ? (
            <div className="h-64 flex items-center justify-center text-muted-foreground">
              Loading...
            </div>
          ) : chartData.length === 0 ? (
            <div className="h-64 flex items-center justify-center text-muted-foreground">
              No dispatch data — run dispatch first
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart
                data={chartData}
                margin={{ top: 4, right: 16, left: 0, bottom: 4 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke={AXIS_STYLE.stroke}
                />
                <XAxis
                  dataKey="slot"
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
                {(["planned", "dispatched", "completed", "cancelled"] as const).map(
                  (status) => (
                    <Bar key={status} dataKey={status} stackId="a" radius={status === "cancelled" ? [4, 4, 0, 0] : undefined}>
                      {chartData.map((_, i) => (
                        <Cell
                          key={i}
                          fill={STATUS_COLORS[status]}
                        />
                      ))}
                    </Bar>
                  )
                )}
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Transport Requests</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Start</TableHead>
                <TableHead>End</TableHead>
                <TableHead className="text-right">Containers</TableHead>
                <TableHead className="text-right">Trucks</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Calculation</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {requests.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="text-sm text-muted-foreground">
                    {new Date(r.time_slot_start).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {new Date(r.time_slot_end).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right font-mono">
                    {r.total_containers.toFixed(1)}
                  </TableCell>
                  <TableCell className="text-right font-mono">
                    {r.trucks_needed}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={r.status} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground max-w-xs truncate">
                    {r.calculation}
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
