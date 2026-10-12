import { describe, expect, it } from "vitest";
import {
  batchForm,
  canImport,
  commitBody,
  discFiles,
  discPath,
  discProcessing,
  draftFromStudy,
  keepDiscFile,
  manualFiles,
  mergeDrafts,
  reportsFor,
  uploadBatches,
  DISC_MAX_FILE_BYTES,
  type StagedFile,
} from "./discImport";
import type { DiscPreview, DiscStudy } from "./types";

const file = (name: string, size = 1) => new File([new Uint8Array(size)], name);

const study = (over: Partial<DiscStudy> = {}): DiscStudy => ({
  key: "s1",
  title: "Rx gamba — 2026-08-28",
  document_date: "2026-08-28",
  description_fallback: "Rx gamba · Ospedale",
  patient_tag: "Mario Rossi",
  patient_tag_id: null,
  extra_metadata: {},
  images: [
    { key: "i1", path: "DICOM/a", kind: "dicom", label: "AP", readable: true, frames: 1 },
    { key: "i2", path: "DICOM/b", kind: "dicom", label: "Lateral", readable: true, frames: 1 },
    { key: "i3", path: "DICOM/c", kind: "dicom", label: "Odd", readable: false, frames: 1 },
  ],
  ...over,
});

const preview = (over: Partial<DiscPreview> = {}): DiscPreview => ({
  id: "imp",
  no_study: false,
  studies: [study()],
  reports: [
    { key: "r1", path: "REPORT/r.pdf", filename: "r.pdf", page_count: 1, has_text: true, study_key: "s1", preselected: true },
    { key: "r2", path: "other.pdf", filename: "other.pdf", page_count: 1, has_text: true, study_key: null, preselected: false },
  ],
  warnings: [],
  ...over,
});

describe("disc file selection", () => {
  it("strips the picked folder name before filtering", () => {
    expect(discPath("CDROM/README.TXT")).toBe("README.TXT");
    expect(discPath("README.TXT")).toBe("README.TXT");
    const kept = discFiles([
      { file: file("README.TXT"), relativePath: "CDROM/README.TXT" },
      { file: file("x.dll"), relativePath: "CDROM/VIEWERS/x.dll" },
      { file: file("a"), relativePath: "CDROM/DICOM/1/2/a" },
    ]).map((f) => f.path);
    expect(kept).toEqual(["README.TXT", "DICOM/1/2/a"]);
  });

  it("keeps DICOM, reports, vendor pages and JPEG copies; drops the viewer and decorations", () => {
    const keep = (path: string) => keepDiscFile(path, 10);
    expect(keep("DICOMDIR")).toBe(true);
    expect(keep("DICOM/12840113/56410336/89864915")).toBe(true);
    expect(keep("REPORT/8499735.pdf")).toBe(true);
    expect(keep("help.pdf")).toBe(true); // the server drops viewer manuals
    expect(keep("IHE_PDI/Study000.htm")).toBe(true);
    expect(keep("IHE_PDI/1/2/3/a.jpg")).toBe(true);
    expect(keep("IHE_PDI/Left.jpg")).toBe(false);
    expect(keep("IHE_PDI/1/2/000_001.htm")).toBe(false);
    expect(keep("REPORT/8499735.p7m")).toBe(false);
    expect(keep("REPORT/8499735.tsr")).toBe(false);
    expect(keep("VIEWERS/lang/it.pdf")).toBe(false);
    expect(keep("COMMON/LICENSE")).toBe(false);
    expect(keep("start.exe")).toBe(false);
    expect(keep("INDEX.BAK")).toBe(false);
    expect(keep("IHE_PDI/Thumbs.db")).toBe(false);
    expect(keep("scan.dcm")).toBe(true);
    expect(keepDiscFile("DICOM/huge", DISC_MAX_FILE_BYTES + 1)).toBe(false);
  });

  it("puts manual picks under manual/", () => {
    expect(manualFiles([file("a.jpg")]).map((f) => f.path)).toEqual(["manual/a.jpg"]);
  });
});

describe("upload batches", () => {
  const staged = (n: number, size: number): StagedFile[] =>
    Array.from({ length: n }, (_, i) => ({ file: file(`f${i}`, size), path: `DICOM/f${i}` }));

  it("splits by bytes and by count", () => {
    expect(uploadBatches(staged(5, 10), 25, 100).map((b) => b.length)).toEqual([2, 2, 1]);
    expect(uploadBatches(staged(5, 1), 1000, 2).map((b) => b.length)).toEqual([2, 2, 1]);
    expect(uploadBatches(staged(1, 99), 10, 10).map((b) => b.length)).toEqual([1]); // a big file goes alone
    expect(uploadBatches([], 10, 10)).toEqual([]);
  });

  it("sends one path per file", () => {
    const form = batchForm(staged(2, 1));
    expect(form.getAll("paths")).toEqual(["DICOM/f0", "DICOM/f1"]);
    expect(form.getAll("files")).toHaveLength(2);
  });
});

describe("drafts", () => {
  it("defaults: OCR and translation off, summary on", () => {
    const p = discProcessing();
    expect([p.ocrEnabled, p.translationEnabled, p.summaryEnabled]).toEqual([false, false, true]);
  });

  it("starts from the proposed metadata, readable images and preselected reports", () => {
    const d = draftFromStudy(study(), preview().reports, 7);
    expect(d).toMatchObject({
      key: "s1", include: true, title: "Rx gamba — 2026-08-28", documentDate: "2026-08-28",
      folderId: 7, tagIds: [], addPatientTag: true, imageKeys: ["i1", "i2"], reportKeys: ["r1"],
    });
    const tagged = draftFromStudy(study({ patient_tag_id: 4 }), [], null);
    expect([tagged.tagIds, tagged.addPatientTag]).toEqual([[4], false]);
  });

  it("offers own and unassigned reports", () => {
    expect(reportsFor(study(), preview().reports).map((r) => r.key)).toEqual(["r1", "r2"]);
    expect(reportsFor(study({ key: "s2" }), preview().reports).map((r) => r.key)).toEqual(["r2"]);
  });

  it("keeps edits and selects only newly added files after Add files", () => {
    const before = preview();
    const edited = { ...draftFromStudy(study(), before.reports, null), title: "Mine", imageKeys: ["i1"] };
    const after = preview({
      studies: [study({ images: [...study().images, { key: "i4", path: "manual/x.jpg", kind: "image", label: "x.jpg", readable: true, frames: 1 }] })],
      reports: [...before.reports, { key: "r3", path: "manual/m.pdf", filename: "m.pdf", page_count: 1, has_text: true, study_key: "s1", preselected: true }],
    });
    const [merged] = mergeDrafts([edited], before, after, null);
    expect(merged.title).toBe("Mine");
    expect(merged.imageKeys).toEqual(["i1", "i4"]);
    expect(merged.reportKeys).toEqual(["r1", "r3"]);
  });

  it("can import only with a titled study that has images", () => {
    const d = draftFromStudy(study(), [], null);
    expect(canImport([d])).toBe(true);
    expect(canImport([{ ...d, title: " " }])).toBe(false);
    expect(canImport([{ ...d, imageKeys: [] }])).toBe(false);
    expect(canImport([{ ...d, include: false }])).toBe(false);
  });

  it("builds the commit body in study image order with the patient tag", () => {
    const d = { ...draftFromStudy(study(), preview().reports, 3), imageKeys: ["i2", "i1"], notes: "n" };
    const body = commitBody([d], preview(), { s1: 9 }, discProcessing());
    expect(body.studies[0]).toEqual({
      study_key: "s1", title: "Rx gamba — 2026-08-28", document_date: "2026-08-28", notes: "n",
      folder_id: 3, tag_ids: [9], image_keys: ["i1", "i2"], report_keys: ["r1"],
    });
    expect(body.ocr_enabled).toBe(false);
    expect(body.summary_enabled).toBe(true);
  });
});
