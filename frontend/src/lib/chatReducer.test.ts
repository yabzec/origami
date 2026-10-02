import { describe, expect, it } from "vitest";
import { chatReducer, initialChatState, nextMessageId, type ChatState } from "./chatReducer";
import type { ChatSource, DocRef } from "./types";

const BOLLETTA: DocRef = { id: "doc-1", title: "Bolletta marzo", document_date: "2026-03-15" };
const CONTRATTO: DocRef = { id: "doc-2", title: "Contratto", document_date: "2026-01-10" };
const SOURCE: ChatSource = { n: 1, chunk_id: 7, document_id: "doc-1", title: "Bolletta marzo", page_number: 2 };

function send(state: ChatState = initialChatState, text = "Domanda?", userId = "u1", assistantId = "a1"): ChatState {
  return chatReducer(state, { type: "SEND", text, userId, assistantId });
}

describe("chatReducer", () => {
  it("SEND appends the user message and an empty assistant message and starts streaming", () => {
    const state = send();
    expect(state.streaming).toBe(true);
    expect(state.messages).toEqual([
      { id: "u1", role: "user", content: "Domanda?" },
      { id: "a1", role: "assistant", content: "" },
    ]);
  });

  it("SEND is ignored while streaming", () => {
    const state = send();
    expect(send(state, "Altra", "u2", "a2")).toBe(state);
  });

  it("META sets sources and grounded on the assistant message and adds auto documents", () => {
    const state = chatReducer(send(), {
      type: "META",
      messageId: "a1",
      sources: [SOURCE],
      grounded: false,
      autoDocuments: [BOLLETTA],
    });
    expect(state.messages[1]).toEqual({ id: "a1", role: "assistant", content: "", sources: [SOURCE], grounded: false });
    expect(state.auto).toEqual([BOLLETTA]);
  });

  it("META skips documents that are pinned, excluded or already auto", () => {
    const start: ChatState = { ...send(), pinned: [CONTRATTO], auto: [BOLLETTA], excluded: ["doc-3"] };
    const doc3: DocRef = { id: "doc-3", title: "Escluso", document_date: "2026-02-01" };
    const doc4: DocRef = { id: "doc-4", title: "Nuovo", document_date: "2026-02-02" };
    const state = chatReducer(start, {
      type: "META",
      messageId: "a1",
      sources: [],
      grounded: true,
      autoDocuments: [CONTRATTO, BOLLETTA, doc3, doc4, doc4],
    });
    expect(state.auto).toEqual([BOLLETTA, doc4]);
    expect(state.pinned).toEqual([CONTRATTO]);
  });

  it("DELTA appends text to the streaming assistant message", () => {
    let state = chatReducer(send(), { type: "DELTA", messageId: "a1", text: "Ciao " });
    state = chatReducer(state, { type: "DELTA", messageId: "a1", text: "mondo" });
    expect(state.messages[1].content).toBe("Ciao mondo");
    expect(state.streaming).toBe(true);
  });

  it("DONE ends streaming", () => {
    const state = chatReducer(send(), { type: "DONE", messageId: "a1" });
    expect(state.streaming).toBe(false);
  });

  it("ERROR stores the message on the assistant reply and ends streaming", () => {
    const state = chatReducer(send(), { type: "ERROR", messageId: "a1", message: "Chat request failed" });
    expect(state.streaming).toBe(false);
    expect(state.messages[1].error).toBe("Chat request failed");
  });

  it("ABORT ends streaming and keeps the partial text", () => {
    const partial = chatReducer(send(), { type: "DELTA", messageId: "a1", text: "Risposta parz" });
    const state = chatReducer(partial, { type: "ABORT" });
    expect(state.streaming).toBe(false);
    expect(state.messages[1].content).toBe("Risposta parz");
  });

  it("ignores events for a stale message id after Stop and a new send", () => {
    let state = chatReducer(send(), { type: "DELTA", messageId: "a1", text: "Prima" });
    state = chatReducer(state, { type: "ABORT" });
    expect(chatReducer(state, { type: "DELTA", messageId: "a1", text: " tardi" })).toBe(state);
    state = send(state, "Seconda?", "u2", "a2");
    state = chatReducer(state, { type: "DELTA", messageId: "a1", text: " tardi" });
    state = chatReducer(state, { type: "DONE", messageId: "a1" });
    state = chatReducer(state, { type: "DELTA", messageId: "a2", text: "Nuova" });
    expect(state.messages.map((m) => m.content)).toEqual(["Domanda?", "Prima", "Seconda?", "Nuova"]);
    expect(state.streaming).toBe(true);
  });

  it("PIN adds the document once and removes it from auto and excluded", () => {
    const start: ChatState = { ...initialChatState, auto: [BOLLETTA], excluded: ["doc-1"] };
    const state = chatReducer(start, { type: "PIN", doc: BOLLETTA });
    expect(state.pinned).toEqual([BOLLETTA]);
    expect(state.auto).toEqual([]);
    expect(state.excluded).toEqual([]);
    expect(chatReducer(state, { type: "PIN", doc: BOLLETTA })).toBe(state);
  });

  it("REMOVE of a pinned document does not exclude it", () => {
    const state = chatReducer({ ...initialChatState, pinned: [CONTRATTO] }, { type: "REMOVE", id: "doc-2" });
    expect(state.pinned).toEqual([]);
    expect(state.excluded).toEqual([]);
  });

  it("REMOVE of an auto document excludes it and a later META never re-adds it", () => {
    let state = chatReducer({ ...initialChatState, auto: [BOLLETTA] }, { type: "REMOVE", id: "doc-1" });
    expect(state.auto).toEqual([]);
    expect(state.excluded).toEqual(["doc-1"]);
    state = send(state);
    state = chatReducer(state, { type: "META", messageId: "a1", sources: [], grounded: true, autoDocuments: [BOLLETTA] });
    expect(state.auto).toEqual([]);
    expect(state.excluded).toEqual(["doc-1"]);
  });

  it("NEW_CHAT resets to the initial state", () => {
    const busy: ChatState = { ...send(), pinned: [CONTRATTO], auto: [BOLLETTA], excluded: ["doc-3"] };
    expect(chatReducer(busy, { type: "NEW_CHAT" })).toEqual(initialChatState);
  });

  it("nextMessageId returns unique ids", () => {
    const ids = new Set(Array.from({ length: 50 }, () => nextMessageId()));
    expect(ids.size).toBe(50);
  });
});
