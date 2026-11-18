import type { Scope, State } from "../api/types";
import { SCOPE_HINT, SCOPE_LABEL, STATE_LABEL, monogramHue } from "../lib/status";

/** Each state has its own shape so status never depends on color alone. */
export function Glyph({ state, size = 12, title }: { state: State; size?: number; title?: string }) {
  const s = size;
  const c = s / 2;
  const fill = "var(--st)";
  let shape;
  switch (state) {
    case "operational":
      shape = <circle cx={c} cy={c} r={c * 0.8} fill={fill} />;
      break;
    case "degraded":
      shape = <polygon points={`${c},${s * 0.08} ${s * 0.95},${s * 0.9} ${s * 0.05},${s * 0.9}`} fill={fill} />;
      break;
    case "partial_outage":
      shape = <polygon points={`${c},0 ${s},${c} ${c},${s} 0,${c}`} fill={fill} />;
      break;
    case "major_outage":
      shape = <rect x={s * 0.1} y={s * 0.1} width={s * 0.8} height={s * 0.8} rx={s * 0.08} fill={fill} />;
      break;
    case "maintenance":
      shape = (
        <>
          <circle cx={c} cy={c} r={c * 0.75} fill="none" stroke={fill} strokeWidth={s * 0.14} />
          <path d={`M${c},${c * 0.25} A${c * 0.75},${c * 0.75} 0 0 1 ${c},${s - c * 0.25} Z`} fill={fill} />
        </>
      );
      break;
    default:
      shape = <circle cx={c} cy={c} r={c * 0.7} fill="none" stroke={fill} strokeWidth={s * 0.16} strokeDasharray={`${s * 0.3} ${s * 0.18}`} />;
  }
  return (
    <svg className={`st-${state} shrink-0`} width={s} height={s} viewBox={`0 0 ${s} ${s}`} role="img" aria-label={title ?? STATE_LABEL[state]}>
      {shape}
    </svg>
  );
}

export function StateLabel({ state, className = "" }: { state: State; className?: string }) {
  return (
    <span className={`st-${state} inline-flex items-center gap-1.5 ${className}`}>
      <Glyph state={state} />
      <span style={{ color: "var(--st)" }} className="font-semibold">
        {STATE_LABEL[state]}
      </span>
    </span>
  );
}

export function ScopeBadge({ scope, state }: { scope: Scope; state: State }) {
  if (scope === "none") return null;
  const muted = scope === "local_only" || scope === "local_network";
  return (
    <span
      title={SCOPE_HINT[scope]}
      className={`st-${state} narrow inline-flex items-center whitespace-nowrap rounded px-1.5 py-px text-[0.75rem] font-semibold`}
      style={{
        color: muted ? "var(--muted)" : "var(--st)",
        border: `1px ${muted ? "dashed" : "solid"} ${muted ? "var(--line)" : "var(--st)"}`,
      }}
    >
      {SCOPE_LABEL[scope]}
    </span>
  );
}

export function Monogram({ id, name, size = 32 }: { id: string; name: string; size?: number }) {
  const hue = monogramHue(id);
  const letters = name.replace(/[^A-Za-z0-9 ]/g, "").split(" ").filter(Boolean);
  const text = (letters.length > 1 ? letters[0][0] + letters[1][0] : name.slice(0, 2)).toUpperCase();
  return (
    <span
      aria-hidden
      className="monogram wide grid shrink-0 place-items-center rounded-md font-bold"
      style={{ width: size, height: size, fontSize: size * 0.38, ["--mh" as string]: hue }}
    >
      {text}
    </span>
  );
}
