import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { PageCarousel } from "./PageCarousel";

vi.mock("@/hooks/usePreviewImage", () => ({ usePreviewImage: () => null }));

const pages = [
  { id: 1, page_number: 1 },
  { id: 2, page_number: 2 },
  { id: 3, page_number: 3 },
];

it("moves the selected page with the arrow buttons", async () => {
  const onMove = vi.fn();
  render(
    <PageCarousel pages={pages} selectedPageId={2} disabled={false} onSelect={vi.fn()} onDelete={vi.fn()} onMove={onMove} />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Move page 2 left" }));
  expect(onMove).toHaveBeenLastCalledWith(2, 0);
  await userEvent.click(screen.getByRole("button", { name: "Move page 2 right" }));
  expect(onMove).toHaveBeenLastCalledWith(2, 2);
});

it("hides the arrows on unselected pages and disables them at the ends", () => {
  render(
    <PageCarousel pages={pages} selectedPageId={1} disabled={false} onSelect={vi.fn()} onDelete={vi.fn()} onMove={vi.fn()} />,
  );
  expect(screen.getByRole("button", { name: "Move page 1 left" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Move page 2 left" })).not.toBeInTheDocument();
});
