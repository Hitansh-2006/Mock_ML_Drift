import { useEffect, useRef } from "react";
import { Button } from "./ui";

/**
 * Modal confirmation on a native <dialog>: focus is trapped, Esc cancels and
 * the page behind is inert, all provided by the browser. Focus starts on
 * Cancel so a stray Enter never confirms a risky action.
 */
export function ConfirmDialog({ open, title, children, confirmLabel, confirmVariant = "primary", busy, onConfirm, onCancel }) {
  const ref = useRef(null);
  const cancelRef = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      el.showModal();
      cancelRef.current?.focus();
    } else if (!open && el.open) {
      el.close();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby="dialog-title"
      onCancel={(e) => {
        e.preventDefault(); // we close it ourselves via `open`
        if (!busy) onCancel();
      }}
      onClick={(e) => {
        if (e.target === ref.current && !busy) onCancel(); // backdrop click
      }}
    >
      {open && (
        <>
          <div className="dialog-body">
            <h2 id="dialog-title">{title}</h2>
            {children}
          </div>
          <div className="dialog-actions">
            <button ref={cancelRef} type="button" className="btn btn-secondary" onClick={onCancel} disabled={busy}>
              Cancel
            </button>
            <Button
              variant={confirmVariant === "danger" ? "danger" : "primary"}
              solid={confirmVariant === "danger"}
              loading={busy}
              loadingLabel="Working…"
              onClick={onConfirm}
            >
              {confirmLabel}
            </Button>
          </div>
        </>
      )}
    </dialog>
  );
}
