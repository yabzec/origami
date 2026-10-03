import { describe, expect, it } from "vitest";
import { initialChatState, type ChatState } from "./chatReducer";
import { CHAT_STORAGE_KEY, loadChatState, saveChatState } from "./chatStorage";

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
    key: () => null,
    get length() {
      return m.size;
    },
  };
}

const doc = { id: "doc-1", title: "Bolletta", document_date: "2026-03-15" };
const state: ChatState = {
  messages: [
    { id: "u1", role: "user", content: "Ciao" },
    { id: "a1", role: "assistant", content: "mezza risp" },
  ],
  streaming: false,
  pinned: [doc],
  auto: [],
  excluded: ["doc-9"],
};

describe("chatStorage", () => {
  it("round-trips the state", () => {
    const s = memoryStorage();
    saveChatState(state, s);
    expect(loadChatState(s)).toEqual(state);
  });

  it("returns the initial state for missing or invalid data", () => {
    const s = memoryStorage();
    expect(loadChatState(s)).toEqual(initialChatState);
    s.setItem(CHAT_STORAGE_KEY, "{not json");
    expect(loadChatState(s)).toEqual(initialChatState);
    s.setItem(CHAT_STORAGE_KEY, '{"messages":3}');
    expect(loadChatState(s)).toEqual(initialChatState);
  });

  it("forces streaming to false and keeps partial text", () => {
    const s = memoryStorage();
    saveChatState({ ...state, streaming: true }, s);
    const loaded = loadChatState(s);
    expect(loaded.streaming).toBe(false);
    expect(loaded.messages[1].content).toBe("mezza risp");
  });

  it("never throws when storage does", () => {
    const bad = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
    } as unknown as Storage;
    expect(() => saveChatState(state, bad)).not.toThrow();
    expect(loadChatState(bad)).toEqual(initialChatState);
  });
});
