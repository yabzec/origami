import { initialChatState, type ChatState } from "./chatReducer";

export const CHAT_STORAGE_KEY = "origami.chat";

function defaultStorage(): Storage | undefined {
  try {
    return window.sessionStorage;
  } catch {
    return undefined;
  }
}

/** Saved conversation, or an empty one when missing or unreadable. Never streaming. */
export function loadChatState(storage: Storage | undefined = defaultStorage()): ChatState {
  try {
    const raw = storage?.getItem(CHAT_STORAGE_KEY);
    if (!raw) return initialChatState;
    const data = JSON.parse(raw);
    if (!data || !Array.isArray(data.messages) || !Array.isArray(data.pinned) || !Array.isArray(data.auto) || !Array.isArray(data.excluded)) {
      return initialChatState;
    }
    return { messages: data.messages, pinned: data.pinned, auto: data.auto, excluded: data.excluded, streaming: false };
  } catch {
    return initialChatState;
  }
}

export function saveChatState(state: ChatState, storage: Storage | undefined = defaultStorage()): void {
  try {
    storage?.setItem(CHAT_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // storage unavailable or full: the conversation just won't survive navigation
  }
}
