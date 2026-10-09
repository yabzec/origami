import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { initialSearch, type SearchState } from "@/lib/localScan";
import type { ScanDevice } from "@/lib/types";
import { DeviceListbox } from "./DeviceListbox";

const server: ScanDevice = { id: "fake:0", name: "Server Epson" };
const local: ScanDevice = { id: "agent:abcdefgh:u1", name: "Home HP" };

function setup(devices: ScanDevice[], search: SearchState = initialSearch) {
  const onChange = vi.fn();
  const onSearch = vi.fn();
  const onOpen = vi.fn();
  const view = render(
    <DeviceListbox
      devices={devices}
      value="fake:0"
      onChange={onChange}
      search={search}
      onSearch={onSearch}
      onOpen={onOpen}
      installPanel={<p>Install panel</p>}
    />,
  );
  return { onChange, onSearch, onOpen, view };
}

describe("DeviceListbox", () => {
  it("shows the selected device and opens with groups", async () => {
    const { onOpen } = setup([server, local]);
    const button = screen.getByRole("button", { name: /scanner/i });
    expect(button).toHaveTextContent("Server Epson");
    await userEvent.click(button);
    expect(onOpen).toHaveBeenCalled();
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(screen.getByText("Server")).toBeInTheDocument();
    expect(screen.getByText("This computer's network")).toBeInTheDocument();
  });

  it("selecting a device closes the list", async () => {
    const { onChange } = setup([server, local]);
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    await userEvent.click(screen.getByRole("option", { name: "Home HP" }));
    expect(onChange).toHaveBeenCalledWith("agent:abcdefgh:u1");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("search keeps the list open and new devices appear in place", async () => {
    const { onSearch, view } = setup([server]);
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    await userEvent.click(screen.getByRole("option", { name: /search local scanners/i }));
    expect(onSearch).toHaveBeenCalled();
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    view.rerender(
      <DeviceListbox
        devices={[server, local]}
        value="fake:0"
        onChange={() => {}}
        search={{ phase: "found", startedAt: 0, found: 1 }}
        onSearch={onSearch}
        onOpen={() => {}}
        installPanel={<p>Install panel</p>}
      />,
    );
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Home HP" })).toBeInTheDocument();
    expect(screen.getByText("1 found")).toBeInTheDocument();
  });

  it("shows the install panel inside the open list", async () => {
    setup([server], { phase: "install", startedAt: null, found: 0 });
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    expect(screen.getByText("Install panel")).toBeInTheDocument();
  });

  it("keyboard: arrows move, Enter on search keeps it open, Escape closes", async () => {
    const { onSearch, onChange } = setup([server, local]);
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    await userEvent.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(onSearch).toHaveBeenCalled();
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await userEvent.keyboard("{ArrowUp}{Enter}");
    expect(onChange).toHaveBeenCalledWith("agent:abcdefgh:u1");
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("shows status text for unresponsive and none", async () => {
    const { view } = setup([server], { phase: "unresponsive", startedAt: 0, found: 0 });
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    expect(screen.getByText(/agent not responding/i)).toBeInTheDocument();
    view.rerender(
      <DeviceListbox devices={[server]} value="fake:0" onChange={() => {}}
        search={{ phase: "none", startedAt: 0, found: 0 }} onSearch={() => {}} onOpen={() => {}}
        installPanel={null} />,
    );
    expect(screen.getByText(/no scanners found/i)).toBeInTheDocument();
  });
});
