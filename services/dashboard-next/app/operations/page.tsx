"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw, ShieldAlert, Workflow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type {
  PipelineRun,
  PipelineStatus,
  QualityAlert,
  QualityStatus,
} from "@/lib/types";

interface ApiError {
  error?: string;
  detail?: string | { msg?: string }[];
}

function formatError(err: ApiError): string {
  if (err.error) return err.error;
  if (typeof err.detail === "string") return err.detail;
  if (Array.isArray(err.detail)) {
    return err.detail.map((item) => item.msg ?? JSON.stringify(item)).join("; ");
  }
  return "Request failed";
}

async function readError(res: Response): Promise<string> {
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return formatError((await res.json()) as ApiError);
  }
  return (await res.text()) || `HTTP ${res.status}`;
}

function badge(status: string) {
  const value = status.toLowerCase();
  const tone =
    value.includes("success") || value === "ok"
      ? "bg-emerald-600 text-white"
      : value.includes("fail")
        ? "bg-rose-700 text-white"
        : value.includes("run") || value.includes("active")
          ? "bg-sky-700 text-white"
          : "bg-muted text-foreground";

  return (
    <span className={`rounded px-2 py-1 text-xs font-medium ${tone}`}>
      {status}
    </span>
  );
}

export default function OperationsPage() {
  const [pipeline, setPipeline] = useState<PipelineStatus | null>(null);
  const [quality, setQuality] = useState<QualityStatus | null>(null);
  const [history, setHistory] = useState<PipelineRun[]>([]);
  const [alerts, setAlerts] = useState<QualityAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [statusRes, historyRes, alertsRes] = await Promise.all([
        fetch("/api/operations/status"),
        fetch("/api/operations/history?limit=20"),
        fetch("/api/operations/alerts"),
      ]);

      if (!statusRes.ok) throw new Error(await readError(statusRes));
      if (!historyRes.ok) throw new Error(await readError(historyRes));
      if (!alertsRes.ok) throw new Error(await readError(alertsRes));

      const statusJson = (await statusRes.json()) as {
        pipeline: PipelineStatus;
        quality: QualityStatus;
      };
      const historyJson = (await historyRes.json()) as {
        runs: PipelineRun[];
      };
      const alertsJson = (await alertsRes.json()) as {
        alerts: QualityAlert[];
      };

      setPipeline(statusJson.pipeline);
      setQuality(statusJson.quality);
      setHistory(historyJson.runs ?? []);
      setAlerts(alertsJson.alerts ?? []);
      setError(null);
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : "Operations unavailable"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const runAction = useCallback(
    async (key: string, path: string, successMessage: string) => {
      setBusyKey(key);
      setActionError(null);
      setActionMessage(null);
      try {
        const res = await fetch(path, { method: "POST" });
        if (!res.ok) {
          throw new Error(await readError(res));
        }
        setActionMessage(successMessage);
        await loadData();
      } catch (action) {
        setActionError(
          action instanceof Error ? action.message : "Action failed unexpectedly"
        );
      } finally {
        setBusyKey(null);
      }
    },
    [loadData]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Operations</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Pipeline status, recent execution history, quality alerts, and
            manual operator triggers.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            onClick={() =>
              void runAction(
                "pipeline",
                "/api/operations/pipeline/trigger",
                "Pipeline trigger started"
              )
            }
            disabled={busyKey === "pipeline"}
          >
            {busyKey === "pipeline" ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                <Workflow className="mr-2 h-4 w-4" />
                Trigger pipeline
              </>
            )}
          </Button>
          <Button
            onClick={() =>
              void runAction(
                "quality",
                "/api/operations/quality/trigger",
                "Quality trigger started"
              )
            }
            disabled={busyKey === "quality"}
          >
            {busyKey === "quality" ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                <RefreshCw className="mr-2 h-4 w-4" />
                Trigger quality
              </>
            )}
          </Button>
        </div>
      </div>

      {error ? (
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : null}

      {actionError ? (
        <Card className="border-destructive/50">
          <CardContent className="pt-6 text-sm text-destructive">
            {actionError}
          </CardContent>
        </Card>
      ) : null}

      {actionMessage ? (
        <Card className="border-emerald-500/40">
          <CardContent className="pt-6 text-sm text-emerald-400">
            {actionMessage}
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Pipeline status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {loading ? (
              <p className="text-muted-foreground">Loading...</p>
            ) : (
              <>
                <div>{badge(pipeline?.last_status ?? "unknown")}</div>
                <p className="text-muted-foreground">
                  Last run:{" "}
                  {pipeline?.last_run
                    ? new Date(pipeline.last_run).toLocaleString()
                    : "never"}
                </p>
                <p className="font-mono">
                  Run count: {pipeline?.run_count ?? 0}
                </p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Quality status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {loading ? (
              <p className="text-muted-foreground">Loading...</p>
            ) : (
              <>
                <div>{badge(quality?.last_check ? "checked" : "not-run")}</div>
                <p className="text-muted-foreground">
                  Last check:{" "}
                  {quality?.last_check
                    ? new Date(quality.last_check).toLocaleString()
                    : "never"}
                </p>
                <p className="font-mono">
                  Active alerts: {quality?.active_alerts ?? 0}
                </p>
                <p className="font-mono">
                  Shadow streak: {quality?.shadow_win_streak ?? 0}/
                  {quality?.promote_threshold ?? 0}
                </p>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent pipeline history</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Run</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Started</TableHead>
                <TableHead>Completed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {history.map((run) => (
                <TableRow key={run.id}>
                  <TableCell className="font-mono">{run.run_type}</TableCell>
                  <TableCell>{badge(run.status)}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {new Date(run.started_at).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {run.completed_at
                      ? new Date(run.completed_at).toLocaleString()
                      : "—"}
                  </TableCell>
                </TableRow>
              ))}
              {!loading && history.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="text-center text-sm text-muted-foreground"
                  >
                    No pipeline runs recorded yet.
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Quality alerts</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {quality?.last_metrics ? (
            <div className="grid gap-4 md:grid-cols-4 text-sm">
              <div>
                <p className="text-muted-foreground">WAPE</p>
                <p className="font-mono">
                  {String(quality.last_metrics["wape"] ?? "—")}
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">RBias</p>
                <p className="font-mono">
                  {String(quality.last_metrics["rbias"] ?? "—")}
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">Combined</p>
                <p className="font-mono">
                  {String(quality.last_metrics["combined_score"] ?? "—")}
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">Pairs</p>
                <p className="font-mono">
                  {String(quality.last_metrics["n_pairs"] ?? "—")}
                </p>
              </div>
            </div>
          ) : null}

          <div className="space-y-3">
            {alerts.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
                No active quality alerts.
              </div>
            ) : (
              alerts.map((alert) => (
                <div
                  key={`${alert.type}-${alert.timestamp}`}
                  className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4"
                >
                  <div className="flex items-start gap-3">
                    <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
                    <div className="space-y-1 text-sm">
                      <div className="font-medium">{alert.message}</div>
                      <div className="font-mono text-muted-foreground">
                        {alert.type} · value {alert.value} · threshold {alert.threshold}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {new Date(alert.timestamp).toLocaleString()}
                      </div>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          {quality?.shadow_streak_version ? (
            <div className="flex items-start gap-3 rounded-lg border border-sky-500/30 bg-sky-500/5 p-4 text-sm">
              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-sky-400" />
              <div>
                Shadow streak is currently tracked for{" "}
                <span className="font-mono">{quality.shadow_streak_version}</span>.
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
