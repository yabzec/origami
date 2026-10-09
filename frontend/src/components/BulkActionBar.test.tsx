import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BulkActionBar } from "./BulkActionBar";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderBar(overrides = {}) {
  const props = { count: 3, onSelectAll: vi.fn(), onMove: vi.fn(), onDelete: vi.fn(), onClear: vi.fn(), ...overrides };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BulkActionBar {...props} />
    </QueryClientProvider>,
  );
  return props;
}

it("confirms before deleting", async () => {
  const props = renderBar();
  expect(screen.getByText("3 selected")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(screen.getByRole("dialog", { name: "Delete 3 documents?" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Delete 3" }));
  expect(props.onDelete).toHaveBeenCalled();
});

it("moves to the root by default", async () => {
  const props = renderBar();
  await userEvent.click(screen.getByRole("button", { name: "Move…" }));
  await userEvent.click(screen.getByRole("button", { name: "Move here" }));
  expect(props.onMove).toHaveBeenCalledWith(null);
});
