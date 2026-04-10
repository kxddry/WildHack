"use client";

import { useCallback, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

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

function formatError(err: ApiError): string {
  if (err.error) return err.error;
  if (typeof err.detail === "string") return err.detail;
  if (Array.isArray(err.detail)) {
    return err.detail.map((item) => item.msg ?? JSON.stringify(item)).join("; ");
  }
  return "Запрос не удался (детали отсутствуют)";
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
            {(file.size / (1024 * 1024)).toFixed(2)} МБ
          </div>
        </div>
      ) : (
        <div className="text-sm text-muted-foreground">
          Нажмите или перетащите файл <code>.parquet</code> или <code>.csv</code>.
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
  const [historyFile, setHistoryFile] = useState<File | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [historyUploading, setHistoryUploading] = useState(false);
  const [historyResult, setHistoryResult] = useState<UploadResult | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const handleHistoryFile = useCallback((file: File | null) => {
    setHistoryFile(file);
    setHistoryError(null);
    setHistoryResult(null);
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
        error instanceof Error ? error.message : "Ошибка загрузки"
      );
    } finally {
      setHistoryUploading(false);
    }
  }, [autoRefresh, historyFile]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Данные</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Загрузка исторического снимка с автоматическим переобучением и запуском пайплайна.
        </p>
      </div>

      <Card>
        <CardContent className="pt-6 text-sm text-muted-foreground">
          Исторический снимок с колонками{" "}
          <code>office_from_id, route_id, timestamp, status_1..8, target_2h</code>.
          По умолчанию сценарий выполняет загрузку, переобучение, принудительное продвижение и
          запуск пайплайна одним действием оператора.
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Загрузка исторического снимка</CardTitle>
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
            Запустить: импорт → переобучение → принудительное продвижение → запуск пайплайна
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
                  Обработка...
                </>
              ) : (
                "Запустить импорт"
              )}
            </Button>
            {historyFile ? (
              <button
                onClick={() => handleHistoryFile(null)}
                className="text-sm text-muted-foreground hover:text-foreground"
              >
                Очистить
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
                  Ошибка загрузки истории
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
                  Снимок загружен: {historyResult.filename}
                </div>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Заняло {historyResult.elapsed_seconds.toFixed(2)} с
                </p>
              </div>
            </div>

            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-muted-foreground">Получено строк</dt>
              <dd className="font-mono">
                {historyResult.rows_received.toLocaleString()}
              </dd>
              <dt className="text-muted-foreground">Вставлено строк</dt>
              <dd className="font-mono">
                {historyResult.rows_inserted.toLocaleString()}
              </dd>
              <dt className="text-muted-foreground">Всего в истории</dt>
              <dd className="font-mono">
                {historyResult.rows_total.toLocaleString()}
              </dd>
              <dt className="text-muted-foreground">Маршрутов</dt>
              <dd className="font-mono">{historyResult.routes}</dd>
              <dt className="text-muted-foreground">Складов</dt>
              <dd className="font-mono">{historyResult.warehouses}</dd>
              <dt className="text-muted-foreground">Активная модель</dt>
              <dd className="font-mono">
                {historyResult.active_model_version ?? "без изменений"}
              </dd>
              <dt className="text-muted-foreground">Переобучение</dt>
              <dd className="font-mono">
                {historyResult.retrain_result?.promotion_status ??
                  historyResult.retrain_result?.status ??
                  "пропущено"}
              </dd>
              <dt className="text-muted-foreground">Пайплайн</dt>
              <dd className="font-mono">
                {historyResult.pipeline_triggered ? "запущен" : "пропущен"}
              </dd>
            </dl>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Ожидаемая схема импорта</CardTitle>
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
    </div>
  );
}
