import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TagInput } from "./TagInput";

let tags = [{ id: 1, name: "Casa", color: "#111111" }];
const fetchMock = vi.fn();
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  fetchMock.mockReset();
  tags = [{ id: 1, name: "Casa", color: "#111111" }];
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === "/api/tags" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      const created = { id: 2, name: body.name, color: body.color };
      tags = [...tags, created];
      return json(201, created);
    }
    return json(200, tags);
  });
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ onIds }: { onIds: (ids: number[]) => void }) {
  const [ids, setIds] = useState<number[]>([]);
  return (
    <TagInput
      id="tags"
      value={ids}
      onChange={(next) => {
        setIds(next);
        onIds(next);
      }}
    />
  );
}

function renderInput() {
  const onIds = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <label htmlFor="tags">Tags</label>
      <Harness onIds={onIds} />
    </QueryClientProvider>,
  );
  return { input: screen.getByLabelText("Tags"), onIds };
}

it("selects an existing tag from the suggestions", async () => {
  const { input, onIds } = renderInput();
  await userEvent.type(input, "ca");
  await userEvent.click(await screen.findByRole("option", { name: "Casa" }));
  expect(onIds).toHaveBeenLastCalledWith([1]);
  expect(screen.getByText("Casa")).toBeInTheDocument(); // chip
});

it("creates a new tag on Enter and selects it", async () => {
  const { input, onIds } = renderInput();
  await userEvent.type(input, "Bollette{Enter}");
  await waitFor(() => expect(onIds).toHaveBeenLastCalledWith([2]));
  expect(fetchMock).toHaveBeenCalledWith("/api/tags", expect.objectContaining({ method: "POST" }));
});

it("selects an existing tag on Enter instead of creating a duplicate", async () => {
  const { input, onIds } = renderInput();
  await userEvent.type(input, "ca");
  await screen.findByRole("option", { name: "Casa" }); // tags have loaded
  await userEvent.type(input, "sa{Enter}");
  expect(onIds).toHaveBeenLastCalledWith([1]);
  expect(fetchMock).not.toHaveBeenCalledWith("/api/tags", expect.objectContaining({ method: "POST" }));
});

it("removes a chip", async () => {
  const { input, onIds } = renderInput();
  await userEvent.type(input, "ca");
  await screen.findByRole("option", { name: "Casa" }); // tags have loaded
  await userEvent.type(input, "sa{Enter}");
  await userEvent.click(await screen.findByRole("button", { name: "Remove Casa" }));
  expect(onIds).toHaveBeenLastCalledWith([]);
});

it("picks the arrow-highlighted suggestion on Enter instead of creating", async () => {
  const { input, onIds } = renderInput();
  await userEvent.type(input, "cas");
  await screen.findByRole("option", { name: "Casa" });
  await userEvent.keyboard("{ArrowDown}{Enter}");
  expect(onIds).toHaveBeenLastCalledWith([1]);
  expect(fetchMock).not.toHaveBeenCalledWith("/api/tags", expect.objectContaining({ method: "POST" }));
});

it("selects the existing tag when creation returns 409", async () => {
  const { input, onIds } = renderInput();
  await userEvent.type(input, "x");
  await screen.findByRole("option", { name: /Create/ }); // tags have loaded
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === "/api/tags" && init?.method === "POST") {
      tags = [...tags, { id: 7, name: "Bollette", color: "#222222" }]; // created elsewhere
      return json(409, { error: { code: "duplicate_tag", message: "Tag with same name exists" } });
    }
    return json(200, tags);
  });
  await userEvent.clear(input);
  await userEvent.type(input, "Bollette{Enter}");
  await waitFor(() => expect(onIds).toHaveBeenLastCalledWith([7]));
  expect(screen.queryByText("Tag with same name exists")).not.toBeInTheDocument();
  expect(screen.queryByText("Could not create tag")).not.toBeInTheDocument();
});
