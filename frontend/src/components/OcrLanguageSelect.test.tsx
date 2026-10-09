import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OcrLanguageSelect } from "./OcrLanguageSelect";

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(
    async () =>
      new Response(
        JSON.stringify({
          languages: [
            { code: "deu", name: "German" },
            { code: "eng", name: "English" },
            { code: "ita", name: "Italian" },
          ],
          default: "ita+eng",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
  );
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ onValue }: { onValue: (v: string) => void }) {
  const [value, setValue] = useState("ita");
  return (
    <OcrLanguageSelect
      id="lang"
      value={value}
      onChange={(v) => {
        setValue(v);
        onValue(v);
      }}
    />
  );
}

it("checks languages in click order and keeps at least one", async () => {
  const onValue = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Harness onValue={onValue} />
    </QueryClientProvider>,
  );
  const trigger = await screen.findByRole("button", { name: /Italian/ });
  await userEvent.click(trigger);
  await userEvent.click(await screen.findByRole("checkbox", { name: "German" }));
  expect(onValue).toHaveBeenLastCalledWith("ita+deu");
  await userEvent.click(screen.getByRole("checkbox", { name: "Italian" }));
  expect(onValue).toHaveBeenLastCalledWith("deu");
  expect(screen.getByRole("checkbox", { name: "German" })).toBeDisabled(); // the only one left
});
