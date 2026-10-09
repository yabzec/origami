import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentInstallPanel } from "./AgentInstallPanel";

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
  );
  fetchMock.mockImplementation(
    async () =>
      new Response(JSON.stringify({ platforms: ["windows-amd64", "linux-amd64"] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AgentInstallPanel onInstalled={() => {}} />
    </QueryClientProvider>,
  );
}

it("links built platforms and marks the others as not built", async () => {
  renderPanel();
  expect(await screen.findByText("macOS (Apple silicon): not built on this server")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Download for macOS/ })).not.toBeInTheDocument();
  expect(screen.getByText("macOS (Intel): not built on this server")).toBeInTheDocument();
  expect(screen.getByText("Linux (ARM64): not built on this server")).toBeInTheDocument();
  const windows = screen.getByRole("link", { name: "Windows" });
  expect(windows).toHaveAttribute("href", expect.stringContaining("/api/agent/download/windows-amd64"));
  expect(windows).toHaveAttribute("download");
  expect(screen.getByRole("link", { name: "Linux (x86-64)" })).toHaveAttribute("download");
  expect(fetchMock.mock.calls[0][0]).toContain("/api/agent/downloads");
});

it("links the detected platform when it is built", async () => {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
  renderPanel();
  const link = await screen.findByRole("link", { name: "Download for Windows" });
  expect(link).toHaveAttribute("download");
});

it("shows the install steps for the platform that was downloaded", async () => {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (X11; Linux x86_64)");
  const stopNavigation = (e: Event) => e.preventDefault(); // jsdom cannot follow a download link
  document.addEventListener("click", stopNavigation);
  try {
    renderPanel();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("link", { name: "Download for Linux (x86-64)" }));
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent("chmod +x ~/.local/share/origami-agent/origami-agent-linux-amd64");
    fireEvent.click(screen.getByRole("link", { name: "Windows" }));
    expect(screen.getByRole("status")).toHaveTextContent("origami-agent-windows-amd64.exe");
  } finally {
    document.removeEventListener("click", stopNavigation);
  }
});
