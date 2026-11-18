import { describe, expect, it } from "vitest";
// Vitest runs in Node; the frontend deliberately has no @types/node, and `?raw` CSS imports are
// emptied by Vitest's CSS handling, so read the file directly.
// @ts-expect-error -- node builtin, untyped in this project
import { readFileSync } from "node:fs";

const css: string = readFileSync("src/index.css", "utf8") // Vitest runs from frontend/;

// Unlayered CSS beats every @layer, so a bare `a { color }` rule silently overrides
// `.btn-primary` (in @layer components) and makes link-styled buttons unreadable.
describe("index.css cascade layers", () => {
  it("keeps the global link rules inside @layer base", () => {
    const base = css.indexOf("@layer base {");
    expect(base).toBeGreaterThan(-1);
    const linkRule = css.search(/^\s*a\s*\{/m);
    expect(linkRule).toBeGreaterThan(base);
  });
});
