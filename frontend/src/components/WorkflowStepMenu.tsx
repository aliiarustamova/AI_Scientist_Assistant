import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import {
  WORKFLOW_STEPS,
  getNavTargetForPath,
} from "@/lib/workflowContext";

/**
 * Global hamburger: jump between main experiment steps with restored
 * session state (plan id + structured hypothesis) when available.
 */
export function WorkflowStepMenu() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const { pathname } = useLocation();

  const go = (path: string) => {
    const { path: to, state } = getNavTargetForPath(path);
    if (state) {
      navigate(to, { state });
    } else {
      navigate(to);
    }
    setOpen(false);
  };

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Open workflow steps menu"
          className={cn(
            "fixed left-5 top-[5.25rem] z-40 h-11 w-11 rounded-sm border-rule bg-paper-raised",
            "shadow-[0_2px_12px_-4px_hsl(var(--ink)/0.12)]",
            "hover:bg-rule-soft/50 hover:border-ink/25",
            "focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2",
          )}
        >
          <Menu className="h-5 w-5 text-ink" strokeWidth={1.75} />
        </Button>
      </SheetTrigger>
      <SheetContent
        side="left"
        className="w-[min(100%,20rem)] border-rule bg-paper p-0 sm:max-w-md"
      >
        <SheetHeader className="border-b border-rule px-6 py-5 text-left">
          <SheetTitle className="font-serif-display text-xl text-ink">
            Workflow
          </SheetTitle>
          <SheetDescription className="text-[13px] leading-relaxed text-ink-soft">
            Jump to any step. Your last hypothesis and plan id are kept for
            this browser tab so you do not have to start over.
          </SheetDescription>
        </SheetHeader>
        <nav className="px-2 py-3" aria-label="Workflow steps">
          <ul className="space-y-0.5">
            {WORKFLOW_STEPS.map((item) => {
              const active =
                pathname === item.path ||
                (item.path !== "/" && pathname.startsWith(item.path));
              return (
                <li key={item.path}>
                  <button
                    type="button"
                    onClick={() => go(item.path)}
                    className={cn(
                      "flex w-full items-baseline gap-3 rounded-sm px-4 py-3 text-left transition-colors",
                      active
                        ? "bg-primary/[0.08] text-ink"
                        : "text-ink-soft hover:bg-rule-soft/60 hover:text-ink",
                    )}
                  >
                    <span className="font-mono-notebook text-[11px] uppercase tracking-[0.2em] text-muted-foreground">
                      {item.step}
                    </span>
                    <span className="font-serif-display text-[17px] leading-tight">
                      {item.short}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>
      </SheetContent>
    </Sheet>
  );
}

export default WorkflowStepMenu;
