import { describe, expect, it } from "vitest";
import { duration, timeAgo } from "./format";

describe("format", () => {
  it("timeAgo", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(timeAgo("2026-01-01T11:57:00Z", now)).toBe("3m ago");
    expect(timeAgo(null, now)).toBe("never");
  });
  it("duration", () => {
    expect(duration(72 * 60000)).toBe("1h 12m");
  });
});
