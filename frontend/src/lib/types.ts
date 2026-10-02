export interface Tag {
  id: number;
  name: string;
  color: string;
}

export interface Folder {
  id: number;
  name: string;
  parent_id: number | null;
  created_at: string;
}

export type DocType = "scan" | "pdf" | "text" | "image" | "video";
export const DOC_TYPES: DocType[] = ["scan", "pdf", "text", "image", "video"];
export type DocStatus = "pending" | "processing" | "ready" | "failed";

export type TranslationStatus = "pending" | "done" | "failed";

export interface ActiveJob {
  type: string;
  attempts: number;
  max_attempts: number;
  run_at: string;
  last_error: string | null;
}

export interface Document {
  id: string;
  title: string;
  description: string;
  summary: string | null;
  folder_id: number | null;
  doc_type: DocType;
  ocr_languages: string;
  ocr_enabled: boolean;
  document_date: string;
  detected_language: string | null;
  translation_status: TranslationStatus | null;
  ocr_applied: boolean | null;
  status: DocStatus;
  error_message: string | null;
  original_filename: string | null;
  file_path: string | null;
  preview_path: string | null;
  page_count: number | null;
  file_size: number | null;
  created_at: string;
  updated_at: string;
  tags: Tag[];
  active_job: ActiveJob | null;
}

export interface DocumentText {
  summary: string | null;
  variant: "content" | "translation";
  detected_language: string | null;
  translation_status: TranslationStatus | null;
  translation_language: string;
  chunks: { chunk_index: number; page_number: number | null; content: string }[];
}

export interface ScanStatus {
  available: boolean;
  busy: boolean;
}

export interface ScanDevice {
  id: string;
  name: string;
}

export interface ScanPageInfo {
  id: number;
  page_number: number;
}

export interface SearchSnippet {
  chunk_id: number;
  page_number: number | null;
  source: string;
  text: string;
  similarity: number | null;
}

export interface SearchResult {
  document: Document;
  score: number;
  snippets: SearchSnippet[];
}

export interface SearchResponse {
  mode: string;
  results: SearchResult[];
}

export interface ChatSource {
  n: number;
  chunk_id: number;
  document_id: string;
  title: string;
  page_number: number | null;
}

export type ChatEvent =
  | { type: "meta"; grounded: boolean; sources: ChatSource[] }
  | { type: "delta"; text: string }
  | { type: "done" }
  | { type: "error"; code: string; message: string };
