"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  FileDown,
  FlaskConical,
  History,
  Loader2,
  Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type {
  ModelRegistrySummary,
  TeamTrackPreviewResponse,
} from "@/lib/types";

interface UploadResult {
  status: string;
  filename: string;
  rows_received: number;
  rows_inserted: number;
  rows_total: number;
  warehouses: number;
  routes: number;
  elapsed_seconds: number;
  pipeline_triggered: boolean;
  pipeline_result: unknown;
  active_model_version: string | null;
  retrain_result?: {
    status?: string;
    promotion_status?: string;
    reason?: string;
  } | null;
}

interface ApiError {
  error?: string;
  detail?: string | { msg?: string }[];
}

const HISTORY_COLUMNS = [
  "office_from_id",
  "route_id",
  "timestamp",
  "status_1",
  "status_2",
  "status_3",
  "status_4",
  "status_5",
  "status_6",
  "status_7",
  "status_8",
  "target_2h",
];

const TEAM_TRACK_COLUMNS = ["id", "route_id", "timestamp"];

function formatError(err: ApiError): string {
  if (err.error) return err.error;
  if (typeof err.detail === "string") return err.detail;
  if (Array.isArray(err.detail)) {
    return err.detail.map((item) => item.msg ?? JSON.stringify(item)).join("; ");
  }
  return "Request failed (no detail provided)";
}

async function readError(res: Response): Promise<string> {
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return formatError((await res.json()) as ApiError);
  }
  const text = await res.text();
  return text || `HTTP ${res.status}`;
}

function FileDropzone({
  file,
  onFileChange,
  accept,
}: {
  file: File | null;
  onFileChange: (file: File | null) => void;
  accept: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  return (
    <div
      onClick={() => inputRef.current?.click()}
      onDragOver={(event) => {
        event.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragOver(false);
        onFileChange(event.dataTransfer.files?.[0] ?? null);
      }}
      className={[
        "cursor-pointer rounded-lg border-2 border-dashed p-10 text-center transition-colors",
        dragOver
          ? "border-primary bg-primary/5"
          : "border-muted-foreground/30 hover:border-primary/50",
      ].join(" ")}
    >
      <Upload className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
      {file ? (
        <div className="text-sm">
          <div className="font-medium">{file.name}</div>
          <div className="mt-1 text-xs text-muted-foreground">
            {(file.size / (1024 * 1024)).toFixed(2)} MB
          </div>
        </div>
      ) : (
        <div className="text-sm text-muted-foreground">
          Click or drag a <code>.parquet</code> or <code>.csv</code> file here.
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
      />
    </div>
  );
}

export default function SetupPage() {
  const [registry, setRegistry] = useState<ModelRegistrySummary | null>(null);
  const [registryError, setRegistryError] = useState<string | null>(null);

  const [historyFile, setHistoryFile] = useState<File | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [historyUploading, setHistoryUploading] = useState(false);
  const [historyResult, setHistoryResult] = useState<UploadResult | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const [teamFile, setTeamFile] = useState<File | null>(null);
  const [selectedModelVersion, setSelectedModelVersion] =
    useState<string>("active_primary");
  const [teamLoading, setTeamLoading] = useState(false);
  const [downloadLoading, setDownloadLoading] = useState(false);
  const [teamPreview, setTeamPreview] = useState<TeamTrackPreviewResponse | null>(
    null
  );
  const [teamError, setTeamError] = useState<string | null>(null);

  const loadRegistry = useCallback(async () => {
    try {
      const res = await fetch("/api/models/registry");
      if (!res.ok) {
        throw new Error(await readError(res));
      }
      setRegistry((await res.json()) as ModelRegistrySummary);
      setRegistryError(null);
    } catch (error) {
      setRegistryError(
        error instanceof Error ? error.message : "Model registry unavailable"
      );
    }
  }, []);

  useEffect(() => {
    void loadRegistry();
  }, [loadRegistry]);

  const evaluationModels =
    registry?.models.filter((model) => model.evaluation_available) ?? [];

  const handleHistoryFile = useCallback((file: File | null) => {
    setHistoryFile(file);
    setHistoryError(null);
    setHistoryResult(null);
  }, []);

  const handleTeamFile = useCallback((file: File | null) => {
    setTeamFile(file);
    setTeamError(null);
    setTeamPreview(null);
  }, []);

  const historyUpload = useCallback(async () => {
    if (!historyFile) return;
    setHistoryUploading(true);
    setHistoryError(null);
    setHistoryResult(null);

    try {
      const form = new FormData();
      form.append("file", historyFile);
      const res = await fetch(`/api/data/upload?auto_refresh=${autoRefresh}`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        setHistoryError(await readError(res));
        return;
      }

      setHistoryResult((await res.json()) as UploadResult);
    } catch (error) {
      setHistoryError(
        error instanceof Error ? error.message : "Upload failed unexpectedly"
      );
    } finally {
      setHistoryUploading(false);
    }
  }, [autoRefresh, historyFile]);

  const teamModelQuery =
    selectedModelVersion === "active_primary"
      ? ""
      : `?model_version=${encodeURIComponent(selectedModelVersion)}`;

  const previewTeamTrack = useCallback(async () => {
    if (!teamFile) return;
    setTeamLoading(true);
    setTeamError(null);
    setTeamPreview(null);

    try {
      const form = new FormData();
      form.append("file", teamFile);
      const res = await fetch(`/api/team-track/preview${teamModelQuery}`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        setTeamError(await readError(res));
        return;
      }

      setTeamPreview((await res.json()) as TeamTrackPreviewResponse);
    } catch (error) {
      setTeamError(
        error instanceof Error
          ? error.message
          : "Team Track preview failed unexpectedly"
      );
    } finally {
      setTeamLoading(false);
    }
  }, [teamFile, teamModelQuery]);

  const downloadSubmission = useCallback(async () => {
    if (!teamFile) return;
    setDownloadLoading(true);
    setTeamError(null);

    try {
      const form = new FormData();
      form.append("file", teamFile);
      const res = await fetch(`/api/team-track/submission${teamModelQuery}`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        setTeamError(await readError(res));
        return;
      }

      const blob = await res.blob();
      const disposition = res.headers.get("content-disposition") ?? "";
      const match = disposition.match(/filename=\"?([^"]+)\"?/i);
      const filename = match?.[1] ?? "submission.csv";
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setTeamError(
        error instanceof Error
          ? error.message
          : "Team Track submission failed unexpectedly"
      );
    } finally {
      setDownloadLoading(false);
    }
  }, [teamFile, teamModelQuery]);

  return (
    <div className="max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Data</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Operator data flows split into two explicit scenarios: historical
          snapshot ingest and isolated Team Track evaluation.
        </p>
      </div>

      <Tabs defaultValue="history" className="space-y-6">
        <TabsList>
          <TabsTrigger value="history" className="gap-2">
            <History className="h-4 w-4" />
            History Ingest
          </TabsTrigger>
          <TabsTrigger value="team-track" className="gap-2">
            <FlaskConical className="h-4 w-4" />
            Team Track Test
          </TabsTrigger>
        </TabsList>

        <TabsContent value="history" className="space-y-6">
          <Card>
            <CardContent className="pt-6 text-sm text-muted-foreground">
              Historical snapshot with columns{" "}
              <code>office_from_id, route_id, timestamp, status_1..8, target_2h</code>.
              By default this flow executes ingest, retrain, force-promote, and
              pipeline trigger in one operator action.
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Upload historical snapshot</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <FileDropzone
                file={historyFile}
                onFileChange={handleHistoryFile}
                accept=".parquet,.csv,.tsv,.txt"
              />

              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(event) => setAutoRefresh(event.target.checked)}
                  className="h-4 w-4"
                />
                Run ingest → retrain → force promote → pipeline trigger
              </label>

              <div className="flex items-center gap-3">
                <Button
                  onClick={() => void historyUpload()}
                  disabled={!historyFile || historyUploading}
                  className="min-w-36"
                >
                  {historyUploading ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Processing...
                    </>
                  ) : (
                    "Start ingest"
                  )}
                </Button>
                {historyFile ? (
                  <button
                    onClick={() => handleHistoryFile(null)}
                    className="text-sm text-muted-foreground hover:text-foreground"
                  >
                    Clear
                  </button>
                ) : null}
              </div>
            </CardContent>
          </Card>

          {historyError ? (
            <Card className="border-destructive/50">
              <CardContent className="pt-6">
                <div className="flex items-start gap-3">
                  <AlertCircle className="h-5 w-5 shrink-0 text-destructive" />
                  <div className="text-sm">
                    <div className="font-medium text-destructive">
                      History ingest failed
                    </div>
                    <p className="mt-1 break-words text-muted-foreground">
                      {historyError}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ) : null}

          {historyResult ? (
            <Card className="border-emerald-500/40">
              <CardContent className="space-y-4 pt-6">
                <div className="flex items-start gap-3">
                  <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-500" />
                  <div className="text-sm">
                    <div className="font-medium text-emerald-400">
                      Snapshot ingested: {historyResult.filename}
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      Took {historyResult.elapsed_seconds.toFixed(2)}s
                    </p>
                  </div>
                </div>

                <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
                  <dt className="text-muted-foreground">Rows received</dt>
                  <dd className="font-mono">
                    {historyResult.rows_received.toLocaleString()}
                  </dd>
                  <dt className="text-muted-foreground">Rows inserted</dt>
                  <dd className="font-mono">
                    {historyResult.rows_inserted.toLocaleString()}
                  </dd>
                  <dt className="text-muted-foreground">History total</dt>
                  <dd className="font-mono">
                    {historyResult.rows_total.toLocaleString()}
                  </dd>
                  <dt className="text-muted-foreground">Routes</dt>
                  <dd className="font-mono">{historyResult.routes}</dd>
                  <dt className="text-muted-foreground">Warehouses</dt>
                  <dd className="font-mono">{historyResult.warehouses}</dd>
                  <dt className="text-muted-foreground">Active model</dt>
                  <dd className="font-mono">
                    {historyResult.active_model_version ?? "unchanged"}
                  </dd>
                  <dt className="text-muted-foreground">Retrain</dt>
                  <dd className="font-mono">
                    {historyResult.retrain_result?.promotion_status ??
                      historyResult.retrain_result?.status ??
                      "skipped"}
                  </dd>
                  <dt className="text-muted-foreground">Pipeline</dt>
                  <dd className="font-mono">
                    {historyResult.pipeline_triggered ? "triggered" : "skipped"}
                  </dd>
                </dl>
              </CardContent>
            </Card>
          ) : null}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Expected ingest schema</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-1.5 text-xs font-mono sm:grid-cols-3">
                {HISTORY_COLUMNS.map((column) => (
                  <div
                    key={column}
                    className="rounded bg-muted px-2 py-1 text-muted-foreground"
                  >
                    {column}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="team-track" className="space-y-6">
          <Card>
            <CardContent className="pt-6 text-sm text-muted-foreground">
              Upload the Team Track test template with columns{" "}
              <code>id, route_id, timestamp</code>. History is read from the live
              <code> route_status_history </code>
              snapshot in Postgres. Default target is the active primary; extra
              registry versions appear only when they have their own versioned
              inference artifacts.
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Select model</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Select
                value={selectedModelVersion}
                onValueChange={setSelectedModelVersion}
              >
                <SelectTrigger className="max-w-xl">
                  <SelectValue placeholder="Choose model" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="active_primary">Active primary</SelectItem>
                  {evaluationModels.map((model) => (
                    <SelectItem
                      key={model.model_version}
                      value={model.model_version}
                    >
                      {model.model_version}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {registryError ? (
                <p className="text-xs text-yellow-500">{registryError}</p>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Upload Team Track file</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <FileDropzone
                file={teamFile}
                onFileChange={handleTeamFile}
                accept=".parquet,.csv,.tsv,.txt"
              />

              <div className="flex flex-wrap items-center gap-3">
                <Button
                  onClick={() => void previewTeamTrack()}
                  disabled={!teamFile || teamLoading}
                >
                  {teamLoading ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Building preview...
                    </>
                  ) : (
                    "Preview submission"
                  )}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => void downloadSubmission()}
                  disabled={!teamFile || downloadLoading}
                >
                  {downloadLoading ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Preparing CSV...
                    </>
                  ) : (
                    <>
                      <FileDown className="mr-2 h-4 w-4" />
                      Download submission CSV
                    </>
                  )}
                </Button>
                {teamFile ? (
                  <button
                    onClick={() => handleTeamFile(null)}
                    className="text-sm text-muted-foreground hover:text-foreground"
                  >
                    Clear
                  </button>
                ) : null}
              </div>
            </CardContent>
          </Card>

          {teamError ? (
            <Card className="border-destructive/50">
              <CardContent className="pt-6">
                <div className="flex items-start gap-3">
                  <AlertCircle className="h-5 w-5 shrink-0 text-destructive" />
                  <div className="text-sm">
                    <div className="font-medium text-destructive">
                      Team Track evaluation failed
                    </div>
                    <p className="mt-1 break-words text-muted-foreground">
                      {teamError}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ) : null}

          {teamPreview ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Preview</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
                  <dt className="text-muted-foreground">Rows</dt>
                  <dd className="font-mono">{teamPreview.row_count}</dd>
                  <dt className="text-muted-foreground">Routes</dt>
                  <dd className="font-mono">{teamPreview.route_count}</dd>
                  <dt className="text-muted-foreground">Resolved model</dt>
                  <dd className="font-mono">
                    {teamPreview.model.resolved_version}
                  </dd>
                  <dt className="text-muted-foreground">Model source</dt>
                  <dd className="font-mono">{teamPreview.model.source}</dd>
                  <dt className="text-muted-foreground">Feature count</dt>
                  <dd className="font-mono">{teamPreview.model.feature_count}</dd>
                  <dt className="text-muted-foreground">Preview rows</dt>
                  <dd className="font-mono">{teamPreview.preview_count}</dd>
                </dl>

                <div className="rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>ID</TableHead>
                        <TableHead>Route</TableHead>
                        <TableHead>Timestamp</TableHead>
                        <TableHead className="text-right">Raw forecast</TableHead>
                        <TableHead className="text-right">Submitted y_pred</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {teamPreview.preview.map((row) => (
                        <TableRow key={row.id}>
                          <TableCell className="font-mono">{row.id}</TableCell>
                          <TableCell className="font-mono">
                            {row.route_id}
                          </TableCell>
                          <TableCell className="text-sm text-muted-foreground">
                            {new Date(row.timestamp).toLocaleString()}
                          </TableCell>
                          <TableCell className="text-right font-mono">
                            {row.raw_forecast.toFixed(4)}
                          </TableCell>
                          <TableCell className="text-right font-mono">
                            {row.y_pred}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          ) : null}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Expected Team Track schema</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-1.5 text-xs font-mono sm:grid-cols-3">
                {TEAM_TRACK_COLUMNS.map((column) => (
                  <div
                    key={column}
                    className="rounded bg-muted px-2 py-1 text-muted-foreground"
                  >
                    {column}
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
