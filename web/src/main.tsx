import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles/tokens.css";
import "./styles/layout.css";
import "./styles/workspace.css";
import "./styles/inspector.css";
import "./styles/evaluation.css";

const qc = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 2000, retry: 1 },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
);
