import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { TagManager } from "./TagManager";

const mutate = vi.fn();
vi.mock("@/hooks/useTags", () => ({
  useTags: () => ({
    data: [
      { id: 3, name: "Fatture", color: "#ff0000" },
      { id: 7, name: "Salute", color: "#00ff00" },
    ],
  }),
  useCreateTag: () => ({ mutate: vi.fn() }),
  useDeleteTag: () => ({ mutate }),
}));

afterEach(() => {
  mutate.mockClear();
  vi.restoreAllMocks();
});

function renderAt(entry: string) {
  const qc = new QueryClient();
  const router = createMemoryRouter(
    [{ path: "*", element: <QueryClientProvider client={qc}><TagManager /></QueryClientProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

it("links each tag to the explorer filtered by it", () => {
  renderAt("/");
  expect(screen.getByRole("link", { name: "Fatture" })).toHaveAttribute("href", "/?all=1&tag=3");
  expect(screen.getByRole("link", { name: "Salute" })).toHaveAttribute("href", "/?all=1&tag=7");
});

it("marks the tag filtering the browse page as active", () => {
  renderAt("/?all=1&tag=7");
  expect(screen.getByRole("link", { name: "Salute" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Fatture" })).not.toHaveAttribute("aria-current");
});

it("does not mark a tag active outside the browse page", () => {
  renderAt("/documents/5?tag=7");
  expect(screen.getByRole("link", { name: "Salute" })).not.toHaveAttribute("aria-current");
});

it("deletes after confirm without navigating", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const router = renderAt("/somewhere");
  const row = screen.getByRole("link", { name: "Fatture" }).closest("li")!;
  await userEvent.click(row.querySelector("button")!);
  expect(mutate).toHaveBeenCalledWith(3);
  expect(router.state.location.pathname).toBe("/somewhere");
});
