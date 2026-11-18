import { useEffect, useState } from "react";

export type Theme = "system" | "dark" | "light" | "contrast";
export type Density = "comfortable" | "compact";
export type MobileCols = 1 | 2;

export interface Prefs {
  theme: Theme;
  density: Density;
  mobileCols: MobileCols;
  sort: "status" | "name" | "category";
}

const DEFAULTS: Prefs = { theme: "system", density: "comfortable", mobileCols: 1, sort: "status" };
const KEY = "pulse.prefs";

function read(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

const listeners = new Set<(p: Prefs) => void>();
let current: Prefs = typeof window === "undefined" ? DEFAULTS : read();

export function setPrefs(patch: Partial<Prefs>) {
  current = { ...current, ...patch };
  try {
    localStorage.setItem(KEY, JSON.stringify(current));
  } catch {
    /* storage unavailable: keep in memory */
  }
  listeners.forEach((l) => l(current));
}

export function usePrefs(): Prefs {
  const [p, setP] = useState(current);
  useEffect(() => {
    listeners.add(setP);
    return () => {
      listeners.delete(setP);
    };
  }, []);
  return p;
}

export function applyTheme(theme: Theme) {
  const resolved =
    theme === "system" ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : theme;
  document.documentElement.dataset.theme = resolved;
}
