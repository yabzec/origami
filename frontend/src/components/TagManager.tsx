import { useState } from "react";
import { useCreateTag, useDeleteTag, useTags } from "@/hooks/useTags";

export function TagManager() {
  const { data: tags } = useTags();
  const create = useCreateTag();
  const remove = useDeleteTag();
  const [name, setName] = useState("");
  const [color, setColor] = useState("#888888");

  return (
    <div className="mt-6">
      <span className="px-2 text-xs font-semibold uppercase text-zinc-400">Tags</span>
      <ul className="mt-1">
        {(tags ?? []).map((tag) => (
          <li key={tag.id} className="group flex items-center gap-2 px-2 py-1 text-sm">
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: tag.color }} />
            <span className="flex-1 truncate">{tag.name}</span>
            <button
              className="hidden text-zinc-400 hover:text-red-600 group-hover:block"
              onClick={() => window.confirm(`Delete tag "${tag.name}"?`) && remove.mutate(tag.id)}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <form
        className="mt-1 flex items-center gap-1 px-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim()) return;
          create.mutate({ name: name.trim(), color }, { onSuccess: () => setName("") });
        }}
      >
        <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="h-6 w-6" />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New tag"
          className="w-full rounded border border-zinc-200 px-1 py-0.5 text-sm"
        />
      </form>
    </div>
  );
}
