import { usePreviewImage } from "@/hooks/usePreviewImage";

function PageImage({ pageId }: { pageId: number }) {
  const url = usePreviewImage(pageId);
  if (!url) return <span className="text-zinc-300">…</span>;
  return <img src={url} alt="Selected page" className="max-h-full max-w-full object-contain" />;
}

export function ScanPreview({
  pageId,
  previewUrl,
  scanning,
}: {
  pageId: number | null;
  previewUrl: string | null;
  scanning: boolean;
}) {
  return (
    <div className="relative flex h-[60vh] items-center justify-center overflow-hidden rounded-lg border border-zinc-200 bg-zinc-50 p-2">
      {previewUrl ? (
        <>
          <img src={previewUrl} alt="Scanner preview" className="max-h-full max-w-full object-contain" />
          <span className="absolute top-2 left-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
            Preview — not saved
          </span>
        </>
      ) : pageId !== null ? (
        <PageImage key={pageId} pageId={pageId} />
      ) : (
        <p className="text-zinc-400">No pages yet — place a page on the scanner and press “Scan first page”.</p>
      )}
      {scanning && (
        <div className="absolute inset-0 flex items-center justify-center bg-white/60 text-zinc-600">Scanning…</div>
      )}
    </div>
  );
}
