import { useEffect, useRef, useState } from "react";
import { Send, Sparkles } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { askAssistant } from "@/lib/api";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

// Per-route context: what the assistant "sees" and what it suggests
type RouteContext = {
  label: string;
  subtitle: string;
  suggestions: string[];
};

const ROUTE_CONTEXT: Record<string, RouteContext> = {
  "/": {
    label: "Welcome",
    subtitle: "Ask how Praxis works",
    suggestions: [
      "What can Praxis help me do?",
      "How does the workflow work?",
      "What kind of hypotheses work best?",
    ],
  },
  "/lab": {
    label: "Hypothesis",
    subtitle: "Ask about framing your hypothesis",
    suggestions: [
      "How do I write a testable hypothesis?",
      "What makes a hypothesis too vague?",
      "Suggest a variable to control.",
    ],
  },
  "/literature": {
    label: "Literature",
    subtitle: "Ask about prior work and novelty",
    suggestions: [
      "Summarize the most relevant paper.",
      "Is this hypothesis novel?",
      "What gaps exist in the literature?",
    ],
  },
  "/plan": {
    label: "Experiment plan",
    subtitle: "Ask about the protocol, materials, or design",
    suggestions: [
      "Why was this protocol chosen?",
      "What are the risks in this experiment?",
      "How could this be improved?",
      "What assumptions are being made?",
    ],
  },
  "/review": {
    label: "Review",
    subtitle: "Ask about refining the plan",
    suggestions: [
      "What should I refine before running this?",
      "Are the controls sufficient?",
      "What's the weakest part of this plan?",
    ],
  },
  "/drafts": {
    label: "Drafts",
    subtitle: "Ask about your in-progress work",
    suggestions: [
      "Which draft is closest to ready?",
      "Suggest next steps for my drafts.",
    ],
  },
  "/library": {
    label: "Library",
    subtitle: "Ask about your saved plans",
    suggestions: [
      "Which plans use E. coli?",
      "Find plans related to growth rate.",
    ],
  },
  "/account": {
    label: "Account",
    subtitle: "Ask about your activity",
    suggestions: [
      "How many experiments have I generated?",
      "What's my most-used organism?",
    ],
  },
};

const DEFAULT_CONTEXT: RouteContext = {
  label: "Praxis",
  subtitle: "Ask anything about your work",
  suggestions: [
    "What can you help me with?",
    "Summarize what I'm looking at.",
  ],
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  route?: string;
};

export const AIAssistantPanel = ({ open, onOpenChange, route = "/" }: Props) => {
  const ctx = ROUTE_CONTEXT[route] ?? DEFAULT_CONTEXT;
  const SUGGESTIONS = ctx.suggestions;
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, pending]);

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || pending) return;
  
    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: trimmed,
    };
  
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setPending(true);
  
    try {
      const data = await askAssistant(trimmed, {
        route,
        // 🔥 Next upgrade: pass protocol/materials here
      });
  
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: data.answer || "No response returned.",
        },
      ]);
    } catch (error) {
      setMessages((m) => [
        ...m,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Something went wrong while analyzing the experiment.",
        },
      ]);
    }
  
    setPending(false);
  };

  const handleSuggestion = (s: string) => {
    setInput(s);
    inputRef.current?.focus();
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full flex-col gap-0 border-l border-rule bg-paper p-0 sm:max-w-[440px]"
      >
        {/* Header */}
        <SheetHeader className="space-y-2 border-b border-rule bg-paper-raised px-6 py-5 text-left">
          <div className="flex items-center gap-2.5">
            <span
              aria-hidden
              className="flex h-7 w-7 items-center justify-center rounded-sm border border-rule bg-paper"
            >
              <Sparkles className="h-3.5 w-3.5 text-primary" strokeWidth={1.75} />
            </span>
            <SheetTitle className="font-serif-display text-[22px] text-ink">
              AI Assistant
            </SheetTitle>
            <span className="ml-auto inline-flex items-center gap-1.5 rounded-sm border border-rule bg-paper px-2 py-1 font-mono-notebook text-[10px] uppercase tracking-[0.2em] text-ink-soft">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-sage" />
              {ctx.label}
            </span>
          </div>
          <SheetDescription
            className="text-[14px] italic text-ink-soft"
            style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
          >
            {ctx.subtitle}
          </SheetDescription>
        </SheetHeader>

        {/* Suggestions — only when conversation is empty */}
        {messages.length === 0 && (
          <div className="border-b border-rule px-6 py-5">
            <p className="font-mono-notebook text-[11px] uppercase tracking-[0.22em] text-muted-foreground">
              Suggested questions
            </p>
            <div className="mt-3 flex flex-col gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => handleSuggestion(s)}
                  className="rounded-sm border border-rule bg-paper-raised px-3.5 py-2.5 text-left text-[14px] text-ink-soft transition-colors hover:border-ink/40 hover:bg-rule-soft/40 hover:text-ink"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Messages */}
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto px-6 py-5"
        >
          {messages.length === 0 && (
            <p
              className="text-center text-[14px] italic text-muted-foreground"
              style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
            >
              Pick a suggestion or type a question to begin.
            </p>
          )}
          <div className="flex flex-col gap-4">
            {messages.map((m) => (
              <div
                key={m.id}
                className={cn(
                  "flex",
                  m.role === "user" ? "justify-end" : "justify-start"
                )}
              >
                <div
                  className={cn(
                    "max-w-[85%] rounded-md px-3.5 py-2.5 text-[14.5px] leading-[1.55]",
                    m.role === "user"
                      ? "bg-ink text-paper"
                      : "border border-rule bg-paper-raised text-ink-soft"
                  )}
                >
                  {m.role === "assistant" && (
                    <p className="mb-1.5 font-mono-notebook text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
                      Assistant
                    </p>
                  )}
                  <p className="whitespace-pre-wrap">{m.content}</p>
                </div>
              </div>
            ))}
            {pending && (
              <div className="flex justify-start">
                <div className="max-w-[85%] rounded-md border border-rule bg-paper-raised px-3.5 py-2.5">
                  <p className="font-mono-notebook text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
                    Assistant
                  </p>
                  <p className="mt-1.5 flex items-center gap-2 text-[14.5px] italic text-ink-soft">
                    <span className="flex gap-1">
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ink-soft/60 [animation-delay:0ms]" />
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ink-soft/60 [animation-delay:150ms]" />
                      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ink-soft/60 [animation-delay:300ms]" />
                    </span>
                    Analyzing experiment…
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Input */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
          className="border-t border-rule bg-paper-raised px-4 py-4"
        >
          <div className="flex items-end gap-2 rounded-md border border-rule bg-paper px-3 py-2 focus-within:border-ink/40">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
              rows={1}
              placeholder="Ask about the protocol, materials, or design decisions…"
              className="max-h-32 flex-1 resize-none bg-transparent py-1.5 text-[14.5px] leading-[1.5] text-ink placeholder:text-muted-foreground focus:outline-none"
            />
            <Button
              type="submit"
              size="icon"
              disabled={!input.trim() || pending}
              className="h-8 w-8 shrink-0 rounded-sm bg-ink text-paper hover:bg-ink/90 disabled:opacity-40"
              aria-label="Send"
            >
              <Send className="h-3.5 w-3.5" strokeWidth={2} />
            </Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
};

export default AIAssistantPanel;
