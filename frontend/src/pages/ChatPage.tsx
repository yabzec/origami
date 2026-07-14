import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { getToken } from "@/lib/api";
import { splitCitations } from "@/lib/citations";
import { parseSSEStream } from "@/lib/sse";
import type { ChatSource } from "@/lib/types";

interface ChatResult {
  grounded: boolean | null;
  sources: ChatSource[];
  answer: string;
  error: string | null;
  streaming: boolean;
}

const emptyResult: ChatResult = { grounded: null, sources: [], answer: "", error: null, streaming: false };

function Answer({ text }: { text: string }) {
  return (
    <p className="whitespace-pre-wrap text-sm leading-relaxed">
      {splitCitations(text).map((part, index) =>
        part.kind === "text" ? (
          <span key={index}>{part.text}</span>
        ) : (
          <a key={index} href={`#source-${part.n}`} className="align-super text-xs font-semibold text-blue-700">
            [{part.n}]
          </a>
        ),
      )}
    </p>
  );
}

export function ChatPage() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<ChatResult>(emptyResult);

  const ask = async (e: FormEvent) => {
    e.preventDefault();
    if (!question.trim() || result.streaming) return;
    setResult({ ...emptyResult, streaming: true });
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${getToken() ?? ""}`,
        },
        body: JSON.stringify({ question }),
      });
      if (!resp.ok || !resp.body) {
        setResult((r) => ({ ...r, streaming: false, error: "Chat request failed" }));
        return;
      }
      for await (const event of parseSSEStream(resp.body)) {
        if (event.type === "meta") {
          setResult((r) => ({ ...r, grounded: event.grounded, sources: event.sources }));
        } else if (event.type === "delta") {
          setResult((r) => ({ ...r, answer: r.answer + event.text }));
        } else if (event.type === "error") {
          setResult((r) => ({ ...r, error: event.message, streaming: false }));
        } else if (event.type === "done") {
          setResult((r) => ({ ...r, streaming: false }));
        }
      }
      setResult((r) => ({ ...r, streaming: false }));
    } catch {
      setResult((r) => ({ ...r, streaming: false, error: "Connection lost mid-answer" }));
    }
  };

  return (
    <div className="mx-auto max-w-3xl p-6">
      <h2 className="mb-4 text-lg font-semibold">Ask your documents</h2>
      <form onSubmit={ask} className="mb-6 space-y-2">
        <Textarea
          rows={2}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Quanto ho pagato la bolletta di marzo?"
        />
        <Button type="submit" disabled={result.streaming || !question.trim()}>
          {result.streaming ? "Answering…" : "Ask"}
        </Button>
      </form>

      {result.grounded === false && (
        <div className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          Answer not based on your documents.
        </div>
      )}
      {result.error && (
        <div className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{result.error}</div>
      )}
      {result.answer && (
        <div className="rounded-lg border border-zinc-200 bg-white p-4">
          <Answer text={result.answer} />
          {result.streaming && <span className="animate-pulse text-zinc-400">▍</span>}
        </div>
      )}
      {result.sources.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-1 text-sm font-semibold text-zinc-500">Sources</h3>
          <ol className="space-y-1 text-sm">
            {result.sources.map((source) => (
              <li key={source.n} id={`source-${source.n}`}>
                [{source.n}]{" "}
                <Link to={`/documents/${source.document_id}`} className="underline">
                  {source.title}
                </Link>
                {source.page_number != null && <span className="text-zinc-400"> (p. {source.page_number})</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
