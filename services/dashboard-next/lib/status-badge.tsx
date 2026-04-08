import { Badge, type BadgeProps } from "@/components/ui/badge";

type BadgeVariant = NonNullable<BadgeProps["variant"]>;

// Русские лейблы для всех известных статусов. Ключ — lowercase raw value.
// Если значение не найдено — возвращается исходная строка без искажения.
const STATUS_LABELS: Record<string, string> = {
  // Системные / пайплайн
  ok: "Ок",
  pass: "Ок",
  healthy: "Работает",
  mock: "Мок",
  success: "Успех",
  complete: "Завершено",
  completed: "Завершено",
  promoted: "Продвинута",
  running: "Выполняется",
  run: "Выполняется",
  active: "Активен",
  checked: "Проверено",
  failed: "Ошибка",
  fail: "Ошибка",
  error: "Ошибка",
  skipped: "Пропущено",
  skip: "Пропущено",
  warn: "Предупреждение",
  warning: "Предупреждение",
  "not-run": "Не запускалось",
  not_run: "Не запускалось",
  unavailable: "Недоступен",
  unknown: "Неизвестно",
  degraded: "Деградация",
  // Статусы заявок на транспорт
  planned: "Запланирован",
  dispatched: "Отправлен",
  cancelled: "Отменён",
};

const STATUS_RULES: Array<{
  match: (value: string) => boolean;
  variant: BadgeVariant;
}> = [
  {
    match: (value) =>
      value === "ok" ||
      value === "pass" ||
      value === "healthy" ||
      value === "mock" ||
      value.includes("success") ||
      value.includes("promoted") ||
      value.includes("complete"),
    variant: "success",
  },
  {
    match: (value) =>
      value.includes("fail") ||
      value.includes("error") ||
      value === "cancelled",
    variant: "destructive",
  },
  {
    match: (value) =>
      value.includes("skip") ||
      value.includes("warn") ||
      value.includes("not-run") ||
      value.includes("not_run") ||
      value === "unavailable" ||
      value === "degraded" ||
      value === "dispatched",
    variant: "warning",
  },
  {
    match: (value) =>
      value.includes("run") ||
      value.includes("active") ||
      value === "checked" ||
      value === "planned",
    variant: "info",
  },
];

export function statusToBadgeVariant(status: string | null | undefined): BadgeVariant {
  const value = (status ?? "unknown").toLowerCase().trim();
  if (!value) return "secondary";
  const rule = STATUS_RULES.find((r) => r.match(value));
  return rule?.variant ?? "secondary";
}

function localizeStatus(status: string | null | undefined, fallback: string): string {
  const raw = status ?? fallback;
  const key = raw.toLowerCase().trim();
  return STATUS_LABELS[key] ?? raw;
}

interface StatusBadgeProps {
  status: string | null | undefined;
  fallback?: string;
  className?: string;
}

export function StatusBadge({ status, fallback = "unknown", className }: StatusBadgeProps) {
  const label = localizeStatus(status, fallback);
  const variant = statusToBadgeVariant(status);
  return (
    <Badge variant={variant} className={className}>
      {label}
    </Badge>
  );
}
