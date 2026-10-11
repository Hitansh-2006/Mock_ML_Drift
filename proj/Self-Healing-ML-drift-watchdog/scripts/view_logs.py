"""
Quick human-readable viewer for the drift-watchdog database. This is a
stand-in for the real dashboard (step 5) — use it to sanity-check what's
actually in the DB without hand-writing SQL.

Usage:
    python scripts/view_logs.py summary          # counts + latest drift status
    python scripts/view_logs.py logs [--limit 20]
    python scripts/view_logs.py drift [--limit 10]
    python scripts/view_logs.py drift --id 3      # full per-feature detail for one event
"""
import argparse
import json

from serving.db import SessionLocal, PredictionLog, DriftEvent


def cmd_summary():
    db = SessionLocal()
    try:
        total = db.query(PredictionLog).filter(PredictionLog.is_shadow == False).count()  # noqa: E712
        labeled = db.query(PredictionLog).filter(
            PredictionLog.is_shadow == False, PredictionLog.true_label.isnot(None)  # noqa: E712
        ).count()
        shadow_total = db.query(PredictionLog).filter(PredictionLog.is_shadow == True).count()  # noqa: E712
        latest_drift = db.query(DriftEvent).order_by(DriftEvent.id.desc()).first()

        print(f"Production predictions logged : {total}")
        print(f"  of which labeled            : {labeled}")
        print(f"Shadow predictions logged      : {shadow_total}")
        if latest_drift:
            status = "DRIFT DETECTED" if latest_drift.triggered else "no drift"
            print(f"Latest drift check (event #{latest_drift.id}): {status}, "
                  f"score={latest_drift.drift_score}, "
                  f"window=timesteps {latest_drift.window_start_timestep}-{latest_drift.window_end_timestep}")
        else:
            print("No drift checks have been run yet.")
    finally:
        db.close()


def cmd_logs(limit):
    db = SessionLocal()
    try:
        rows = (
            db.query(PredictionLog)
            .order_by(PredictionLog.id.desc())
            .limit(limit)
            .all()
        )
        print(f"{'id':>5} {'ts':>5} {'model':>7} {'shadow':>7} {'prediction':>12} {'true_label':>12}")
        for r in reversed(rows):
            label = f"{r.true_label:.2f}" if r.true_label is not None else "-"
            print(f"{r.id:>5} {r.timestep:>5} {r.model_version:>7} {str(r.is_shadow):>7} "
                  f"{r.prediction:>12.2f} {label:>12}")
    finally:
        db.close()


def cmd_drift(limit, event_id):
    db = SessionLocal()
    try:
        if event_id is not None:
            event = db.query(DriftEvent).filter(DriftEvent.id == event_id).first()
            if not event:
                print(f"No drift event with id {event_id}")
                return
            status = "DRIFT DETECTED" if event.triggered else "no drift"
            print(f"Event #{event.id} | {status} | score={event.drift_score} | "
                  f"window=timesteps {event.window_start_timestep}-{event.window_end_timestep}")
            for feat, v in (event.details or {}).items():
                flag = "  <-- DRIFTED" if v["drifted"] else ""
                print(f"    {feat}: ks={v['ks_statistic']} p={v['p_value']}{flag}")
        else:
            rows = db.query(DriftEvent).order_by(DriftEvent.id.desc()).limit(limit).all()
            print(f"{'id':>4} {'window':>15} {'score':>7} {'triggered':>10}")
            for r in reversed(rows):
                window = f"{r.window_start_timestep}-{r.window_end_timestep}"
                print(f"{r.id:>4} {window:>15} {r.drift_score:>7} {str(r.triggered):>10}")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary")

    p_logs = sub.add_parser("logs")
    p_logs.add_argument("--limit", type=int, default=20)

    p_drift = sub.add_parser("drift")
    p_drift.add_argument("--limit", type=int, default=10)
    p_drift.add_argument("--id", type=int, default=None)

    args = parser.parse_args()

    if args.command == "summary":
        cmd_summary()
    elif args.command == "logs":
        cmd_logs(args.limit)
    elif args.command == "drift":
        cmd_drift(args.limit, args.id)


if __name__ == "__main__":
    main()
