import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { BatchUploadDialog } from "./BatchUploadDialog";

vi.mock("@/hooks/useOcrLanguages", () => ({
  useOcrLanguages: () => ({
    data: { languages: [{ code: "ita", name: "Italian" }], default: "ita", translation_languages: [{ code: "it", name: "Italian" }], translation_default: "it" },
  }),
}));
vi.mock("@/hooks/useFolders", () => ({ useFolders: () => ({ data: [] }) }));
vi.mock("@/hooks/useTags", () => ({ useTags: () => ({ data: [] }), useCreateTag: () => ({ mutateAsync: vi.fn() }) }));

function renderDialog(picked: { file: File; relativePath: string }[]) {
  const router = createMemoryRouter([
    { path: "/", element: <BatchUploadDialog picked={picked} open onClose={() => {}} initialFolderId={null} /> },
  ]);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("BatchUploadDialog", () => {
  it("lists accepted and skipped files before upload", () => {
    renderDialog([
      { file: new File(["x"], "a.pdf"), relativePath: "Bills/a.pdf" },
      { file: new File(["x"], "b.exe"), relativePath: "Bills/b.exe" },
    ]);
    expect(screen.getByText("Bills/a.pdf")).toBeInTheDocument();
    expect(screen.getByText(/Bills\/b\.exe.*unsupported type \.exe/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload 1 file" })).toBeEnabled();
  });
});
