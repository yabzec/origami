import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ChatInput } from "./ChatInput";

it("Enter sends the trimmed text and Shift+Enter adds a new line", async () => {
  const onSend = vi.fn();
  render(<ChatInput streaming={false} onSend={onSend} onStop={vi.fn()} />);
  const box = screen.getByRole("textbox", { name: "Message" });
  await userEvent.type(box, "riga uno{Shift>}{Enter}{/Shift}riga due");
  expect(box).toHaveValue("riga uno\nriga due");
  expect(onSend).not.toHaveBeenCalled();
  await userEvent.type(box, "  {Enter}");
  expect(onSend).toHaveBeenCalledWith("riga uno\nriga due");
  expect(box).toHaveValue("");
});

it("while streaming the input is disabled and Stop is offered", async () => {
  const onStop = vi.fn();
  render(<ChatInput streaming onSend={vi.fn()} onStop={onStop} />);
  expect(screen.getByRole("textbox", { name: "Message" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Stop" }));
  expect(onStop).toHaveBeenCalledOnce();
});
