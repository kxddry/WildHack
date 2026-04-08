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
  AlertTriangle,
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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
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
    fetch("/api/warehouses")
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
        setMetricsError(e instanceof Error ? e.message : "Метрики недоступны")
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
        <h1 className="text-2xl font-bold">Обзор</h1>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">Обзор</h1>
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Раздел обзора недоступен</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Обзор</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Сводка по складам и маршрутам
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
        <KpiCard
          title="Всего складов"
          value={warehouses.length}
          icon={WarehouseIcon}
        />
        <KpiCard
          title="Всего маршрутов"
          value={totalRoutes}
          icon={Route}
        />
        <KpiCard
          title="Ожидаемые грузовики"
          value={totalTrucks}
          subtitle="запланированные + отправленные"
          icon={Truck}
        />
        <KpiCard
          title="С прогнозами"
          value={withForecasts}
          subtitle={`из ${warehouses.length} складов`}
          icon={Database}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Бизнес-показатели (PRD §9.2)</CardTitle>
        </CardHeader>
        <CardContent>
          {metricsError ? (
            <Alert variant="destructive">
              <AlertTriangle />
              <AlertTitle>Метрики недоступны</AlertTitle>
              <AlertDescription>{metricsError}</AlertDescription>
            </Alert>
          ) : metrics === null ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-4 w-48" />
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <KpiCard
                  title="Точность заказа (±2 грузовика)"
                  value={
                    metrics.n_slots_evaluated > 0
                      ? `${(metrics.order_accuracy * 100).toFixed(1)}%`
                      : "—"
                  }
                  subtitle={`${metrics.n_slots_evaluated} из ${metrics.n_slots_total} слотов оценено`}
                  icon={Target}
                />
                <KpiCard
                  title="Средняя загрузка грузовиков"
                  value={
                    metrics.n_slots_evaluated > 0
                      ? `${(metrics.avg_truck_utilization * 100).toFixed(1)}%`
                      : "—"
                  }
                  subtitle={
                    metrics.truck_capacity > 0
                      ? `вместимость = ${metrics.truck_capacity} ед. / грузовик`
                      : "данных по исполнению пока нет"
                  }
                  icon={Gauge}
                />
              </div>
              {metrics.n_slots_evaluated > 0 && (
                <p className="text-xs text-muted-foreground">
                  Демонстрационные значения рассчитываются по окну историй повторного проигрывания и сохраняют формулу KPI из PRD без изменений.
                </p>
              )}
              {metrics.note && (
                <p className="text-xs text-muted-foreground">{metrics.note}</p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Загрузка складов (маршрутов на склад)</CardTitle>
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
          <CardTitle>Склады</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ID</TableHead>
                <TableHead>Название</TableHead>
                <TableHead className="text-right">Маршруты</TableHead>
                <TableHead>Последний прогноз</TableHead>
                <TableHead className="text-right">Ожидаемые грузовики</TableHead>
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
