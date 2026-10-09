import { useEffect } from "react";
import { OcrLanguageSelect } from "@/components/OcrLanguageSelect";
import { useOcrLanguages } from "@/hooks/useOcrLanguages";
import type { ProcessingValues } from "@/lib/processing";

export function ProcessingOptions({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string;
  value: ProcessingValues;
  onChange: (value: ProcessingValues) => void;
}) {
  const { data } = useOcrLanguages();
  const noLanguages = data !== undefined && data.languages.length === 0;

  useEffect(() => {
    // fill the server default once it arrives, unless the user (or the document) already chose
    if (data && value.ocrLanguages === "" && data.default) onChange({ ...value, ocrLanguages: data.default });
  }, [data, value, onChange]);

  const set = (patch: Partial<ProcessingValues>) => onChange({ ...value, ...patch });

  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Processing</legend>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value.ocrEnabled && !noLanguages}
          disabled={noLanguages}
          onChange={(e) => set({ ocrEnabled: e.target.checked })}
        />
        OCR (extract text)
      </label>
      {value.ocrEnabled && (
        <OcrLanguageSelect
          id={`${idPrefix}-ocr-lang`}
          value={value.ocrLanguages}
          onChange={(ocrLanguages) => set({ ocrLanguages })}
        />
      )}
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={value.summaryEnabled} onChange={(e) => set({ summaryEnabled: e.target.checked })} />
        AI summary
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value.translationEnabled}
          onChange={(e) => set({ translationEnabled: e.target.checked })}
        />
        Translation
      </label>
    </fieldset>
  );
}
