import type { ReactNode } from "react";
import { iconKind, type IconKind } from "@/lib/docIcons";
import { cn } from "@/lib/utils";
import type { Document } from "@/lib/types";

const PAGE = (
  <>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5" />
  </>
);

const ICONS: Record<IconKind, { color: string; title: string; body: ReactNode }> = {
  pdf: {
    color: "text-red-600",
    title: "PDF",
    body: (
      <>
        {PAGE}
        <text x="12" y="17.5" textAnchor="middle" fontSize="5.5" fontWeight="700" fill="currentColor" stroke="none">
          PDF
        </text>
      </>
    ),
  },
  word: {
    color: "text-blue-600",
    title: "Word document",
    body: (
      <>
        {PAGE}
        <path d="m8 12 1.5 6 2.5-4.5 2.5 4.5 1.5-6" />
      </>
    ),
  },
  text: {
    color: "text-zinc-500",
    title: "Text",
    body: (
      <>
        {PAGE}
        <path d="M9 13h6M9 17h6M9 9h2" />
      </>
    ),
  },
  image: {
    color: "text-green-600",
    title: "Image",
    body: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <circle cx="8.5" cy="9.5" r="1.5" />
        <path d="m21 16-5-5-9 9" />
      </>
    ),
  },
  video: {
    color: "text-purple-600",
    title: "Video",
    body: (
      <>
        <rect x="2" y="6" width="14" height="12" rx="2" />
        <path d="m16 10 6-3v10l-6-3z" />
      </>
    ),
  },
};

export function DocTypeIcon({
  doc,
  className,
}: {
  doc: Pick<Document, "doc_type" | "original_filename">;
  className?: string;
}) {
  const { color, title, body } = ICONS[iconKind(doc)];
  return (
    <svg
      viewBox="0 0 24 24"
      width={24}
      height={24}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      role="img"
      aria-label={title}
      className={cn("shrink-0", color, className)}
    >
      <title>{title}</title>
      {body}
    </svg>
  );
}
