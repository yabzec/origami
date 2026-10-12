import { FolderPicker } from "@/components/FolderPicker";
import { TagInput } from "@/components/TagInput";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { discReportUrl, discThumbUrl } from "@/lib/api";
import type { StudyDraft } from "@/lib/discImport";
import type { DiscReport, DiscStudy } from "@/lib/types";

function toggle(keys: string[], key: string, on: boolean): string[] {
  return on ? [...keys.filter((k) => k !== key), key] : keys.filter((k) => k !== key);
}

export function DiscStudyCard({
  importId,
  study,
  reports,
  draft,
  onChange,
}: {
  importId: string;
  study: DiscStudy;
  reports: DiscReport[];
  draft: StudyDraft;
  onChange: (draft: StudyDraft) => void;
}) {
  const set = (patch: Partial<StudyDraft>) => onChange({ ...draft, ...patch });
  const id = (name: string) => `disc-${study.key}-${name}`;
  return (
    <section className="space-y-3 rounded-md border border-zinc-200 p-3">
      <label className="flex items-center gap-2 text-sm font-medium">
        <input type="checkbox" checked={draft.include} onChange={(e) => set({ include: e.target.checked })} />
        Import this study ({study.images.length} images)
      </label>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Label htmlFor={id("title")}>Title</Label>
          <Input id={id("title")} value={draft.title} onChange={(e) => set({ title: e.target.value })} />
        </div>
        <div>
          <Label htmlFor={id("date")}>Document date</Label>
          <Input
            id={id("date")}
            type="date"
            value={draft.documentDate}
            onChange={(e) => set({ documentDate: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor={id("folder")}>Folder</Label>
          <FolderPicker id={id("folder")} value={draft.folderId} onChange={(folderId) => set({ folderId })} />
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor={id("tags")}>Tags</Label>
          <TagInput id={id("tags")} value={draft.tagIds} onChange={(tagIds) => set({ tagIds })} />
          {study.patient_tag && study.patient_tag_id === null && (
            <label className="mt-1 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={draft.addPatientTag}
                onChange={(e) => set({ addPatientTag: e.target.checked })}
              />
              Tag “{study.patient_tag}”
            </label>
          )}
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor={id("notes")}>Notes</Label>
          <Textarea id={id("notes")} rows={2} value={draft.notes} onChange={(e) => set({ notes: e.target.value })} />
        </div>
      </div>
      <fieldset>
        <legend className="mb-1 text-sm font-medium">Images</legend>
        {!study.images.some((i) => i.readable) && (
          <p className="mb-1 text-xs text-amber-700">No image can be decoded; add the images with Add files.</p>
        )}
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {study.images.map((image) => (
            <label key={image.key} className={image.readable ? "text-xs" : "text-xs opacity-50"}>
              {image.readable ? (
                <img
                  src={discThumbUrl(importId, image.key)}
                  alt={image.label}
                  className="mb-1 h-28 w-full rounded border bg-black object-contain"
                />
              ) : (
                <div className="mb-1 flex h-28 items-center justify-center rounded border text-center">
                  Format not supported
                </div>
              )}
              <span className="flex items-center gap-1">
                <input
                  type="checkbox"
                  disabled={!image.readable}
                  checked={draft.imageKeys.includes(image.key)}
                  onChange={(e) => set({ imageKeys: toggle(draft.imageKeys, image.key, e.target.checked) })}
                />
                {image.label}
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      {reports.length > 0 && (
        <fieldset className="space-y-1">
          <legend className="mb-1 text-sm font-medium">Reports</legend>
          {reports.map((report) => (
            <div key={report.key} className="text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={draft.reportKeys.includes(report.key)}
                  onChange={(e) => set({ reportKeys: toggle(draft.reportKeys, report.key, e.target.checked) })}
                />
                {report.filename} ({report.page_count} p.)
                <a href={discReportUrl(importId, report.key)} target="_blank" rel="noreferrer" className="underline">
                  View
                </a>
              </label>
              {!report.has_text && (
                <p className="pl-6 text-xs text-amber-700">
                  This report has no text. Enable OCR to make it searchable.
                </p>
              )}
            </div>
          ))}
        </fieldset>
      )}
    </section>
  );
}
