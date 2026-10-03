import { useEffect, useRef } from "react";
import { Link } from "react-router";
import { Markdown } from "@/components/chat/Markdown";
import type { ChatMessage } from "@/lib/chatReducer";
import { isNearBottom } from "@/lib/scroll";

function TypingIndicator() {
  return (
    <span role="status" aria-label="Assistant is typing" className="inline-flex gap-1 py-1">
      <span className="h-2 w-2 animate-bounce rounded-full bg-zinc-400" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-zinc-400 [animation-delay:150ms]" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-zinc-400 [animation-delay:300ms]" />
    </span>
  );
}

function AssistantMessage({ message, typing }: { message: ChatMessage; typing: boolean }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] space-y-2">
        {message.grounded === false && (
          <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            Answer not based on your documents.
          </div>
        )}
        <div className="rounded-2xl border border-zinc-200 bg-white px-4 py-3">
          {typing ? (
            <TypingIndicator />
          ) : message.content ? (
            <Markdown text={message.content} sources={message.sources} />
          ) : (
            !message.error && <p className="text-sm text-zinc-400">No answer.</p>
          )}
          {message.error && <p className="mt-2 text-sm text-red-700">{message.error}</p>}
        </div>
        {message.sources && message.sources.length > 0 && (
          <ol className="space-y-0.5 pl-1 text-xs text-zinc-500">
            {message.sources.map((source) => (
              <li key={source.n}>
                [{source.n}]{" "}
                <Link to={`/documents/${source.document_id}`} className="underline hover:text-zinc-800">
                  {source.title}
                </Link>
                {source.page_number != null && <span> (p. {source.page_number})</span>}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

export function MessageList({ messages, streaming }: { messages: ChatMessage[]; streaming: boolean }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true); // follow new content unless the user scrolled up
  const countRef = useRef(messages.length);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (messages.length !== countRef.current) {
      countRef.current = messages.length;
      stickRef.current = true; // a new question always brings the view back down
    }
    if (stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (el) stickRef.current = isNearBottom(el.scrollTop, el.scrollHeight, el.clientHeight);
  };

  return (
    <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto px-6 py-4">
      <div className="mx-auto max-w-3xl space-y-4">
        {messages.length === 0 && (
          <p className="pt-8 text-center text-sm text-zinc-400">
            Ask a question about your documents. Origami picks the relevant files; you can add or remove them above.
          </p>
        )}
        {messages.map((message, index) =>
          message.role === "user" ? (
            <div key={message.id} className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl bg-zinc-900 px-4 py-2 text-sm whitespace-pre-wrap text-white">
                {message.content}
              </div>
            </div>
          ) : (
            <AssistantMessage
              key={message.id}
              message={message}
              typing={streaming && index === messages.length - 1 && message.content === ""}
            />
          ),
        )}
      </div>
    </div>
  );
}
