import { describe, expect, it } from "vitest";

import { readWorkbenchUrl, workbenchSearch, type WorkbenchUrlState } from "./workbenchUrl";

describe("procurement workbench URL state", () => {
  it("round-trips view, task, tab, filters, search and pagination", () => {
    const search = workbenchSearch({
      view: "tasks",
      task: "a".repeat(32),
      ai: "b".repeat(32),
      review: "c".repeat(32),
      tab: "audit",
      status: "attention",
      q: "快递袋 RFQ",
      page: 3,
    });

    expect(readWorkbenchUrl(search)).toEqual({
      view: "tasks",
      task: "a".repeat(32),
      ai: "b".repeat(32),
      review: "c".repeat(32),
      tab: "audit",
      status: "attention",
      q: "快递袋 RFQ",
      page: 3,
    });
  });

  it("falls back from invalid URL values without losing a valid task", () => {
    expect(readWorkbenchUrl(`?view=unknown&task=${"b".repeat(32)}&tab=missing&status=nope&page=-4`))
      .toEqual({
        view: "tasks",
        task: "b".repeat(32),
        ai: null,
        review: null,
        tab: "quotes",
        status: "all",
        q: "",
        page: 0,
      });
  });

  it("opens AI and review details directly when the view is omitted", () => {
    expect(readWorkbenchUrl(`?ai=${"d".repeat(32)}`).view).toBe("ai");
    expect(readWorkbenchUrl(`?review=${"e".repeat(32)}`).view).toBe("reviews");
  });

  it("restores view, task, tab, filter and page after a refresh (URL round-trip)", () => {
    const state: WorkbenchUrlState = {
      view: "tasks",
      task: "f".repeat(32),
      ai: null,
      review: null,
      tab: "compare",
      status: "attention",
      q: "快递袋",
      page: 2,
    };
    const search = workbenchSearch(state);
    expect(readWorkbenchUrl(search)).toEqual(state);
    expect(readWorkbenchUrl(`?${search.replace(/^\?/, "")}`)).toEqual(state);
  });

  it("keeps task filter and page in the URL for home entry deep links", () => {
    expect(workbenchSearch({ view: "tasks", task: null, ai: null, review: null, tab: "quotes", status: "attention", q: "", page: 0 }))
      .toContain("status=attention");
    expect(workbenchSearch({ view: "tasks", task: null, ai: null, review: null, tab: "quotes", status: "completed", q: "", page: 3 }))
      .toBe("?view=tasks&status=completed&page=3");
  });
});
