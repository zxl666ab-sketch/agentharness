export type AppView = "inspector" | "eval";

export type AppUrlState = {
  runId: string | null;
  view: AppView;
};

export function readAppUrlState(search = window.location.search): AppUrlState {
  try {
    const params = new URLSearchParams(search);
    const run = params.get("run")?.trim();
    return {
      runId: run || null,
      view: params.get("view") === "eval" ? "eval" : "inspector",
    };
  } catch {
    return { runId: null, view: "inspector" };
  }
}

export function writeAppUrlState(runId: string | null, view: AppView): void {
  const url = new URL(window.location.href);
  if (runId) url.searchParams.set("run", runId);
  else url.searchParams.delete("run");
  if (view === "eval") url.searchParams.set("view", "eval");
  else url.searchParams.delete("view");
  window.history.replaceState({}, "", url.toString());
}
