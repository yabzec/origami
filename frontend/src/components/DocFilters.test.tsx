import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { DocFilters, FilterField } from "./DocFilters";

const TAGS = [{ id: 7, name: "casa", color: "#888" }];
const EMPTY = { tagId: null, docType: null, dateFrom: null, dateTo: null };

it("labels the date fields and the leading controls", () => {
  render(
    <DocFilters value={EMPTY} tags={TAGS} onChange={vi.fn()}>
      <FilterField label="Order by">
        <select />
      </FilterField>
    </DocFilters>,
  );
  expect(screen.getByLabelText("Order by")).toBeInTheDocument();
  expect(screen.getByLabelText("From")).toHaveAttribute("type", "date");
  expect(screen.getByLabelText("To")).toHaveAttribute("type", "date");
});

it("reports each change as a patch", async () => {
  const onChange = vi.fn();
  render(<DocFilters value={EMPTY} tags={TAGS} onChange={onChange} />);
  await userEvent.selectOptions(screen.getByDisplayValue("All tags"), "casa");
  expect(onChange).toHaveBeenLastCalledWith({ tagId: 7 });
  await userEvent.selectOptions(screen.getByDisplayValue("All types"), "pdf");
  expect(onChange).toHaveBeenLastCalledWith({ docType: "pdf" });
  fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-01-01" } });
  expect(onChange).toHaveBeenLastCalledWith({ dateFrom: "2026-01-01" });
});

it("clears both dates", async () => {
  const onChange = vi.fn();
  render(<DocFilters value={{ ...EMPTY, dateTo: "2026-02-01" }} tags={TAGS} onChange={onChange} />);
  await userEvent.click(screen.getByRole("button", { name: "Clear dates" }));
  expect(onChange).toHaveBeenLastCalledWith({ dateFrom: null, dateTo: null });
});
