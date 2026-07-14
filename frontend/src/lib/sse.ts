import type { ChatEvent } from "./types";

export async function* parseSSEStream(stream: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 2);
        if (frame.startsWith("data: ")) yield JSON.parse(frame.slice(6)) as ChatEvent;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
