import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import { PropertiesPanel } from "./PropertiesPanel";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, patch: vi.fn() } };
});

const PROPS = [
  { key: "Patient", value: "Mario Rossi" },
  { key: "Modality", value: "DX" },
];

function renderPanel(properties = PROPS) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <PropertiesPanel doc={{ id: "d1", properties }} />
    </QueryClientProvider>,
  );
  return { invalidate };
}

afterEach(() => vi.mocked(api.patch).mockReset());

describe("PropertiesPanel", () => {
  it("shows the rows in a scrolling frame", () => {
    renderPanel();
    const frame = screen.getByTestId("properties-frame");
    expect(frame).toHaveClass("max-h-64", "overflow-y-auto");
    expect(within(frame).getByText("Patient")).toBeInTheDocument();
    expect(within(frame).getByText("Mario Rossi")).toBeInTheDocument();
  });

  it("shows an empty state", () => {
    renderPanel([]);
    expect(screen.getByText("No properties")).toBeInTheDocument();
  });

  it("edits, adds and deletes rows, then saves the normalized list", async () => {
    vi.mocked(api.patch).mockResolvedValueOnce({});
    const { invalidate } = renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByTestId("properties-frame")).toHaveClass("max-h-64", "overflow-y-auto");
    await userEvent.click(screen.getByRole("button", { name: "Delete row Modality" }));
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    const keys = screen.getAllByRole("textbox", { name: "Key" });
    const values = screen.getAllByRole("textbox", { name: "Value" });
    await userEvent.type(keys[1], " Colore ");
    await userEvent.type(values[1], "blu");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(api.patch).toHaveBeenCalledWith("/api/documents/d1", {
      properties: [{ key: "Patient", value: "Mario Rossi" }, { key: "Colore", value: "blu" }],
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["document", "d1"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["documents"] });
    expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("blocks save with an inline validation error", async () => {
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    await userEvent.type(screen.getAllByRole("textbox", { name: "Key" })[2], "patient");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText("Duplicate key: patient")).toBeInTheDocument();
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("shows the server error message", async () => {
    vi.mocked(api.patch).mockRejectedValueOnce(new ApiError(422, "validation_error", "Request validation failed"));
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Request validation failed")).toBeInTheDocument();
  });

  it("cancel restores the server rows", async () => {
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.clear(screen.getAllByRole("textbox", { name: "Value" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "Delete row Modality" }));
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Mario Rossi")).toBeInTheDocument();
    expect(screen.getByText("DX")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getAllByRole("textbox", { name: "Key" })).toHaveLength(2);
  });
});
