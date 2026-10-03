import { useEffect, useReducer, useRef } from "react";
import { ChatInput } from "@/components/chat/ChatInput";
import { ContextBar } from "@/components/chat/ContextBar";
import { MessageList } from "@/components/chat/MessageList";
import { Button } from "@/components/ui/button";
import { getToken } from "@/lib/api";
import { chatReducer, initialChatState, nextMessageId } from "@/lib/chatReducer";
import { buildChatRequest } from "@/lib/chatRequest";
import { parseSSEStream } from "@/lib/sse";

const NEW_CHAT_CONFIRM = "Start a new chat? The current conversation will be cleared.";

export function ChatPage() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []); // leaving the page stops the stream

  const send = async (text: string) => {
    if (state.streaming) return;
    const body = buildChatRequest(state, text);
    const assistantId = nextMessageId();
    dispatch({ type: "SEND", text, userId: nextMessageId(), assistantId });
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken() ?? ""}` },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        dispatch({ type: "ERROR", messageId: assistantId, message: "Chat request failed" });
        return;
      }
      for await (const event of parseSSEStream(resp.body)) {
        if (controller.signal.aborted) break;
        if (event.type === "meta") {
          dispatch({
            type: "META",
            messageId: assistantId,
            sources: event.sources,
            grounded: event.grounded,
            autoDocuments: event.auto_documents,
          });
        } else if (event.type === "delta") {
          dispatch({ type: "DELTA", messageId: assistantId, text: event.text });
        } else if (event.type === "error") {
          dispatch({ type: "ERROR", messageId: assistantId, message: event.message });
        } else if (event.type === "done") {
          dispatch({ type: "DONE", messageId: assistantId });
        }
      }
      dispatch({ type: "DONE", messageId: assistantId }); // stream closed without "done"; no-op otherwise
    } catch {
      if (!controller.signal.aborted) {
        dispatch({ type: "ERROR", messageId: assistantId, message: "Connection lost mid-answer" });
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  };

  const stop = () => {
    controllerRef.current?.abort();
    dispatch({ type: "ABORT" });
  };

  const newChat = () => {
    if (state.messages.length > 0 && !window.confirm(NEW_CHAT_CONFIRM)) return;
    controllerRef.current?.abort();
    dispatch({ type: "NEW_CHAT" });
  };

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-zinc-200 px-6 py-3">
        <h2 className="text-lg font-semibold">Ask your documents</h2>
        <Button variant="outline" size="sm" onClick={newChat}>
          New chat
        </Button>
      </header>
      <ContextBar
        pinned={state.pinned}
        auto={state.auto}
        onPin={(doc) => dispatch({ type: "PIN", doc })}
        onRemove={(id) => dispatch({ type: "REMOVE", id })}
      />
      <MessageList messages={state.messages} streaming={state.streaming} />
      <ChatInput streaming={state.streaming} onSend={send} onStop={stop} />
    </div>
  );
}
