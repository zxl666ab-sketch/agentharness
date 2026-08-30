import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ErrorBoundary } from "./components/ui";
import "./styles/tokens.css";
import "./styles/app.css";

const qc = new QueryClient({
  defaultOptions: {
    // SSE drives freshness; focus refetch would only duplicate it.
    queries: { staleTime: 2000, retry: 1, refetchOnWindowFocus: false },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <ErrorBoundary><App /></ErrorBoundary>
    </QueryClientProvider>
  </React.StrictMode>
);
