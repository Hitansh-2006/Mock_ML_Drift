"""
Manage dashboard accounts from the command line.

    python -m serving.manage_users create alice            # asks for a password (hidden)
    python -m serving.manage_users create vic --role viewer  # a read-only account
    python -m serving.manage_users set-password alice
    python -m serving.manage_users list
    python -m serving.manage_users delete vic

Uses the same database as the API (DRIFT_WATCHDOG_DB_URL, default ./drift_watchdog.db).
"""
import argparse
import getpass
import sys

from serving import auth
from serving.db import SessionLocal, User, init_db


def _ask_password() -> str:
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    return first


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create")
    create.add_argument("username")
    create.add_argument("--role", choices=auth.ROLES, default="admin")
    sub.add_parser("set-password").add_argument("username")
    sub.add_parser("list")
    sub.add_parser("delete").add_argument("username")
    args = parser.parse_args(argv)

    init_db()
    db = SessionLocal()
    try:
        if args.cmd == "list":
            for u in db.query(User).order_by(User.username):
                print(f"{u.username}\t{u.role}")
        elif args.cmd == "create":
            auth.create_user(db, args.username, _ask_password(), args.role)
            print(f"Created {args.role} '{args.username.strip().lower()}'.")
        elif args.cmd == "set-password":
            auth.set_password(db, args.username, _ask_password())
            print("Password changed; that user was signed out everywhere.")
        elif args.cmd == "delete":
            print("Deleted." if auth.delete_user(db, args.username) else "No such user.")
    except ValueError as e:
        sys.exit(f"Error: {e}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
