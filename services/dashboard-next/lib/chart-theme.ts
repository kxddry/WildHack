export const TOOLTIP_STYLE: React.CSSProperties = {
  backgroundColor: "var(--color-card)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  color: "var(--color-foreground)",
};

export const TOOLTIP_LABEL_STYLE: React.CSSProperties = {
  color: "var(--color-foreground)",
};

export const TOOLTIP_ITEM_STYLE: React.CSSProperties = {
  color: "var(--color-muted-foreground)",
};

export const AXIS_STYLE = {
  stroke: "var(--color-border)",
  fontSize: 12,
  fill: "var(--color-muted-foreground)",
};

export const CHART_COLORS = [
  "#3b82f6",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#06b6d4",
  "#ec4899",
  "#f97316",
  "#14b8a6",
  "#6366f1",
] as const;

export const STATUS_COLORS: Record<string, string> = {
  planned: "#3b82f6",
  dispatched: "#10b981",
  completed: "#86efac",
  cancelled: "#ef4444",
};
