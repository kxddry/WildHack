"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Trophy,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import type { ModelRegistrySummary } from "@/lib/types";

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

export default function ModelsPage() {
  const [summary, setSummary] = useState<ModelRegistrySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const loadSummary = useCallback(async () => {
    try {
      const res = await fetch("/api/models/registry");
      if (!res.ok) {
        throw new Error(await readError(res));
      }
      setSummary((await res.json()) as ModelRegistrySummary);
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Модели недоступны");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

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
        await loadSummary();
      } catch (action) {
        setActionError(
          action instanceof Error ? action.message : "Действие не выполнено"
        );
      } finally {
        setBusyKey(null);
      }
    },
    [loadSummary]
  );

  const lastRetrain = summary?.last_retrain ?? {};

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Модели</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Реестр, состояние чемпиона и управление переобучением, теневыми
            запусками и продвижением в основную модель.
          </p>
        </div>
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button disabled={busyKey === "retrain"}>
              {busyKey === "retrain" ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Переобучение...
                </>
              ) : (
                <>
                  <RefreshCw className="mr-2 h-4 w-4" />
                  Переобучить
                </>
              )}
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Запустить переобучение?</AlertDialogTitle>
              <AlertDialogDescription>
                Это запустит новый цикл обучения на последнем снимке.
                Новая модель станет кандидатом реестра и будет бороться за
                место чемпиона. Используйте только если нужно переобучить
                модель вне расписания.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Отмена</AlertDialogCancel>
              <AlertDialogAction
                onClick={() =>
                  void runAction(
                    "retrain",
                    "/api/models/retrain",
                    "Переобучение запущено"
                  )
                }
              >
                Переобучить
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Модели недоступны</AlertTitle>
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

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Чемпион</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <Trophy className="h-4 w-4 text-amber-400" />
              <span className="font-mono">
                {summary?.champion_version ?? "Чемпион не определён"}
              </span>
            </div>
            <p className="text-muted-foreground">
              Лучший CV-скор в реестре.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Последнее переобучение</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div>
              <StatusBadge status={String(lastRetrain.status ?? "not-run")} />
            </div>
            <p className="font-mono">
              {String(lastRetrain.version ?? lastRetrain.new_model_version ?? "—")}
            </p>
            <p className="text-muted-foreground">
              {String(
                lastRetrain.promotion_status ??
                  lastRetrain.finished_at ??
                  "Переобучений не зафиксировано"
              )}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Доступно для оценки</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-sky-400" />
              <span>
                {summary?.models.filter((model) => model.evaluation_available).length ??
                  0}{" "}
                версий готово для Team Track
              </span>
            </div>
            <p className="text-muted-foreground">
              Старые версии реестра остаются доступны для продвижения, но
              недоступны для изолированных тестов до переобучения с
              версионированными артефактами инференса.
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Реестр</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Версия</TableHead>
                <TableHead>Скор</TableHead>
                <TableHead>Дата обучения</TableHead>
                <TableHead>Конфигурация переобучения</TableHead>
                <TableHead>Оценка</TableHead>
                <TableHead className="text-right">Действия</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading
                ? Array.from({ length: 4 }).map((_, idx) => (
                    <TableRow key={`skeleton-${idx}`}>
                      <TableCell>
                        <Skeleton className="h-5 w-32" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-4 w-16" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-4 w-32" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-4 w-24" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-5 w-20" />
                      </TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-2">
                          <Skeleton className="h-8 w-24" />
                          <Skeleton className="h-8 w-32" />
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                : summary?.models.map((model) => {
                    const config = model.config_json ?? {};
                    const trainingWindow = config["training_window_days"];
                    const policy = config["policy"];
                    return (
                      <TableRow key={model.model_version}>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <span className="font-mono">{model.model_version}</span>
                            {model.is_champion ? <Badge>Чемпион</Badge> : null}
                          </div>
                        </TableCell>
                        <TableCell className="font-mono">
                          {model.cv_score != null ? model.cv_score.toFixed(4) : "—"}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {model.training_date
                            ? new Date(model.training_date).toLocaleString()
                            : "—"}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {trainingWindow != null ? `${String(trainingWindow)} дн.` : "—"}
                          {policy ? ` · ${String(policy)}` : ""}
                        </TableCell>
                        <TableCell>
                          {model.evaluation_available ? (
                            <Badge variant="success">доступна</Badge>
                          ) : (
                            <Badge variant="warning">недоступна</Badge>
                          )}
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-2">
                            <AlertDialog>
                              <AlertDialogTrigger asChild>
                                <Button
                                  variant="secondary"
                                  size="sm"
                                  disabled={
                                    busyKey === `shadow:${model.model_version}`
                                  }
                                >
                                  {busyKey === `shadow:${model.model_version}` ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                  ) : (
                                    "Загрузить как теневую"
                                  )}
                                </Button>
                              </AlertDialogTrigger>
                              <AlertDialogContent>
                                <AlertDialogHeader>
                                  <AlertDialogTitle>
                                    Загрузить{" "}
                                    <span className="font-mono">
                                      {model.model_version}
                                    </span>{" "}
                                    как теневую?
                                  </AlertDialogTitle>
                                  <AlertDialogDescription>
                                    Теневая модель получает параллельную копию
                                    продакшн-трафика для офлайн-скоринга. Она
                                    не влияет на ответы клиентам, но сбрасывает
                                    счётчик серии теневой модели.
                                  </AlertDialogDescription>
                                </AlertDialogHeader>
                                <AlertDialogFooter>
                                  <AlertDialogCancel>Отмена</AlertDialogCancel>
                                  <AlertDialogAction
                                    onClick={() =>
                                      void runAction(
                                        `shadow:${model.model_version}`,
                                        `/api/models/${encodeURIComponent(model.model_version)}/shadow`,
                                        `Теневая модель загружена: ${model.model_version}`
                                      )
                                    }
                                  >
                                    Загрузить как теневую
                                  </AlertDialogAction>
                                </AlertDialogFooter>
                              </AlertDialogContent>
                            </AlertDialog>
                            <AlertDialog>
                              <AlertDialogTrigger asChild>
                                <Button
                                  size="sm"
                                  disabled={
                                    busyKey === `promote:${model.model_version}`
                                  }
                                >
                                  {busyKey === `promote:${model.model_version}` ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                  ) : (
                                    "Сделать основной"
                                  )}
                                </Button>
                              </AlertDialogTrigger>
                              <AlertDialogContent>
                                <AlertDialogHeader>
                                  <AlertDialogTitle>
                                    Сделать{" "}
                                    <span className="font-mono">
                                      {model.model_version}
                                    </span>{" "}
                                    основной моделью?
                                  </AlertDialogTitle>
                                  <AlertDialogDescription>
                                    Это немедленно переключит продакшн-трафик
                                    на выбранную версию модели. Все прогнозы
                                    будут обслуживаться ей до замены. Убедитесь,
                                    что офлайн-оценка подтвердила безопасность
                                    продвижения.
                                  </AlertDialogDescription>
                                </AlertDialogHeader>
                                <AlertDialogFooter>
                                  <AlertDialogCancel>Отмена</AlertDialogCancel>
                                  <AlertDialogAction
                                    variant="destructive"
                                    onClick={() =>
                                      void runAction(
                                        `promote:${model.model_version}`,
                                        `/api/models/${encodeURIComponent(model.model_version)}/promote`,
                                        `Основная модель: ${model.model_version}`
                                      )
                                    }
                                  >
                                    Сделать основной
                                  </AlertDialogAction>
                                </AlertDialogFooter>
                              </AlertDialogContent>
                            </AlertDialog>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Примечания</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Версии реестра без версионированных <code>static_aggs</code> и{" "}
          <code>fill_values</code> остаются доступны для теневой или основной
          загрузки, но панель сознательно помечает их как недоступные
          для оценки Team Track.
        </CardContent>
      </Card>
    </div>
  );
}
