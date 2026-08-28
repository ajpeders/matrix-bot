import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useStatus } from "../state/status";
import { logout } from "../api/auth";

const navItems = [
  { to: "/", label: "Home", icon: "⌂", end: true },
  { to: "/playlists", label: "Library", icon: "♫" },
];

export default function Layout() {
  const { botName, status, error } = useStatus();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <div className="flex h-full w-full bg-bg text-text">
      <aside className="flex w-60 shrink-0 flex-col gap-6 border-r border-border bg-bg-elev p-5">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-dim">
            Matrix Music Bot
          </div>
          <div className="mt-1 truncate text-sm text-muted">{botName ?? "—"}</div>
        </div>

        <nav className="flex flex-col gap-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                  isActive
                    ? "bg-surface text-text"
                    : "text-muted hover:bg-surface hover:text-text"
                }`
              }
            >
              <span className="text-base leading-none">{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="mt-auto space-y-3">
          <div className="rounded-md border border-border bg-surface px-3 py-2 text-xs">
            <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-dim">
              Call
            </div>
            <div className="mt-1 flex items-center gap-2">
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  status?.connected ? "bg-accent" : "bg-dim"
                }`}
              />
              <span className="text-muted">
                {status?.connected ? "Streaming" : "Not in a call"}
              </span>
            </div>
          </div>

          <button
            type="button"
            onClick={handleLogout}
            className="w-full rounded-md border border-border px-3 py-2 text-sm text-muted transition-colors hover:border-accent hover:text-text"
          >
            Log out
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        {error && (
          <div className="border-b border-danger/40 bg-danger/10 px-8 py-3 text-sm text-danger">
            Can't reach the bot: {error}
          </div>
        )}
        <div className="mx-auto max-w-5xl px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
