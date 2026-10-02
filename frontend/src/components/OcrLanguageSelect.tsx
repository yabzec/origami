import type { SelectHTMLAttributes } from "react";
import { Select } from "@/components/ui/select";
import { OCR_LANGUAGES } from "@/lib/ocrLanguages";

export function OcrLanguageSelect(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <Select {...props}>
      {OCR_LANGUAGES.map((l) => (
        <option key={l.value} value={l.value}>
          {l.label}
        </option>
      ))}
    </Select>
  );
}
