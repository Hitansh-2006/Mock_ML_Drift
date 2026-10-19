import { useRef } from "react";
import { Button } from "./ui";

/**
 * Quick actions. Every button states WHY it is disabled (tooltip on hover and
 * keyboard focus) and shows a spinner while its work is in flight.
 */
export function ActionsCard({ status, connState, readOnlyReason, busy, trainingBaseline, onUpload, onTrainBaseline, onDriftCheck, onRetrain }) {
  const fileRef = useRef(null);

  const blocked =
    readOnlyReason ||
    (connState === "offline" ? "The serving API is not reachable."
      : connState === "locked" ? "Your session has ended. Sign in again."
        : connState === "loading" ? "Connecting…"
          : null);
  const baselineReady = !!status?.baseline_ready;
  const shadow = status?.shadow_version;
  const anyBusy = !!busy || trainingBaseline;

  const reason = (specific) => blocked || (anyBusy && !specific ? "Another action is in progress." : specific) || undefined;

  return (
    <div className="card" aria-labelledby="actions-title">
      <div className="card-title" id="actions-title">Actions</div>
      <div className="actions-grid">
        <input
          ref={fileRef}
          type="file"
          accept=".csv"
          hidden
          aria-label="Choose a CSV file to upload as the stream"
          onChange={(e) => {
            const file = e.target.files[0];
            e.target.value = "";
            if (file) onUpload(file);
          }}
        />
        <Button
          loading={busy === "upload"} loadingLabel="Uploading…"
          disabledReason={reason(null)}
          onClick={() => fileRef.current?.click()}
          title="Replace the simulated stream file"
        >
          Upload data
        </Button>
        <Button
          loading={busy === "drift"} loadingLabel="Checking…"
          disabledReason={reason(null)}
          onClick={onDriftCheck}
        >
          Run drift check
        </Button>
        <Button
          loading={trainingBaseline} loadingLabel="Training baseline…"
          disabledReason={reason(baselineReady ? "A baseline already exists and is never overwritten. Use Retrain challenger." : null)}
          onClick={onTrainBaseline}
        >
          Train baseline
        </Button>
        <Button
          loading={busy === "retrain"} loadingLabel="Starting…"
          disabledReason={reason(
            !baselineReady && status ? "Train a baseline first."
              : shadow ? `Challenger ${shadow} is already in shadow evaluation. Decide on it first.` : null
          )}
          onClick={onRetrain}
        >
          Retrain challenger
        </Button>
      </div>
      <p className="actions-hint">Retraining only starts a shadow test. Production changes only when you approve.</p>
    </div>
  );
}
