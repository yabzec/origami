import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { DiscPreview } from "@/lib/types";
import { DiscImportDialog } from "./DiscImportDialog";

vi.mock("@/hooks/useOcrLanguages", () => ({
  useOcrLanguages: () => ({
    data: { languages: [{ code: "ita", name: "Italian" }], default: "ita", translation_languages: [{ code: "it", name: "Italian" }], translation_default: "it" },
  }),
}));
vi.mock("@/hooks/useFolders", () => ({ useFolders: () => ({ data: [] }) }));
vi.mock("@/hooks/useTags", () => ({
  useTags: () => ({ data: [], refetch: vi.fn() }),
  useCreateTag: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { get: vi.fn(), post: vi.fn(), upload: vi.fn(), del: vi.fn(() => Promise.resolve()) } };
});

const PREVIEW: DiscPreview = {
  id: "imp",
  no_study: false,
  studies: [
    {
      key: "s1",
      title: "Rx gamba — 2026-08-28",
      document_date: "2026-08-28",
      description_fallback: "",
      properties: [],
      images: [{ key: "i1", path: "DICOM/a", kind: "dicom", label: "AP", readable: true, frames: 1 }],
    },
  ],
  reports: [{ key: "r1", path: "REPORT/r.pdf", filename: "r.pdf", page_count: 1, has_text: false, study_key: "s1", preselected: true }],
  warnings: [],
};

const picked = [
  { file: new File(["x"], "README.TXT"), relativePath: "CD/README.TXT" },
  { file: new File(["x"], "start.exe"), relativePath: "CD/start.exe" },
  { file: new File(["x"], "a"), relativePath: "CD/DICOM/a" },
];

function renderDialog(onClose: () => void = () => {}) {
  const router = createMemoryRouter([
    { path: "/", element: <DiscImportDialog picked={picked} onClose={onClose} initialFolderId={null} /> },
  ]);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("DiscImportDialog", () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    vi.mocked(api.upload).mockReset();
  });

  it("uploads the useful files and shows the proposed study with import defaults", async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ id: "imp" });
    vi.mocked(api.upload).mockResolvedValueOnce(PREVIEW);
    renderDialog();
    expect(await screen.findByDisplayValue("Rx gamba — 2026-08-28")).toBeInTheDocument();
    const form = vi.mocked(api.upload).mock.calls[0][1] as FormData;
    expect(form.getAll("paths")).toEqual(["README.TXT", "DICOM/a"]);
    expect(screen.getByLabelText("OCR (extract text)")).not.toBeChecked();
    expect(screen.getByLabelText("AI summary")).toBeChecked();
    expect(screen.getByLabelText("Translation")).not.toBeChecked();
    expect(screen.getByText(/This report has no text/)).toBeInTheDocument();
  });

  it("asks for manual files when no study is found", async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ id: "imp" });
    vi.mocked(api.upload).mockResolvedValueOnce({ ...PREVIEW, no_study: true, studies: [], reports: [] });
    renderDialog();
    expect(await screen.findByText(/No X-ray study found on this disc/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
  });

  it("commits the study without tags", async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ id: "imp" })
      .mockResolvedValueOnce({ documents: [{ id: "d1", title: "Rx gamba — 2026-08-28" }] });
    vi.mocked(api.upload).mockResolvedValueOnce(PREVIEW);
    renderDialog();
    await userEvent.click(await screen.findByRole("button", { name: "Import" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Rx gamba — 2026-08-28" })).toBeInTheDocument());
    const [path, body] = vi.mocked(api.post).mock.calls[1] as [string, { studies: { tag_ids: number[]; image_keys: string[]; report_keys: string[] }[] }];
    expect(path).toBe("/api/disc-imports/imp/commit");
    expect(body.studies[0]).toMatchObject({ tag_ids: [], image_keys: ["i1"], report_keys: ["r1"] });
  });

  it("does not delete the staging when closed during the commit", async () => {
    let finish: (v: unknown) => void = () => {};
    vi.mocked(api.post)
      .mockResolvedValueOnce({ id: "imp" })
      .mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    vi.mocked(api.upload).mockResolvedValueOnce(PREVIEW);
    const onClose = vi.fn();
    renderDialog(onClose);
    await userEvent.click(await screen.findByRole("button", { name: "Import" }));
    expect(await screen.findByRole("button", { name: "Cancel" })).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(api.del).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    finish({ documents: [] });
  });

  it("closes with Escape after a successful import without deleting the committed import", async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ id: "imp" })
      .mockResolvedValueOnce({ documents: [{ id: "d1", title: "Rx gamba — 2026-08-28" }] });
    vi.mocked(api.upload).mockResolvedValueOnce(PREVIEW);
    vi.mocked(api.del).mockClear();
    const onClose = vi.fn();
    renderDialog(onClose);
    await userEvent.click(await screen.findByRole("button", { name: "Import" }));
    await screen.findByRole("link", { name: "Rx gamba — 2026-08-28" });
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
    expect(api.del).not.toHaveBeenCalled();
  });

  it("deletes the import and skips the upload when closed before it was created", async () => {
    let created: (v: { id: string }) => void = () => {};
    vi.mocked(api.post).mockReturnValueOnce(new Promise((resolve) => (created = resolve)));
    vi.mocked(api.del).mockClear();
    const onClose = vi.fn();
    renderDialog(onClose);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
    created({ id: "imp" });
    await waitFor(() => expect(api.del).toHaveBeenCalledWith("/api/disc-imports/imp"));
    expect(api.del).toHaveBeenCalledTimes(1);
    expect(api.upload).not.toHaveBeenCalled();
  });
});
