/** Transient messages. The container is an aria-live region so screen readers hear them. */
export function Toasts({ toasts, onDismiss }) {
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className="toast" data-tone={t.type}>
          <span className="toast-msg">{t.message}</span>
          <button type="button" aria-label="Dismiss message" onClick={() => onDismiss(t.id)}>×</button>
        </div>
      ))}
    </div>
  );
}
