export const DEFAULT_OCR_LANGUAGES = "ita+eng";

export const OCR_LANGUAGES = [
  { value: "ita+eng", label: "Italian + English" },
  { value: "ita", label: "Italian" },
  { value: "eng", label: "English" },
  { value: "deu", label: "German" },
  { value: "ita+deu", label: "Italian + German" },
] as const;
