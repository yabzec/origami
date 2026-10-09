import { useState, type KeyboardEvent } from "react";
import { useCreateTag, useTags } from "@/hooks/useTags";
import { ApiError } from "@/lib/api";
import { exactTag, matchingTags, nextTagColor } from "@/lib/tagInput";
import { cn } from "@/lib/utils";

export function TagInput({
  id,
  value,
  onChange,
}: {
  id?: string;
  value: number[];
  onChange: (ids: number[]) => void;
}) {
  const { data, refetch } = useTags();
  const tags = data ?? [];
  const create = useCreateTag();
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const [active, setActive] = useState(0);
  const [navigated, setNavigated] = useState(false); // user moved the highlight since the last edit
  const [error, setError] = useState<string | null>(null);

  const selected = value.map((tid) => tags.find((t) => t.id === tid)).filter((t) => t !== undefined);
  const suggestions = matchingTags(tags, value, query);
  const exact = exactTag(tags, query);
  const canCreate = query.trim() !== "" && exact === undefined;

  const add = (tagId: number) => {
    if (!value.includes(tagId)) onChange([...value, tagId]);
    setQuery("");
    setActive(0);
    setNavigated(false);
    setError(null);
  };

  const createTag = () => {
    const name = query.trim();
    create.mutate(
      { name, color: nextTagColor(tags) },
      {
        onSuccess: (tag) => add(tag.id),
        onError: async (err) => {
          if (err instanceof ApiError && err.status === 409) {
            const existing = exactTag((await refetch()).data ?? [], name); // created elsewhere meanwhile
            if (existing) return add(existing.id);
          }
          setError(err instanceof ApiError ? err.message : "Could not create tag");
        },
      },
    );
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? Math.min(active + 1, suggestions.length - 1) : active - 1;
      setActive(Math.max(next, 0));
      setNavigated(true);
    } else if (e.key === "Enter") {
      e.preventDefault();
      // arrowed highlight → select it; exact name → select it; otherwise create the typed name
      if (navigated && suggestions[active]) add(suggestions[active].id);
      else if (exact) add(exact.id);
      else if (canCreate) createTag();
      else if (suggestions[active]) add(suggestions[active].id);
    } else if (e.key === "Backspace" && query === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };

  return (
    <div className="relative">
      <div className="flex min-h-9 flex-wrap items-center gap-1 rounded-md border border-zinc-300 bg-white px-1 py-1">
        {selected.map((tag) => (
          <span
            key={tag.id}
            className="flex items-center gap-1 rounded-full px-2 py-0.5 text-xs"
            style={{ backgroundColor: `${tag.color}22`, color: tag.color }}
          >
            {tag.name}
            <button
              type="button"
              aria-label={`Remove ${tag.name}`}
              onClick={() => onChange(value.filter((x) => x !== tag.id))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          id={id}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
            setNavigated(false);
          }}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 150)} // let option clicks land first
          onKeyDown={onKeyDown}
          placeholder={selected.length ? "" : "Add tags…"}
          className="min-w-24 flex-1 px-1 text-sm outline-none"
        />
      </div>
      {focused && (suggestions.length > 0 || canCreate) && (
        <ul role="listbox" className="absolute z-20 mt-1 max-h-48 w-full overflow-y-auto rounded-md border border-zinc-200 bg-white p-1 shadow-lg">
          {suggestions.map((tag, index) => (
            <li
              key={tag.id}
              role="option"
              aria-selected={index === active}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => add(tag.id)}
              className={cn("cursor-pointer rounded px-2 py-1 text-sm", index === active && "bg-zinc-100")}
            >
              {tag.name}
            </li>
          ))}
          {canCreate && (
            <li
              role="option"
              aria-selected={false}
              onMouseDown={(e) => e.preventDefault()}
              onClick={createTag}
              className="cursor-pointer rounded px-2 py-1 text-sm text-zinc-600 hover:bg-zinc-100"
            >
              Create “{query.trim()}”
            </li>
          )}
        </ul>
      )}
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}
