import { Suspense, lazy, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { PulseProvider, usePulse } from "./api/store";
import { Shell, Wordmark } from "./components/Shell";
import { applyTheme, usePrefs } from "./lib/prefs";
import { isProblem } from "./lib/status";
import Catalog from "./pages/Catalog";
import Dashboard from "./pages/Dashboard";
import Incidents from "./pages/Incidents";
import ServiceDetail from "./pages/ServiceDetail";
import Settings from "./pages/Settings";

const MapPage = lazy(() => import("./pages/MapPage"));

function LazyMap({ kiosk = false }: { kiosk?: boolean }) {
  return (
    <Suspense fallback={<p style={{ color: "var(--muted)" }}>Loading map…</p>}>
      <MapPage kiosk={kiosk} />
    </Suspense>
  );
}

function ThemeSync() {
  const { theme } = usePrefs();
  useEffect(() => {
    applyTheme(theme);
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const on = () => applyTheme("system");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [theme]);
  return null;
}

/** Wall-display mode: no chrome, large type, rotates views. */
function Kiosk() {
  const { summary } = usePulse();
  const views = ["status", "map"] as const;
  const [i, setI] = useState(0);
  useEffect(() => {
    // Scale every rem-based size up so the wall display reads from across a room.
    const root = document.documentElement;
    root.style.fontSize = "clamp(16px, 1.25vw, 30px)";
    return () => {
      root.style.fontSize = "";
    };
  }, []);
  useEffect(() => {
    const t = window.setInterval(() => setI((v) => (v + 1) % views.length), 30_000);
    return () => window.clearInterval(t);
  }, [views.length]);
  const problems = summary?.services.filter((s) => isProblem(s.state)).length ?? 0;
  return (
    <div className="min-h-screen p-6 text-[1.15rem] sm:p-10">
      <div className="mb-6 flex items-center gap-4">
        <Wordmark />
        <span className="narrow ml-auto text-sm" style={{ color: "var(--muted)" }}>
          {new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
          {problems ? `, ${problems} with problems` : ""}
        </span>
      </div>
      {views[i] === "map" ? <LazyMap kiosk /> : <Dashboard kiosk />}
    </div>
  );
}

function Routed() {
  const [params] = useSearchParams();
  if (params.get("kiosk") === "1") return <Kiosk />;
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/service/:id" element={<ServiceDetail />} />
        <Route path="/map" element={<LazyMap />} />
        <Route path="/incidents" element={<Incidents />} />
        <Route path="/catalog" element={<Catalog />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<p>This page doesn't exist.</p>} />
      </Routes>
    </Shell>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <PulseProvider>
        <ThemeSync />
        <Routed />
      </PulseProvider>
    </BrowserRouter>
  );
}
