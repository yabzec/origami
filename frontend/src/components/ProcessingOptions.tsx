import { useEffect } from "react";
import { OcrLanguageSelect } from "@/components/OcrLanguageSelect";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useOcrLanguages } from "@/hooks/useOcrLanguages";
import { normalizeProcessing, type ProcessingValues } from "@/lib/processing";

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
    // fill the server default once it arrives and drop languages that are no longer installed
    if (!data) return;
    const next = normalizeProcessing(value, data);
    if (next !== value) onChange(next);
  }, [data, value, onChange]);

  const targets = data?.translation_languages ?? []; // absent from older servers
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
      {value.translationEnabled && targets.length > 0 && (
        <div className="pl-6">
          <Label htmlFor={`${idPrefix}-tr-lang`}>Translate to</Label>
          <Select
            id={`${idPrefix}-tr-lang`}
            value={value.translationLanguage}
            onChange={(e) => set({ translationLanguage: e.target.value })}
          >
            {targets.map((l) => (
              <option key={l.code} value={l.code}>
                {l.name}
              </option>
            ))}
          </Select>
        </div>
      )}
    </fieldset>
  );
}
