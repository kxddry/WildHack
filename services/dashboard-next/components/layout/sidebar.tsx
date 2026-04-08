"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  CheckCircle,
  BarChart3,
  TrendingUp,
  Truck,
  Activity,
  Boxes,
  Upload,
  Cpu,
  Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";

const links = [
  { href: "/overview", label: "Обзор", icon: BarChart3 },
  { href: "/forecasts", label: "Прогнозы", icon: TrendingUp },
  { href: "/dispatch", label: "Диспетчеризация", icon: Truck },
  { href: "/quality", label: "Качество", icon: Activity },
  { href: "/setup", label: "Данные", icon: Upload },
  { href: "/models", label: "Модели", icon: Cpu },
  { href: "/operations", label: "Операции", icon: Workflow },
  { href: "/readiness", label: "Готовность", icon: CheckCircle },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-60 shrink-0 flex flex-col bg-sidebar border-r border-sidebar-border">
      <div className="flex items-center gap-2 px-4 py-5 border-b border-sidebar-border">
        <Boxes className="h-6 w-6 text-sidebar-primary" />
        <div>
          <div className="font-semibold text-sidebar-foreground leading-tight">
            WildHack
          </div>
          <div className="text-xs text-muted-foreground leading-tight">
            Диспетчер транспорта
          </div>
        </div>
      </div>
      <nav className="flex-1 px-2 py-4 space-y-1">
        {links.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                  : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
