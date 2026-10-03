import { describe, expect, it } from "vitest";
import type { ChatMessage } from "./chatReducer";
import { buildChatRequest } from "./chatRequest";

const msg = (id: string, role: ChatMessage["role"], content: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id,
  role,
  content,
  ...extra,
});

describe("buildChatRequest", () => {
  it("appends the new question and sends pinned and excluded ids", () => {
    const body = buildChatRequest(
      {
        messages: [msg("u1", "user", "Prima?"), msg("a1", "assistant", "Risposta.")],
        pinned: [{ id: "doc-2", title: "Contratto", document_date: "2026-01-10" }],
        auto: [],
        excluded: ["doc-1"],
      },
      "Seconda?",
    );
    expect(body).toEqual({
      messages: [
        { role: "user", content: "Prima?" },
        { role: "assistant", content: "Risposta." },
        { role: "user", content: "Seconda?" },
      ],
      pinned_ids: ["doc-2"],
      excluded_ids: ["doc-1"],
    });
  });

  it("skips assistant messages that errored or have no text", () => {
    const body = buildChatRequest(
      {
        messages: [
          msg("u1", "user", "prima"),
          msg("a1", "assistant", "", { error: "Chat request failed" }),
          msg("u2", "user", "seconda"),
          msg("a2", "assistant", ""),
          msg("u3", "user", "terza"),
          msg("a3", "assistant", "mezza risposta", { error: "Connection lost mid-answer" }),
          msg("u4", "user", "quarta"),
          msg("a4", "assistant", "risposta"),
        ],
        pinned: [],
        auto: [],
        excluded: [],
      },
      "nuova",
    );
    expect(body.messages).toEqual([
      { role: "user", content: "prima" },
      { role: "user", content: "seconda" },
      { role: "user", content: "terza" },
      { role: "user", content: "quarta" },
      { role: "assistant", content: "risposta" },
      { role: "user", content: "nuova" },
    ]);
  });

  it("sends at most the last 40 messages", () => {
    const messages = Array.from({ length: 50 }, (_, i) => msg(`m${i}`, i % 2 === 0 ? "user" : "assistant", `m${i}`));
    const body = buildChatRequest({ messages, pinned: [], auto: [], excluded: [] }, "ultima");
    expect(body.messages).toHaveLength(40);
    expect(body.messages[0].content).toBe("m11");
    expect(body.messages[39]).toEqual({ role: "user", content: "ultima" });
  });

  it("sends auto ids after pinned ids, de-duplicated", () => {
    const doc = (id: string) => ({ id, title: id, document_date: "2026-01-10" });
    const body = buildChatRequest(
      { messages: [], pinned: [doc("doc-2")], auto: [doc("doc-1"), doc("doc-2")], excluded: [] },
      "q",
    );
    expect(body.pinned_ids).toEqual(["doc-2", "doc-1"]);
  });
});
