import { type LucideIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

interface KpiCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: LucideIcon;
}

export function KpiCard({ title, value, subtitle, icon: Icon }: KpiCardProps) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-start justify-between">
          <div className="space-y-1">
            <p className="text-sm text-muted-foreground">{title}</p>
            <p className="text-2xl font-bold tracking-tight">{value}</p>
            {subtitle && (
              <p className="text-xs text-muted-foreground">{subtitle}</p>
            )}
          </div>
          {Icon && (
            <Icon className="h-5 w-5 text-muted-foreground shrink-0 mt-0.5" />
          )}
        </div>
      </CardContent>
    </Card>
  );
}
