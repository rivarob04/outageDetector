import { describe, expect, it } from "vitest";
import type { ServiceSummary, Summary } from "../api/types";
import { verdictSentence } from "./Dashboard";

function svc(id: string, state: ServiceSummary["state"], scope: ServiceSummary["scope"] = "none"): ServiceSummary {
  return { id, name: id.toUpperCase(), state, scope } as ServiceSummary;
}

function summary(services: ServiceSummary[], offline = false): Summary {
  return {
    connectivity: { offline, offline_since: null, checked_at: null, canaries: [] },
    services,
  } as unknown as Summary;
}

describe("verdictSentence", () => {
  it("offline wins", () => {
    expect(verdictSentence(summary([svc("a", "major_outage")], true)).headline).toBe("You're offline.");
  });
  it("all good", () => {
    expect(verdictSentence(summary([svc("a", "operational")])).headline).toBe("Everything's up.");
  });
  it("names a single local-only problem", () => {
    expect(verdictSentence(summary([svc("steam", "major_outage", "local_only")])).headline).toBe("STEAM is down, but only from here.");
  });
  it("widespread wording", () => {
    expect(verdictSentence(summary([svc("x", "partial_outage", "widespread")])).headline).toBe("X looks partly down for everyone.");
  });
  it("lists a few and counts many", () => {
    const three = [svc("a", "degraded"), svc("b", "major_outage"), svc("c", "partial_outage")];
    expect(verdictSentence(summary(three)).headline).toBe("B, C and A are having problems.");
    const many = ["a", "b", "c", "d"].map((i) => svc(i, "degraded", "provider_confirmed"));
    const v = verdictSentence(summary(many));
    expect(v.headline).toBe("4 services are having problems.");
    expect(v.detail).toBe("4 confirmed by the provider.");
  });
});
