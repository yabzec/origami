import ReactMarkdown, { type Components } from "react-markdown";
import { Link } from "react-router";
import remarkGfm from "remark-gfm";
import { citationsToMarkdown, isCitationLabel, isInternalHref } from "@/lib/citations";
import type { ChatSource } from "@/lib/types";

// Raw HTML stays disabled (react-markdown default, no rehype-raw). The default urlTransform
// empties javascript:/data: URLs, which the link renderer below turns into plain text.
const components: Components = {
  a: ({ href, children }) => {
    if (!href) return <span>{children}</span>;
    if (isInternalHref(href)) {
      return (
        <Link
          to={href}
          className={
            isCitationLabel(children) ? "align-super text-xs font-semibold text-blue-700" : "text-blue-700 underline"
          }
        >
          {children}
        </Link>
      );
    }
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-700 underline">
        {children}
      </a>
    );
  },
  // Images would fetch external URLs on render (data leak/tracking): show the alt text only.
  img: ({ alt }) => <span>{alt}</span>,
  p: ({ children }) => <p className="my-2 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  h1: ({ children }) => <h3 className="mt-3 mb-1 text-base font-semibold">{children}</h3>,
  h2: ({ children }) => <h3 className="mt-3 mb-1 text-base font-semibold">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-3 mb-1 font-semibold">{children}</h4>,
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-zinc-300 pl-3 text-zinc-600">{children}</blockquote>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border border-zinc-200 bg-zinc-50 px-2 py-1 font-semibold">{children}</th>,
  td: ({ children }) => <td className="border border-zinc-200 px-2 py-1 align-top">{children}</td>,
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded bg-zinc-100 p-3 text-xs [&>code]:bg-transparent [&>code]:p-0">
      {children}
    </pre>
  ),
  code: ({ children }) => <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[0.85em]">{children}</code>,
};

export function Markdown({ text, sources = [] }: { text: string; sources?: ChatSource[] }) {
  return (
    <div className="text-sm break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {citationsToMarkdown(text, sources)}
      </ReactMarkdown>
    </div>
  );
}
