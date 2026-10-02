import { NavLink, Outlet, useNavigate, useSearchParams } from "react-router";
import { useAuth } from "@/auth";
import { FolderTree } from "@/components/FolderTree";
import { TagManager } from "@/components/TagManager";
import { browseSearch, parseSort } from "@/lib/sorting";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "Browse" },
  { to: "/scan", label: "Scan" },
  { to: "/search", label: "Search" },
  { to: "/chat", label: "Chat" },
];

export function Layout() {
  const { logout, user } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const selectedFolder = searchParams.get("folder") ? Number(searchParams.get("folder")) : null;

  const selectFolder = (id: number | null) => {
    navigate(`/${browseSearch(id, parseSort(searchParams.get("sort")))}`);
  };

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-64 flex-col border-r border-zinc-200 bg-zinc-50 p-3">
        <h1 className="mb-4 px-2 text-lg font-bold">Origami</h1>
        <nav className="mb-4 space-y-0.5">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                cn("block rounded px-2 py-1 text-sm hover:bg-zinc-100", isActive && "bg-zinc-200 font-medium")
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="flex-1 overflow-y-auto">
          <FolderTree selectedId={selectedFolder} onSelect={selectFolder} />
          <TagManager />
        </div>
        <button onClick={logout} className="mt-4 px-2 text-left text-sm text-zinc-500 hover:text-zinc-800">
          Log out {user ? `(${user.username})` : ""}
        </button>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
