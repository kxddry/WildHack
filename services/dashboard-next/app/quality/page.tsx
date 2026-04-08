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
import { Activity, Percent, TrendingUp, Database, AlertTriangle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { KpiCard } from "@/components/kpi-card";
import { TOOLTIP_STYLE, AXIS_STYLE, CHART_COLORS } from "@/lib/chart-theme";
import type {
  Warehouse,
  RouteStatusHistory,
  ModelInfo,
  Forecast,
  ForecastStep,
} from "@/lib/types";

interface ChartPoint {
  ts: string;
  target_2h: number | null;
  status_1: number;
  status_2: number;
  status_3: number;
  status_4: number;
  status_5: number;
  status_6: number;
  status_7: number;
  status_8: number;
}

function computeMetrics(
  history: RouteStatusHistory[],
  forecasts: Forecast[],
  routeId: number
): { wape: number; rbias: number; combined: number; points: number } {
  const routeForecasts = forecasts.filter((f) => f.route_id === routeId);

  let pairs: Array<{ actual: number; predicted: number }> = [];

  // Strategy 1: match by timestamp within 5-minute window
  for (const h of history) {
    if (h.target_2h == null) continue;
    const hTs = new Date(h.timestamp).getTime();

    for (const f of routeForecasts) {
      const steps: ForecastStep[] = Array.isArray(f.forecasts)
        ? (f.forecasts as ForecastStep[])
        : [];
      for (const step of steps) {
        const sTs = new Date(step.timestamp).getTime();
        if (Math.abs(sTs - hTs) < 5 * 60 * 1000) {
          pairs.push({ actual: h.target_2h, predicted: step.predicted_value });
          break;
        }
      }
    }
  }

  // Strategy 2: positional matching when timestamps don't align (e.g. different date ranges)
  if (pairs.length === 0 && routeForecasts.length > 0) {
    const allSteps = routeForecasts.flatMap((f) =>
      Array.isArray(f.forecasts) ? (f.forecasts as ForecastStep[]) : []
    );
    const actuals = history.filter((h) => h.target_2h != null);
    const matchCount = Math.min(allSteps.length, actuals.length);
    for (let i = 0; i < matchCount; i++) {
      pairs.push({
        actual: actuals[i].target_2h!,
        predicted: allSteps[i].predicted_value,
      });
    }
  }

  if (pairs.length === 0) return { wape: 0, rbias: 0, combined: 0, points: 0 };

  const sumAbsActual = pairs.reduce((s, p) => s + Math.abs(p.actual), 0);
  const wape =
    sumAbsActual === 0
      ? 0
      : pairs.reduce((s, p) => s + Math.abs(p.actual - p.predicted), 0) /
        sumAbsActual;

  const rbias =
    sumAbsActual === 0
      ? 0
      : Math.abs(
          pairs.reduce((s, p) => s + (p.actual - p.predicted), 0) / sumAbsActual
        );

  return {
    wape,
    rbias,
    combined: wape + rbias,
    points: pairs.length,
  };
}

export default function QualityPage() {
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [selectedWarehouse, setSelectedWarehouse] = useState<string>("");
  const [routes, setRoutes] = useState<number[]>([]);
  const [selectedRoute, setSelectedRoute] = useState<string>("");
  const [history, setHistory] = useState<RouteStatusHistory[]>([]);
  const [forecasts, setForecasts] = useState<Forecast[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetch("/api/model/info").then((r) => r.json()),
      fetch("/api/warehouses").then((r) => r.json()),
    ])
      .then(([modelData, warehouseData]) => {
        if (modelData.error) throw new Error(modelData.error);
        if (warehouseData.error) throw new Error(warehouseData.error);
        setModelInfo(modelData);
        const ws: Warehouse[] = warehouseData.warehouses ?? [];
        setWarehouses(ws);
        if (ws.length > 0) setSelectedWarehouse(String(ws[0].warehouse_id));
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedWarehouse) return;
    fetch(`/api/forecasts?warehouse_id=${selectedWarehouse}&limit=200`)
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        const fs: Forecast[] = data.forecasts ?? [];
        setForecasts(fs);
        const routeIds = [...new Set(fs.map((f) => f.route_id))].sort(
          (a, b) => a - b
        );
        setRoutes(routeIds);
        if (routeIds.length > 0) setSelectedRoute(String(routeIds[0]));
        else {
          setSelectedRoute("");
          setHistory([]);
        }
      })
      .catch((e) => setError(e.message));
  }, [selectedWarehouse]);

  useEffect(() => {
    if (!selectedRoute) return;
    setLoadingHistory(true);
    setError(null);
    fetch(`/api/status-history?route_id=${selectedRoute}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.error);
        setHistory(data.history ?? []);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoadingHistory(false));
  }, [selectedRoute]);

  const chartData: ChartPoint[] = history.map((h) => ({
    ts: new Date(h.timestamp).toLocaleTimeString([], {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    } as Intl.DateTimeFormatOptions),
    target_2h: h.target_2h,
    status_1: h.status_1,
    status_2: h.status_2,
    status_3: h.status_3,
    status_4: h.status_4,
    status_5: h.status_5,
    status_6: h.status_6,
    status_7: h.status_7,
    status_8: h.status_8,
  }));

  const metrics = computeMetrics(
    history,
    forecasts,
    selectedRoute ? Number(selectedRoute) : 0
  );

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Качество</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Точность модели и метрики качества прогнозов
        </p>
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Раздел качества недоступен</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {loading ? (
        <div className="space-y-4">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : (
        <>
          {modelInfo && (
            <Card>
              <CardHeader>
                <CardTitle>Информация о модели</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  <div>
                    <p className="text-muted-foreground">Версия</p>
                    <p className="font-mono font-medium">{modelInfo.model_version}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Тип</p>
                    <p className="font-medium">{modelInfo.model_type}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Целевая функция</p>
                    <p className="font-medium">{modelInfo.objective}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">CV-скор</p>
                    <p className="font-medium">
                      {modelInfo.cv_score != null
                        ? modelInfo.cv_score.toFixed(4)
                        : "—"}
                    </p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Кол-во признаков</p>
                    <p className="font-medium">{modelInfo.feature_count}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Горизонт прогноза</p>
                    <p className="font-medium">{modelInfo.forecast_horizon} шагов</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Интервал шага</p>
                    <p className="font-medium">{modelInfo.step_interval_minutes} мин</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Дата обучения</p>
                    <p className="font-medium">
                      {modelInfo.training_date
                        ? new Date(modelInfo.training_date).toLocaleDateString()
                        : "—"}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          <div className="flex items-center gap-4">
            <div className="w-52">
              <Select
                value={selectedWarehouse}
                onValueChange={setSelectedWarehouse}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Выберите склад" />
                </SelectTrigger>
                <SelectContent>
                  {warehouses.map((w) => (
                    <SelectItem
                      key={w.warehouse_id}
                      value={String(w.warehouse_id)}
                    >
                      {w.name ?? `Склад ${w.warehouse_id}`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="w-48">
              <Select
                value={selectedRoute}
                onValueChange={setSelectedRoute}
                disabled={routes.length === 0}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Выберите маршрут" />
                </SelectTrigger>
                <SelectContent>
                  {routes.map((id) => (
                    <SelectItem key={id} value={String(id)}>
                      Маршрут {id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            <KpiCard
              title="WAPE"
              value={pct(metrics.wape)}
              subtitle="Взвешенная абс. ошибка"
              icon={Percent}
            />
            <KpiCard
              title="|Относительное смещение|"
              value={pct(metrics.rbias)}
              subtitle="Абсолютное относительное смещение"
              icon={Activity}
            />
            <KpiCard
              title="Суммарный скор"
              value={pct(metrics.combined)}
              subtitle="WAPE + |RBias|"
              icon={TrendingUp}
            />
            <KpiCard
              title="Точек данных"
              value={metrics.points}
              subtitle="сопоставленных пар"
              icon={Database}
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>История статусов маршрута</CardTitle>
            </CardHeader>
            <CardContent>
              {loadingHistory ? (
                <Skeleton className="h-64 w-full" />
              ) : chartData.length === 0 ? (
                <div className="h-64 flex items-center justify-center text-muted-foreground">
                  Нет истории для выбранного маршрута
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={320}>
                  <LineChart
                    data={chartData}
                    margin={{ top: 4, right: 16, left: 0, bottom: 4 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke={AXIS_STYLE.stroke}
                    />
                    <XAxis
                      dataKey="ts"
                      tick={{ fill: AXIS_STYLE.fill, fontSize: 10 }}
                      interval="preserveStartEnd"
                    />
                    <YAxis
                      tick={{ fill: AXIS_STYLE.fill, fontSize: 11 }}
                      allowDecimals={false}
                    />
                    <Tooltip contentStyle={TOOLTIP_STYLE} />
                    <Legend
                      wrapperStyle={{ fontSize: 11, color: AXIS_STYLE.fill }}
                    />
                    {([1, 2, 3, 4, 5, 6, 7, 8] as const).map((n, idx) => (
                      <Line
                        key={n}
                        type="monotone"
                        dataKey={`status_${n}`}
                        name={`Статус ${n}`}
                        stroke={CHART_COLORS[idx % CHART_COLORS.length]}
                        dot={false}
                        strokeWidth={1.5}
                      />
                    ))}
                    <Line
                      type="monotone"
                      dataKey="target_2h"
                      name="Целевое (2ч)"
                      stroke="#ef4444"
                      dot={false}
                      strokeWidth={2.5}
                      strokeDasharray="5 5"
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
