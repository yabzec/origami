import { describe, expect, it } from "vitest";
import { activeViewTab, documentLink, studyRows, viewTabs } from "./documentView";

const att = (id: number, role = "report") => ({ id, role, filename: "Report.pdf", file_size: 1, page_count: 1 });

describe("viewTabs", () => {
  it("plain documents have one tab", () => {
    expect(viewTabs({ attachments: [], extra_metadata: null }).map((t) => t.label)).toEqual(["Document"]);
  });

  it("studies show Images then one tab per attachment, numbered when repeated", () => {
    const tabs = viewTabs({ attachments: [att(5), att(6)], extra_metadata: {} });
    expect(tabs.map((t) => [t.key, t.label])).toEqual([
      ["main", "Images"], ["5", "Report 1"], ["6", "Report 2"],
    ]);
    expect(viewTabs({ attachments: [att(5)], extra_metadata: {} })[1].label).toBe("Report");
  });

  it("picks the tab from the query parameter, falling back to the first", () => {
    const tabs = viewTabs({ attachments: [att(5)], extra_metadata: {} });
    expect(activeViewTab(tabs, "5").key).toBe("5");
    expect(activeViewTab(tabs, "99").key).toBe("main");
    expect(activeViewTab(tabs, null).key).toBe("main");
  });
});

describe("documentLink", () => {
  it("adds the tab only for attachment hits", () => {
    expect(documentLink("d1")).toBe("/documents/d1");
    expect(documentLink("d1", null)).toBe("/documents/d1");
    expect(documentLink("d1", 5)).toBe("/documents/d1?tab=5");
  });
});

describe("studyRows", () => {
  it("lists known fields in order, then the series", () => {
    expect(
      studyRows({ modality: "DX", patient_name: "Mario Rossi", series: [{ description: "AP", images: 2 }], other: "x" }),
    ).toEqual([["Patient", "Mario Rossi"], ["Modality", "DX"], ["Series", "AP (2)"]]);
    expect(studyRows(null)).toEqual([]);
  });
});
