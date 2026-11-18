import type { Scope, State } from "../api/types";

export const STATE_LABEL: Record<State, string> = {
  operational: "Operational",
  degraded: "Degraded",
  partial_outage: "Partial outage",
  major_outage: "Major outage",
  maintenance: "Maintenance",
  unknown: "Unknown",
};

export const STATE_RANK: Record<State, number> = {
  major_outage: 5,
  partial_outage: 4,
  degraded: 3,
  maintenance: 2,
  unknown: 1,
  operational: 0,
};

export const SCOPE_LABEL: Record<Scope, string> = {
  provider_confirmed: "Provider confirmed",
  widespread: "Widespread",
  local_only: "Only from your network",
  local_network: "You're offline",
  none: "",
};

export const SCOPE_HINT: Record<Scope, string> = {
  provider_confirmed: "The provider's official status page reports this.",
  widespread: "Checks fail from several vantage points, but the provider hasn't confirmed yet.",
  local_only: "Checks fail from this network, while other signals look fine.",
  local_network: "This host appears to be offline, so the service can't be judged.",
  none: "",
};

export function isProblem(s: State): boolean {
  return s === "degraded" || s === "partial_outage" || s === "major_outage";
}

export function impactToState(impact: string): State {
  switch (impact) {
    case "critical":
      return "major_outage";
    case "major":
      return "partial_outage";
    case "minor":
      return "degraded";
    case "maintenance":
      return "maintenance";
    case "none":
      return "operational";
    default:
      return "unknown";
  }
}

export function componentState(status: string): State {
  return (
    { operational: "operational", degraded: "degraded", partial_outage: "partial_outage", major_outage: "major_outage", maintenance: "maintenance" } as Record<string, State>
  )[status] ?? "unknown";
}

/** Stable hue for monogram tiles. */
export function monogramHue(id: string): number {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}
