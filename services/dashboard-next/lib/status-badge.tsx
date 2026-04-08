import { Badge, type BadgeProps } from "@/components/ui/badge";

type BadgeVariant = NonNullable<BadgeProps["variant"]>;

const STATUS_RULES: Array<{
  match: (value: string) => boolean;
  variant: BadgeVariant;
}> = [
  {
    match: (value) =>
      value === "ok" ||
      value.includes("success") ||
      value.includes("promoted") ||
      value.includes("complete") ||
      value === "healthy",
    variant: "success",
  },
  {
    match: (value) => value.includes("fail") || value.includes("error"),
    variant: "destructive",
  },
  {
    match: (value) =>
      value.includes("skip") ||
      value.includes("warn") ||
      value.includes("not-run") ||
      value === "unavailable",
    variant: "warning",
  },
  {
    match: (value) =>
      value.includes("run") || value.includes("active") || value === "checked",
    variant: "info",
  },
];

export function statusToBadgeVariant(status: string | null | undefined): BadgeVariant {
  const value = (status ?? "unknown").toLowerCase().trim();
  if (!value) return "secondary";
  const rule = STATUS_RULES.find((r) => r.match(value));
  return rule?.variant ?? "secondary";
}

interface StatusBadgeProps {
  status: string | null | undefined;
  fallback?: string;
  className?: string;
}

export function StatusBadge({ status, fallback = "unknown", className }: StatusBadgeProps) {
  const label = status ?? fallback;
  const variant = statusToBadgeVariant(status);
  return (
    <Badge variant={variant} className={className}>
      {label}
    </Badge>
  );
}
