import { Button } from "@/components/ui/button";
import { getToken } from "@/lib/api";
import { AGENT_PLATFORMS, detectPlatform, downloadUrl, type AgentPlatform } from "@/lib/localScan";

const FIRST_RUN: Record<string, string> = {
  windows: "Run the file once. If Windows shows “Windows protected your PC”, choose More info → Run anyway. Allow it on private networks if the firewall asks.",
  darwin: "Unzip, move Origami Agent to Applications, then right-click it → Open once.",
  linux: "Make it executable (chmod +x origami-agent-linux-*) and run it once.",
};

export function AgentInstallPanel({ onInstalled }: { onInstalled: () => void }) {
  const detected = detectPlatform(navigator.userAgent);
  const token = getToken();
  const others = AGENT_PLATFORMS.filter((p) => p.id !== detected);
  const label = (id: AgentPlatform) => AGENT_PLATFORMS.find((p) => p.id === id)?.label ?? id;

  return (
    <div className="mt-1 space-y-2 border-t border-zinc-100 p-2 text-xs text-zinc-600">
      <p>To use a scanner on this computer's network, install the Origami Agent once.</p>
      {detected && (
        <>
          <a
            href={downloadUrl(detected, token)}
            className="inline-block rounded-md bg-brand-700 px-3 py-1.5 font-medium text-white hover:bg-brand-800"
          >
            Download for {label(detected)}
          </a>
          <p>{FIRST_RUN[detected.split("-")[0]]}</p>
        </>
      )}
      <p>
        Other systems:{" "}
        {others.map((p, i) => (
          <span key={p.id}>
            {i > 0 && " · "}
            <a className="underline" href={downloadUrl(p.id, token)}>
              {p.label}
            </a>
          </span>
        ))}
      </p>
      <Button type="button" size="sm" onClick={onInstalled}>
        Installed, search now
      </Button>
    </div>
  );
}
