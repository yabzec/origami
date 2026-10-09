import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { api, getToken } from "@/lib/api";
import { AGENT_PLATFORMS, detectPlatform, downloadUrl, installSteps, type AgentPlatform } from "@/lib/localScan";

function InstallSteps({ platform, label }: { platform: AgentPlatform; label: string }) {
  return (
    <div role="status" className="space-y-1 rounded-md border border-brand-300 bg-brand-200/40 p-2 text-zinc-700">
      <p className="font-medium">Finish installing on {label}</p>
      <ol className="list-decimal space-y-1 pl-4">
        {installSteps(platform).map((step) => (
          <li key={step.text}>
            {step.text}
            {step.command && (
              <code className="mt-0.5 block rounded bg-zinc-100 px-1.5 py-1 font-mono break-all select-all">
                {step.command}
              </code>
            )}
          </li>
        ))}
      </ol>
      <p>Keep the file in that folder: the browser starts it from there.</p>
    </div>
  );
}

export function AgentInstallPanel({ onInstalled }: { onInstalled: () => void }) {
  const detected = detectPlatform(navigator.userAgent);
  const token = getToken();
  const others = AGENT_PLATFORMS.filter((p) => p.id !== detected);
  const label = (id: AgentPlatform) => AGENT_PLATFORMS.find((p) => p.id === id)?.label ?? id;
  const [downloaded, setDownloaded] = useState<AgentPlatform | null>(null);
  const { data } = useQuery({
    queryKey: ["agent-downloads"],
    queryFn: () => api.get<{ platforms: AgentPlatform[] }>("/api/agent/downloads"),
  });
  // Until the list arrives every link is offered; `download` keeps the scan page either way.
  const built = (id: AgentPlatform) => !data || data.platforms.includes(id);
  const notBuilt = (id: AgentPlatform) => `${label(id)}: not built on this server`;

  return (
    <div className="mt-1 space-y-2 border-t border-zinc-100 p-2 text-xs text-zinc-600">
      <p>To use a scanner on this computer's network, install the Origami Agent once.</p>
      {detected &&
        (built(detected) ? (
          <>
            <a
              href={downloadUrl(detected, token)}
              download
              onClick={() => setDownloaded(detected)}
              className="inline-block rounded-md bg-brand-700 px-3 py-1.5 font-medium text-white hover:bg-brand-800"
            >
              Download for {label(detected)}
            </a>
          </>
        ) : (
          <p>{notBuilt(detected)}</p>
        ))}
      <p>
        Other systems:{" "}
        {others.map((p, i) => (
          <span key={p.id}>
            {i > 0 && " · "}
            {built(p.id) ? (
              <a className="underline" href={downloadUrl(p.id, token)} download onClick={() => setDownloaded(p.id)}>
                {p.label}
              </a>
            ) : (
              <span>{notBuilt(p.id)}</span>
            )}
          </span>
        ))}
      </p>
      {downloaded && <InstallSteps platform={downloaded} label={label(downloaded)} />}
      <Button type="button" size="sm" onClick={onInstalled}>
        Installed, search now
      </Button>
    </div>
  );
}
