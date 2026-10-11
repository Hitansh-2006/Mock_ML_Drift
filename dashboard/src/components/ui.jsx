import { formatAbsolute, formatRelative } from "../lib/format";
import { useNow } from "../hooks/useNow";

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

/**
 * variant: primary | secondary | danger.   loading: spinner + not clickable.
 * `disabledReason` disables the button but keeps it focusable (aria-disabled)
 * and shows the reason as a tooltip on hover AND keyboard focus.
 */
export function Button({
  variant = "secondary", size, loading, loadingLabel, disabledReason, solid, children, onClick, className = "", ...rest
}) {
  const inert = !!loading || !!disabledReason || rest.disabled;
  const classes = ["btn", `btn-${variant}`, size === "sm" ? "btn-sm" : "", solid ? "solid" : "", className]
    .filter(Boolean)
    .join(" ");
  return (
    <button
      type="button"
      {...rest}
      className={classes}
      aria-disabled={inert || undefined}
      aria-busy={loading || undefined}
      data-tooltip={disabledReason || undefined}
      onClick={inert ? undefined : onClick}
    >
      {loading ? (
        <>
          <Spinner />
          {loadingLabel || children}
        </>
      ) : (
        children
      )}
    </button>
  );
}

/** tone: neutral | success | warning | danger | accent */
export function Pill({ tone = "neutral", children, ...rest }) {
  return (
    <span className="pill" data-tone={tone} {...rest}>
      <span className="dot" aria-hidden="true" />
      {children}
    </span>
  );
}

export function Badge({ tone = "neutral", children }) {
  return (
    <span className="badge" data-tone={tone}>
      {children}
    </span>
  );
}

export function Skeleton({ width = "100%", height = 16, style }) {
  return <span className="skeleton" style={{ width, height, ...style }} aria-hidden="true" />;
}

export function EmptyState({ title, children }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children}
    </div>
  );
}

export function Section({ id, title, description, children }) {
  return (
    <section className="section" id={id} aria-labelledby={`${id}-title`}>
      <div className="section-head">
        <h2 id={`${id}-title`}>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {children}
    </section>
  );
}

/** "2 min ago" with the exact time on hover; keeps itself current. */
export function RelativeTime({ ts }) {
  const now = useNow(15);
  if (!ts) return <span>—</span>;
  return (
    <time dateTime={new Date(ts * 1000).toISOString()} title={formatAbsolute(ts)}>
      {formatRelative(ts, now)}
    </time>
  );
}

/**
 * LLM-written text is labelled honestly: the same field holds a template
 * sentence when no GROQ_API_KEY is set (the API doesn't say which), so the tag
 * says "AI or template" instead of claiming AI.
 */
export function Summary({ children }) {
  return (
    <div className="summary">
      <div className="summary-tag" title="Written by an LLM when GROQ_API_KEY is configured, otherwise a fixed template. The API does not record which.">
        Summary · AI or template
      </div>
      <p>{children}</p>
    </div>
  );
}

export function TableCaption({ children }) {
  return <caption className="visually-hidden">{children}</caption>;
}
