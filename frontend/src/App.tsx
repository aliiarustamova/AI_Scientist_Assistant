import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import AppShell from "./components/AppShell";
import Index from "./pages/Index.tsx";
import HypothesisInput from "./pages/HypothesisInput.tsx";
import LiteratureCheck from "./pages/LiteratureCheck.tsx";
import ProtocolSources from "./pages/ProtocolSources.tsx";
import ExperimentPlan from "./pages/ExperimentPlan.tsx";
import ReviewRefine from "./pages/ReviewRefine.tsx";
import Drafts from "./pages/Drafts.tsx";
import Library from "./pages/Library.tsx";
import Account from "./pages/Account.tsx";
import NotFound from "./pages/NotFound.tsx";
import { AIAssistantLauncher } from "./components/AIAssistantLauncher.tsx";

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Index />} />
            <Route path="/lab" element={<HypothesisInput />} />
            <Route path="/literature" element={<LiteratureCheck />} />
            <Route path="/protocol-sources" element={<ProtocolSources />} />
            <Route path="/plan" element={<ExperimentPlan />} />
            <Route path="/review" element={<ReviewRefine />} />
            <Route path="/drafts" element={<Drafts />} />
            <Route path="/library" element={<Library />} />
            <Route path="/account" element={<Account />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
        <AIAssistantLauncher />
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
