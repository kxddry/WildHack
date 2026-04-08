import { Badge } from "@/components/ui/badge";
import { type TransportRequest } from "@/lib/types";

type StatusValue =
  | "pass"
  | "warn"
  | "fail"
  | TransportRequest["status"];

interface StatusBadgeProps {
  status: StatusValue;
}

const statusConfig: Record<
  StatusValue,
  { label: string; style: React.CSSProperties }
> = {
  pass: {
    label: "Ок",
    style: { backgroundColor: "#16a34a", color: "#fff", borderColor: "transparent" },
  },
  warn: {
    label: "Предупреждение",
    style: { backgroundColor: "#d97706", color: "#fff", borderColor: "transparent" },
  },
  fail: {
    label: "Ошибка",
    style: { backgroundColor: "#dc2626", color: "#fff", borderColor: "transparent" },
  },
  planned: {
    label: "Запланирован",
    style: { backgroundColor: "#2563eb", color: "#fff", borderColor: "transparent" },
  },
  dispatched: {
    label: "Отправлен",
    style: { backgroundColor: "#d97706", color: "#fff", borderColor: "transparent" },
  },
  completed: {
    label: "Завершён",
    style: { backgroundColor: "#16a34a", color: "#fff", borderColor: "transparent" },
  },
  cancelled: {
    label: "Отменён",
    style: { backgroundColor: "#dc2626", color: "#fff", borderColor: "transparent" },
  },
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const config = statusConfig[status] ?? {
    label: status,
    style: {},
  };
  return (
    <Badge style={config.style}>
      {config.label}
    </Badge>
  );
}
