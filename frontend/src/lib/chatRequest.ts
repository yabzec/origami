import type { ChatState } from "./chatReducer";

export const MAX_REQUEST_MESSAGES = 40;

export interface ChatRequestBody {
  messages: { role: "user" | "assistant"; content: string }[];
  pinned_ids: string[];
  excluded_ids: string[];
}

/** Body for POST /api/chat: history plus the new question, without failed or empty replies. */
export function buildChatRequest(
  state: Pick<ChatState, "messages" | "pinned" | "excluded">,
  text: string,
): ChatRequestBody {
  const history = state.messages
    .filter((m) => m.role === "user" || (!m.error && m.content.trim() !== ""))
    .map(({ role, content }) => ({ role, content }));
  return {
    messages: [...history, { role: "user" as const, content: text }].slice(-MAX_REQUEST_MESSAGES),
    pinned_ids: state.pinned.map((d) => d.id),
    excluded_ids: [...state.excluded],
  };
}
