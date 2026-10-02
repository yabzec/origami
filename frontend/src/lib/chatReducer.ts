import type { ChatSource, DocRef } from "./types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  grounded?: boolean;
  error?: string;
}

export interface ChatState {
  messages: ChatMessage[];
  streaming: boolean;
  pinned: DocRef[];
  auto: DocRef[];
  excluded: string[];
}

export type ChatAction =
  | { type: "SEND"; text: string; userId: string; assistantId: string }
  | { type: "META"; messageId: string; sources: ChatSource[]; grounded: boolean; autoDocuments: DocRef[] }
  | { type: "DELTA"; messageId: string; text: string }
  | { type: "DONE"; messageId: string }
  | { type: "ERROR"; messageId: string; message: string }
  | { type: "ABORT" }
  | { type: "PIN"; doc: DocRef }
  | { type: "REMOVE"; id: string }
  | { type: "NEW_CHAT" };

export const initialChatState: ChatState = { messages: [], streaming: false, pinned: [], auto: [], excluded: [] };

let messageSeq = 0;

/** Unique per page load; crypto.randomUUID() is unavailable on plain-HTTP LAN origins. */
export function nextMessageId(): string {
  messageSeq += 1;
  return `msg-${Date.now().toString(36)}-${messageSeq}`;
}

/** Apply `update` to the streaming assistant reply `messageId`; stale or late events are ignored. */
function updateReply(
  state: ChatState,
  messageId: string,
  update: (message: ChatMessage) => Partial<ChatMessage>,
  streaming = true,
): ChatState {
  const last = state.messages[state.messages.length - 1];
  if (!state.streaming || !last || last.role !== "assistant" || last.id !== messageId) return state;
  return { ...state, streaming, messages: [...state.messages.slice(0, -1), { ...last, ...update(last) }] };
}

function mergeAuto(state: ChatState, docs: DocRef[]): DocRef[] {
  const taken = new Set([...state.pinned.map((d) => d.id), ...state.auto.map((d) => d.id), ...state.excluded]);
  const added: DocRef[] = [];
  for (const doc of docs) {
    if (taken.has(doc.id)) continue;
    taken.add(doc.id);
    added.push(doc);
  }
  return added.length > 0 ? [...state.auto, ...added] : state.auto;
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "SEND":
      if (state.streaming) return state;
      return {
        ...state,
        streaming: true,
        messages: [
          ...state.messages,
          { id: action.userId, role: "user", content: action.text },
          { id: action.assistantId, role: "assistant", content: "" },
        ],
      };
    case "META": {
      const next = updateReply(state, action.messageId, () => ({ sources: action.sources, grounded: action.grounded }));
      return next === state ? state : { ...next, auto: mergeAuto(next, action.autoDocuments) };
    }
    case "DELTA":
      return updateReply(state, action.messageId, (m) => ({ content: m.content + action.text }));
    case "DONE":
      return updateReply(state, action.messageId, () => ({}), false);
    case "ERROR":
      return updateReply(state, action.messageId, () => ({ error: action.message }), false);
    case "ABORT":
      return state.streaming ? { ...state, streaming: false } : state;
    case "PIN":
      if (state.pinned.some((d) => d.id === action.doc.id)) return state;
      return {
        ...state,
        pinned: [...state.pinned, action.doc],
        auto: state.auto.filter((d) => d.id !== action.doc.id),
        excluded: state.excluded.filter((id) => id !== action.doc.id),
      };
    case "REMOVE":
      if (state.pinned.some((d) => d.id === action.id)) {
        return { ...state, pinned: state.pinned.filter((d) => d.id !== action.id) };
      }
      if (state.auto.some((d) => d.id === action.id)) {
        return {
          ...state,
          auto: state.auto.filter((d) => d.id !== action.id),
          excluded: state.excluded.includes(action.id) ? state.excluded : [...state.excluded, action.id],
        };
      }
      return state;
    case "NEW_CHAT":
      return initialChatState;
  }
}
