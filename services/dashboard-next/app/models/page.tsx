"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Loader2, RefreshCw, ShieldCheck, Trophy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
  return "Request failed";
}

async function readError(res: Response): Promise<string> {
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return formatError((await res.json()) as ApiError);
  }
  return (await res.text()) || `HTTP ${res.status}`;
}

function statusBadge(status: string | undefined) {
  const value = (status ?? "unknown").toLowerCase();
  const tone =
    value.includes("success") || value.includes("promoted")
      ? "bg-emerald-600 text-white"
      : value.includes("fail")
        ? "bg-rose-700 text-white"
        : value.includes("skip")
          ? "bg-amber-600 text-white"
          : "bg-muted text-foreground";

  return (
    <span className={`rounded px-2 py-1 text-xs font-medium ${tone}`}>
      {status ?? "unknown"}
    </span>
  );
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
      setError(loadError instanceof Error ? loadError.message : "Models unavailable");
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
          action instanceof Error ? action.message : "Action failed unexpectedly"
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
          <h1 className="text-2xl font-bold">Models</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Registry, champion state, and operator controls for retrain, shadow,
            and primary promotion.
          </p>
        </div>
        <Button
          onClick={() =>
            void runAction("retrain", "/api/models/retrain", "Retrain triggered")
          }
          disabled={busyKey === "retrain"}
        >
          {busyKey === "retrain" ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Retraining...
            </>
          ) : (
            <>
              <RefreshCw className="mr-2 h-4 w-4" />
              Retrain
            </>
          )}
        </Button>
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

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Champion</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <Trophy className="h-4 w-4 text-amber-400" />
              <span className="font-mono">
                {summary?.champion_version ?? "No champion yet"}
              </span>
            </div>
            <p className="text-muted-foreground">
              Lowest CV score in the registry.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Last retrain</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div>{statusBadge(String(lastRetrain.status ?? "not-run"))}</div>
            <p className="font-mono">
              {String(lastRetrain.version ?? lastRetrain.new_model_version ?? "—")}
            </p>
            <p className="text-muted-foreground">
              {String(lastRetrain.promotion_status ?? lastRetrain.finished_at ?? "No retrain recorded")}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Evaluation surface</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-sky-400" />
              <span>
                {summary?.models.filter((model) => model.evaluation_available).length ??
                  0}{" "}
                version(s) ready for Team Track
              </span>
            </div>
            <p className="text-muted-foreground">
              Old registry versions stay promotable but remain unavailable for
              isolated test runs until retrained with versioned inference
              artifacts.
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Registry</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
              Loading model registry...
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Version</TableHead>
                  <TableHead>Score</TableHead>
                  <TableHead>Training date</TableHead>
                  <TableHead>Retrain config</TableHead>
                  <TableHead>Evaluation</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {summary?.models.map((model) => {
                  const config = model.config_json ?? {};
                  const trainingWindow = config["training_window_days"];
                  const policy = config["policy"];
                  return (
                    <TableRow key={model.model_version}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <span className="font-mono">{model.model_version}</span>
                          {model.is_champion ? <Badge>Champion</Badge> : null}
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
                        {trainingWindow != null ? `${String(trainingWindow)}d` : "—"}
                        {policy ? ` · ${String(policy)}` : ""}
                      </TableCell>
                      <TableCell>
                        {model.evaluation_available ? (
                          <Badge className="bg-emerald-600 text-white hover:bg-emerald-600">
                            available
                          </Badge>
                        ) : (
                          <Badge className="bg-amber-600 text-white hover:bg-amber-600">
                            unavailable
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-2">
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={busyKey === `shadow:${model.model_version}`}
                            onClick={() =>
                              void runAction(
                                `shadow:${model.model_version}`,
                                `/api/models/${encodeURIComponent(model.model_version)}/shadow`,
                                `Shadow loaded: ${model.model_version}`
                              )
                            }
                          >
                            {busyKey === `shadow:${model.model_version}` ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              "Load shadow"
                            )}
                          </Button>
                          <Button
                            size="sm"
                            disabled={busyKey === `promote:${model.model_version}`}
                            onClick={() =>
                              void runAction(
                                `promote:${model.model_version}`,
                                `/api/models/${encodeURIComponent(model.model_version)}/promote`,
                                `Promoted primary: ${model.model_version}`
                              )
                            }
                          >
                            {busyKey === `promote:${model.model_version}` ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              "Promote primary"
                            )}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Notes</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Registry versions without versioned <code>static_aggs</code> and{" "}
          <code>fill_values</code> remain eligible for shadow or primary loads,
          but the dashboard marks them unavailable for Team Track evaluation on
          purpose.
        </CardContent>
      </Card>
    </div>
  );
}
