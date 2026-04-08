"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Workflow,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusBadge } from "@/lib/status-badge";
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
  return "Ошибка запроса";
}

async function readError(res: Response): Promise<string> {
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return formatError((await res.json()) as ApiError);
  }
  return (await res.text()) || `HTTP ${res.status}`;
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
        loadError instanceof Error ? loadError.message : "Раздел операций недоступен"
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
          action instanceof Error ? action.message : "Действие не выполнено"
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
          <h1 className="text-2xl font-bold">Операции</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Статус пайплайна, история последних запусков, оповещения о
            качестве и ручные действия оператора.
          </p>
        </div>
        <div className="flex gap-2">
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="secondary" disabled={busyKey === "pipeline"}>
                {busyKey === "pipeline" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    <Workflow className="mr-2 h-4 w-4" />
                    Запустить пайплайн
                  </>
                )}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Запустить пайплайн?</AlertDialogTitle>
                <AlertDialogDescription>
                  Это запустит ручное выполнение пайплайна. Он загрузит свежие
                  данные, пересчитает признаки и может создать нового
                  кандидата для переобучения. Запускайте только если плановый
                  цикл недоступен или завис.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Отмена</AlertDialogCancel>
                <AlertDialogAction
                  onClick={() =>
                    void runAction(
                      "pipeline",
                      "/api/operations/pipeline/trigger",
                      "Запуск пайплайна инициирован"
                    )
                  }
                >
                  Запустить
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button disabled={busyKey === "quality"}>
                {busyKey === "quality" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    Проверить качество
                  </>
                )}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Запустить проверку качества?</AlertDialogTitle>
                <AlertDialogDescription>
                  Это запустит офлайн-оценку качества против текущей теневой
                  модели. Результаты могут изменить счётчик серии теневой
                  модели и повлиять на решения о продвижении.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Отмена</AlertDialogCancel>
                <AlertDialogAction
                  onClick={() =>
                    void runAction(
                      "quality",
                      "/api/operations/quality/trigger",
                      "Проверка качества инициирована"
                    )
                  }
                >
                  Запустить
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Раздел операций недоступен</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {actionError ? (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Ошибка действия</AlertTitle>
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      ) : null}

      {actionMessage ? (
        <Alert>
          <CheckCircle2 className="text-success" />
          <AlertTitle>Действие принято</AlertTitle>
          <AlertDescription>{actionMessage}</AlertDescription>
        </Alert>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Статус пайплайна</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {loading ? (
              <div className="space-y-2">
                <Skeleton className="h-5 w-24" />
                <Skeleton className="h-4 w-48" />
                <Skeleton className="h-4 w-32" />
              </div>
            ) : (
              <>
                <div>
                  <StatusBadge status={pipeline?.last_status ?? "unknown"} />
                </div>
                <p className="text-muted-foreground">
                  Последний запуск:{" "}
                  {pipeline?.last_run
                    ? new Date(pipeline.last_run).toLocaleString()
                    : "никогда"}
                </p>
                <p className="font-mono">
                  Кол-во запусков: {pipeline?.run_count ?? 0}
                </p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Статус качества</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {loading ? (
              <div className="space-y-2">
                <Skeleton className="h-5 w-24" />
                <Skeleton className="h-4 w-48" />
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-4 w-36" />
              </div>
            ) : (
              <>
                <div>
                  <StatusBadge
                    status={quality?.last_check ? "checked" : "not-run"}
                  />
                </div>
                <p className="text-muted-foreground">
                  Последняя проверка:{" "}
                  {quality?.last_check
                    ? new Date(quality.last_check).toLocaleString()
                    : "никогда"}
                </p>
                <p className="font-mono">
                  Активных оповещений: {quality?.active_alerts ?? 0}
                </p>
                <p className="font-mono">
                  Серия теневой модели: {quality?.shadow_win_streak ?? 0}/
                  {quality?.promote_threshold ?? 0}
                </p>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Недавние запуски пайплайна</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Запуск</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead>Начало</TableHead>
                <TableHead>Завершение</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading
                ? Array.from({ length: 4 }).map((_, idx) => (
                    <TableRow key={`skeleton-${idx}`}>
                      <TableCell>
                        <Skeleton className="h-4 w-20" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-5 w-16" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-4 w-32" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-4 w-32" />
                      </TableCell>
                    </TableRow>
                  ))
                : history.map((run) => (
                    <TableRow key={run.id}>
                      <TableCell className="font-mono">{run.run_type}</TableCell>
                      <TableCell>
                        <StatusBadge status={run.status} />
                      </TableCell>
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
                    Запусков пайплайна пока нет.
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Оповещения о качестве</CardTitle>
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
                <p className="text-muted-foreground">Суммарный</p>
                <p className="font-mono">
                  {String(quality.last_metrics["combined_score"] ?? "—")}
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">Пар</p>
                <p className="font-mono">
                  {String(quality.last_metrics["n_pairs"] ?? "—")}
                </p>
              </div>
            </div>
          ) : null}

          <div className="space-y-3">
            {alerts.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
                Активных оповещений о качестве нет.
              </div>
            ) : (
              alerts.map((alert) => (
                <Alert key={`${alert.type}-${alert.timestamp}`}>
                  <AlertTriangle className="text-warning" />
                  <AlertTitle>{alert.message}</AlertTitle>
                  <AlertDescription>
                    <span className="font-mono">
                      {alert.type} · значение {alert.value} · порог{" "}
                      {alert.threshold}
                    </span>
                    <span className="text-xs">
                      {new Date(alert.timestamp).toLocaleString()}
                    </span>
                  </AlertDescription>
                </Alert>
              ))
            )}
          </div>

          {quality?.shadow_streak_version ? (
            <Alert>
              <ShieldAlert className="text-info" />
              <AlertTitle>Серия теневой модели активна</AlertTitle>
              <AlertDescription>
                Серия теневой модели сейчас отслеживается для{" "}
                <span className="font-mono">
                  {quality.shadow_streak_version}
                </span>
                .
              </AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
