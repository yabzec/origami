import { describe, expect, it } from "vitest";
import { parseSSEStream } from "./sse";
import type { ChatEvent } from "./types";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((c) => controller.enqueue(encoder.encode(c)));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<ChatEvent[]> {
  const events: ChatEvent[] = [];
  for await (const event of parseSSEStream(stream)) events.push(event);
  return events;
}

describe("parseSSEStream", () => {
  it("parses a full event sequence", async () => {
    const events = await collect(
      streamOf(
        'data: {"type":"meta","grounded":true,"sources":[]}\n\n',
        'data: {"type":"delta","text":"Ciao"}\n\n',
        'data: {"type":"done"}\n\n',
      ),
    );
    expect(events.map((e) => e.type)).toEqual(["meta", "delta", "done"]);
  });

  it("handles frames split across chunks", async () => {
    const events = await collect(
      streamOf('data: {"type":"delta","te', 'xt":"spez', 'zato"}\n\ndata: {"type":"done"}\n\n'),
    );
    expect(events).toEqual([{ type: "delta", text: "spezzato" }, { type: "done" }]);
  });

  it("handles multiple frames in one chunk", async () => {
    const events = await collect(streamOf('data: {"type":"delta","text":"a"}\n\ndata: {"type":"delta","text":"b"}\n\n'));
    expect(events).toHaveLength(2);
  });
});
