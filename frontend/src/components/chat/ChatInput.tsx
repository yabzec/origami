import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";

const MAX_HEIGHT_PX = 8 * 20 + 24; // 8 rows of text-sm (20 px line height) + p-3 padding

export function ChatInput({
  streaming,
  onSend,
  onStop,
}: {
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [text]);

  useEffect(() => {
    if (!streaming) ref.current?.focus();
  }, [streaming]);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    onSend(trimmed);
    setText("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="border-t border-zinc-200 px-6 py-3"
    >
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          ref={ref}
          aria-label="Message"
          rows={1}
          value={text}
          disabled={streaming}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about your documents… (Shift+Enter for a new line)"
          style={{ maxHeight: MAX_HEIGHT_PX }}
          className="w-full resize-none overflow-y-auto rounded-md border border-zinc-300 bg-white p-3 text-sm leading-5 focus:ring-2 focus:ring-brand-300 focus:outline-none disabled:bg-zinc-50"
        />
        {streaming ? (
          <Button type="button" variant="outline" onClick={onStop}>
            Stop
          </Button>
        ) : (
          <Button type="submit" disabled={!text.trim()}>
            Send
          </Button>
        )}
      </div>
    </form>
  );
}
