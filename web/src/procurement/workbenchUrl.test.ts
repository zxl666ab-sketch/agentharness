import { describe, expect, it } from "vitest";

import { readWorkbenchUrl, workbenchSearch } from "./workbenchUrl";

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
});
