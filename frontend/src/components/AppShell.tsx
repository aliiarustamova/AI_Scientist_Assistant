import { useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { ChevronRight, FlaskConical, Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

const WORKFLOW = [
  { to: "/", label: "Welcome", step: "00" },
  { to: "/lab", label: "Draft hypothesis", step: "01" },
  { to: "/literature", label: "Literature check", step: "02" },
  { to: "/protocol-sources", label: "Protocol sources", step: "03" },
  { to: "/plan", label: "Experiment plan", step: "04" },
  { to: "/review", label: "Review & refine", step: "05" },
] as const;

const MORE = [
  { to: "/drafts", label: "Drafts" },
  { to: "/library", label: "Library" },
  { to: "/account", label: "Account" },
] as const;

function NavRow({
  to,
  label,
  meta,
  active,
  onClick,
}: {
  to: string;
  label: string;
  meta?: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <Link
      to={to}
      onClick={onClick}
      className={cn(
        "group flex items-center justify-between gap-3 rounded-sm border px-3 py-2.5 font-mono-notebook text-[12px] uppercase tracking-[0.16em] transition-colors",
        active
          ? "border-primary/40 bg-primary/[0.06] text-ink"
          : "border-transparent bg-transparent text-ink-soft hover:border-rule hover:bg-paper-raised hover:text-ink",
      )}
    >
      <span className="flex min-w-0 items-baseline gap-2">
        {meta && (
          <span
            className={cn(
              "shrink-0 font-mono-notebook text-[10px] tracking-[0.2em]",
              active ? "text-primary" : "text-muted-foreground",
            )}
          >
            {meta}
          </span>
        )}
        <span className="truncate normal-case tracking-normal font-sans text-[13px] leading-snug">
          {label}
        </span>
      </span>
      <ChevronRight
        aria-hidden
        className="h-4 w-4 shrink-0 opacity-40 transition-transform group-hover:translate-x-0.5"
        strokeWidth={1.75}
      />
    </Link>
  );
}

/**
 * Global layout: floating menu opens a sheet with workflow steps + app links
 * so users can jump between sections from any page.
 */
const AppShell = () => {
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
        <div className="pointer-events-none fixed inset-x-0 top-0 z-[45] flex justify-end p-4 sm:p-5 sm:pr-10">
          <SheetTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label="Open navigation menu"
              className="pointer-events-auto h-10 w-10 rounded-sm border-rule bg-paper-raised text-ink shadow-sm transition-colors hover:bg-paper hover:text-ink focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
            >
              <Menu className="h-5 w-5" strokeWidth={1.75} />
            </Button>
          </SheetTrigger>
        </div>

        <SheetContent
          side="right"
          className="flex w-[min(100vw,22rem)] flex-col border-l border-rule bg-paper text-ink sm:max-w-md"
        >
          <SheetHeader className="border-b border-rule pb-4 text-left">
            <SheetTitle className="flex items-center gap-2.5 font-serif-display text-lg font-normal tracking-tight text-ink">
              <span
                aria-hidden
                className="flex h-8 w-8 items-center justify-center rounded-sm border border-rule bg-paper-raised"
              >
                <FlaskConical className="h-4 w-4 text-primary" strokeWidth={1.5} />
              </span>
              Praxis
            </SheetTitle>
            <p className="font-mono-notebook text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
              Jump to a step
            </p>
          </SheetHeader>

          <div className="mt-2 flex-1 space-y-6 overflow-y-auto py-2">
            <div>
              <p className="mb-2 px-1 font-mono-notebook text-[10px] uppercase tracking-[0.24em] text-muted-foreground">
                Workflow
              </p>
              <nav aria-label="Workflow steps" className="flex flex-col gap-0.5">
                {WORKFLOW.map((item) => (
                  <NavRow
                    key={item.to}
                    to={item.to}
                    label={item.label}
                    meta={item.step}
                    active={pathname === item.to}
                    onClick={() => setMenuOpen(false)}
                  />
                ))}
              </nav>
            </div>

            <div>
              <p className="mb-2 px-1 font-mono-notebook text-[10px] uppercase tracking-[0.24em] text-muted-foreground">
                App
              </p>
              <nav aria-label="App pages" className="flex flex-col gap-0.5">
                {MORE.map((item) => (
                  <NavRow
                    key={item.to}
                    to={item.to}
                    label={item.label}
                    active={pathname === item.to}
                    onClick={() => setMenuOpen(false)}
                  />
                ))}
              </nav>
            </div>
          </div>

        </SheetContent>
      </Sheet>

      <Outlet />
    </>
  );
};

export default AppShell;
