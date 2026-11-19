import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { usePulse } from "../api/store";

const NAV = [
  { to: "/", label: "Status", key: "d" },
  { to: "/map", label: "Map", key: "m" },
  { to: "/incidents", label: "Incidents", key: "i" },
  { to: "/isp", label: "Local ISPs", key: "l" },
  { to: "/catalog", label: "Add services", key: "c" },
  { to: "/settings", label: "Settings", key: "s" },
];

export function Wordmark() {
  return (
    <span className="wide inline-flex items-center gap-2 text-lg font-extrabold tracking-tight">
      <svg width="22" height="22" viewBox="0 0 64 64" aria-hidden>
        <path d="M6 34h12l6-14 10 26 6-12h18" fill="none" stroke="var(--ok)" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      Pulse
    </span>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { me, live, summary } = usePulse();
  const [login, setLogin] = useState(false);
  const [help, setHelp] = useState(false);
  useShortcuts(setHelp);

  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only">
        Skip to content
      </a>
      <header className="border-b" style={{ borderColor: "var(--line)" }}>
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
          <NavLink to="/" aria-label="Pulse home">
            <Wordmark />
          </NavLink>
          <nav aria-label="Main" className="order-3 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) =>
                  `whitespace-nowrap rounded-md px-2.5 py-1 text-sm font-medium no-underline ${isActive ? "" : "opacity-70 hover:opacity-100"}`
                }
                style={({ isActive }) => (isActive ? { background: "var(--panel-2)" } : undefined)}
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            {summary?.demo && (
              <span className="narrow rounded px-1.5 py-px text-xs font-semibold" style={{ border: "1px solid var(--maint)", color: "var(--maint)" }}>
                Demo data
              </span>
            )}
            <span className="narrow inline-flex items-center gap-1.5 text-xs" style={{ color: "var(--muted)" }} title={live ? "Receiving live updates" : "Live updates disconnected; refreshing every minute"}>
              <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: live ? "var(--ok)" : "var(--unknown)" }} />
              {live ? "Live" : "Polling"}
            </span>
            {me?.user ? (
              <UserMenu name={me.user.username} canLogout={me.user.via === "session"} />
            ) : (
              <button className="btn" onClick={() => setLogin(true)}>
                {me?.setup_required ? "Set up admin" : "Sign in"}
              </button>
            )}
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6">
        {children}
      </main>
      <footer className="narrow mx-auto max-w-[1400px] px-6 pb-8 text-xs" style={{ color: "var(--faint)" }}>
        Pulse {summary?.version}. Press <kbd>?</kbd> for keyboard shortcuts.
      </footer>
      {login && <LoginDialog onClose={() => setLogin(false)} />}
      {help && <HelpDialog onClose={() => setHelp(false)} />}
    </div>
  );
}

function UserMenu({ name, canLogout }: { name: string; canLogout: boolean }) {
  const { refreshMe } = usePulse();
  return (
    <span className="inline-flex items-center gap-2">
      <span style={{ color: "var(--muted)" }}>{name}</span>
      {canLogout && (
        <button
          className="btn"
          onClick={async () => {
            await api("/api/v1/auth/logout", { method: "POST" });
            await refreshMe();
          }}
        >
          Sign out
        </button>
      )}
    </span>
  );
}

export function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className="panel m-auto w-[min(440px,92vw)] rounded-lg p-0 text-[var(--text)] backdrop:bg-black/50"
    >
      <div className="p-5">
        <h2 className="wide mb-4 mt-0 text-lg font-bold">{title}</h2>
        {children}
      </div>
    </dialog>
  );
}

function LoginDialog({ onClose }: { onClose: () => void }) {
  const { me, refreshMe, refresh } = usePulse();
  const setup = me?.setup_required;
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setErr(null);
    try {
      await api(setup ? "/api/v1/auth/setup" : "/api/v1/auth/login", {
        method: "POST",
        json: { username: f.get("username"), password: f.get("password"), setup_code: f.get("setup_code") ?? "" },
      });
      await refreshMe();
      await refresh();
      onClose();
    } catch (ex) {
      setErr((ex as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title={setup ? "Create the admin account" : "Sign in"} onClose={onClose}>
      <form onSubmit={submit} className="grid gap-3">
        {setup && (
          <p className="m-0 text-sm" style={{ color: "var(--muted)" }}>
            Find the setup code in the container logs: <code>docker compose logs pulse</code>.
          </p>
        )}
        {setup && (
          <label>
            <span className="label">Setup code</span>
            <input name="setup_code" className="field" required autoComplete="one-time-code" />
          </label>
        )}
        <label>
          <span className="label">Username</span>
          <input name="username" className="field" required autoComplete="username" defaultValue={setup ? "admin" : ""} autoFocus={!setup} />
        </label>
        <label>
          <span className="label">Password{setup ? " (at least 10 characters)" : ""}</span>
          <input name="password" type="password" className="field" required minLength={setup ? 10 : 1} autoComplete={setup ? "new-password" : "current-password"} />
        </label>
        {err && (
          <p role="alert" className="m-0 text-sm" style={{ color: "var(--major)" }}>
            {err}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy}>
            {setup ? "Create account" : "Sign in"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function HelpDialog({ onClose }: { onClose: () => void }) {
  const rows = [
    ["/", "Search services"],
    ["g d", "Status"],
    ["g m", "Map"],
    ["g i", "Incidents"],
    ["g l", "Local ISPs"],
    ["g c", "Add services"],
    ["g s", "Settings"],
    ["?", "Show this help"],
  ];
  return (
    <Dialog title="Keyboard shortcuts" onClose={onClose}>
      <table className="w-full text-sm">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <td className="py-1 pr-4">
                <kbd className="rounded border px-1.5" style={{ borderColor: "var(--line)" }}>
                  {k}
                </kbd>
              </td>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Dialog>
  );
}

function useShortcuts(setHelp: (v: (h: boolean) => boolean) => void) {
  const navigate = useNavigate();
  useEffect(() => {
    let gAt = 0;
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey || t.closest("input, textarea, select, [contenteditable]")) return;
      if (e.key === "/") {
        const s = document.getElementById("search");
        if (s) {
          e.preventDefault();
          s.focus();
        }
      } else if (e.key === "?") {
        setHelp((h) => !h);
      } else if (e.key === "g") {
        gAt = Date.now();
      } else if (Date.now() - gAt < 1200) {
        const n = NAV.find((x) => x.key === e.key);
        if (n) navigate(n.to);
        gAt = 0;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, setHelp]);
}
