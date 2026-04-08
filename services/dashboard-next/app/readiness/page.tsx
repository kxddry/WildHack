"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CheckCircle, XCircle, AlertTriangle, Upload } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import type { HealthCheck, CheckStatus, Warehouse } from "@/lib/types";

function StatusIcon({ status }: { status: CheckStatus }) {
  if (status === "pass")
    return <CheckCircle className="h-5 w-5 text-green-500 shrink-0" />;
  if (status === "warn")
    return <AlertTriangle className="h-5 w-5 text-yellow-500 shrink-0" />;
  return <XCircle className="h-5 w-5 text-red-500 shrink-0" />;
}

async function fetchJson<T>(url: string): Promise<{ data: T | null; ok: boolean; error?: string }> {
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) return { data: null, ok: false, error: data.error ?? `HTTP ${res.status}` };
    return { data, ok: true };
  } catch (e) {
    return { data: null, ok: false, error: e instanceof Error ? e.message : "Unreachable" };
  }
}

export default function ReadinessPage() {
  const [checks, setChecks] = useState<HealthCheck[]>([]);
  const [loading, setLoading] = useState(true);
  const [emptyDb, setEmptyDb] = useState(false);

  useEffect(() => {
    async function runChecks() {
      const results: HealthCheck[] = [];

      // Fetch shared data once. Stats come from a dedicated endpoint that
      // returns REAL row counts via COUNT(*) — not derivations from the
      // warehouses list. The previous implementation reported "forecasts
      // count = warehouses with latest_forecast_at", which was misleading
      // for the empty-state diagnostics this page exists to surface.
      const [wh, stats, ...health] = await Promise.all([
        fetchJson<{ warehouses: Warehouse[] }>("/api/warehouses"),
        fetchJson<{
          warehouses: number;
          routes: number;
          route_status_history: number;
          forecasts: number;
          transport_requests: number;
        }>("/api/readiness/stats"),
        fetchJson<{ status: string; uptime_seconds?: number }>("/api/health/prediction"),
        fetchJson<{ status: string; uptime_seconds?: number }>("/api/health/dispatcher"),
        fetchJson<{ model_type: string; model_version: string; forecast_horizon: number }>("/api/model/info"),
      ]);
      const warehouses = wh.data?.warehouses ?? [];
      const [prediction, dispatcher, model] = health;

      // 1. PostgreSQL
      results.push({
        name: "PostgreSQL Connection",
        status: wh.ok ? "pass" : "fail",
        detail: wh.ok ? `Connected — ${warehouses.length} warehouse(s)` : wh.error!,
      });

      // Prediction/Dispatcher services report status as "healthy" (OK),
      // "mock" (OK with mock model), "degraded" (warn), anything else → warn.
      const HEALTHY_STATUSES = new Set(["ok", "healthy", "mock"]);
      const statusToCheck = (s: string | undefined): CheckStatus =>
        s && HEALTHY_STATUSES.has(s) ? "pass" : "warn";

      // 2. Prediction Service
      results.push({
        name: "Prediction Service",
        status: prediction.ok ? statusToCheck(prediction.data!.status) : "fail",
        detail: prediction.ok
          ? `Status: ${prediction.data!.status}, uptime ${Math.round(prediction.data!.uptime_seconds ?? 0)}s`
          : prediction.error!,
      });

      // 3. Dispatcher Service
      results.push({
        name: "Dispatcher Service",
        status: dispatcher.ok ? statusToCheck(dispatcher.data!.status) : "fail",
        detail: dispatcher.ok
          ? `Status: ${dispatcher.data!.status}, uptime ${Math.round(dispatcher.data!.uptime_seconds ?? 0)}s`
          : dispatcher.error!,
      });

      // 4. ML Model
      results.push({
        name: "ML Model",
        status: model.ok && model.data!.model_type ? "pass" : "warn",
        detail: model.ok
          ? `${model.data!.model_type} v${model.data!.model_version}, horizon ${model.data!.forecast_horizon} steps`
          : model.error ?? "Model not loaded",
      });

      // 5-9. Database tables — real COUNT(*) values via /api/readiness/stats
      // (proxied to retraining-service). Falls back to the database-unavailable
      // warning only if both the warehouses fetch AND the stats fetch failed.
      const tableNames = [
        "warehouses",
        "routes",
        "route_status_history",
        "forecasts",
        "transport_requests",
      ] as const;

      for (const name of tableNames) {
        const count = stats.data ? stats.data[name] : 0;
        const available = stats.ok;
        results.push({
          name: `Table: ${name}`,
          status: available && count > 0 ? "pass" : available ? "warn" : "fail",
          detail: available ? `${count} row(s) found` : (stats.error ?? "Database unavailable"),
        });
      }

      setChecks(results);
      // Show the "No data yet" CTA only when the DB is reachable AND
      // genuinely empty. Uses the real warehouse count from stats, not
      // the /api/warehouses list length (same value but pinned to
      // the same source the table checks report on).
      setEmptyDb(stats.ok && (stats.data?.warehouses ?? 0) === 0);
      setLoading(false);
    }

    runChecks();
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">System Readiness</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Health checks for all system components
        </p>
      </div>

      {emptyDb && (
        <Card className="border-primary/40 bg-primary/5">
          <CardContent className="pt-6">
            <div className="flex items-start gap-4">
              <Upload className="h-6 w-6 text-primary shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-sm">No data yet</p>
                <p className="text-sm text-muted-foreground mt-1">
                  The database is connected but empty. Upload your historical
                  observations to bootstrap warehouses, routes, and forecasts.
                </p>
                <Link
                  href="/setup"
                  className="inline-flex items-center gap-1.5 mt-3 text-sm font-medium text-primary hover:underline"
                >
                  Upload dataset →
                </Link>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {loading
          ? Array.from({ length: 9 }).map((_, i) => (
              <Card key={i}>
                <CardContent className="pt-6">
                  <div className="h-4 bg-muted rounded animate-pulse mb-2 w-2/3" />
                  <div className="h-3 bg-muted rounded animate-pulse w-full" />
                </CardContent>
              </Card>
            ))
          : checks.map((check) => (
              <Card key={check.name}>
                <CardContent className="pt-6">
                  <div className="flex items-start gap-3">
                    <StatusIcon status={check.status} />
                    <div className="min-w-0">
                      <p className="font-medium text-sm leading-tight">
                        {check.name}
                      </p>
                      <p className="text-xs text-muted-foreground mt-1 break-words">
                        {check.detail}
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
      </div>
    </div>
  );
}
