import argparse
import getpass

from sqlmodel import Session, select

from app.db import engine
from app.models import User
from app.services.auth import hash_password
from app.services.notify import recipients, send_email, smtp_configured


def create_user(username: str) -> None:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    with Session(engine) as session:
        if session.exec(select(User).where(User.username == username)).first():
            raise SystemExit(f"User {username!r} already exists")
        session.add(User(username=username, password_hash=hash_password(password)))
        session.commit()
    print(f"User {username!r} created")


def set_email(username: str, email: str) -> None:
    email = email.strip()
    if "@" not in email or any(ch.isspace() for ch in email):
        raise SystemExit(f"Not a valid email address: {email!r}")
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if user is None:
            raise SystemExit(f"User {username!r} not found")
        user.email = email
        session.commit()
    print(f"Email of {username!r} set to {email}")


def send_test_email() -> None:
    if not smtp_configured():
        raise SystemExit("SMTP is not configured: set SMTP_USER and SMTP_APP_PASSWORD in .env")
    with Session(engine) as session:
        to = recipients(session)
    if not to:
        raise SystemExit("No user has an email address: run `python -m app.cli set-email <username> <email>`")
    ok = send_email(to, "[Origami] Test email", "This is a test message from Origami.\nNotifications work.")
    if not ok:
        raise SystemExit(f"Sending to {', '.join(to)} failed (see the error above)")
    print(f"Test email sent to {', '.join(to)}")


def print_models() -> None:
    from app.services import llm  # imports litellm: only load it for this command

    provider = llm.model_provider()
    try:
        models = llm.list_models()
    except Exception as exc:
        print(f"Cannot list models: {exc}")
        raise SystemExit(1)
    print(f"Provider: {provider}")
    headers = ("ID", "OWNER", "CONTEXT", "ACTIVE")
    rows = [
        (
            m["id"] or "",
            m["owner"] or "",
            "" if m["context_window"] is None else str(m["context_window"]),
            "" if m["active"] is None else ("yes" if m["active"] else "no"),
        )
        for m in sorted(models, key=lambda m: m["id"] or "")
    ]
    widths = [max([len(h), *(len(r[i]) for r in rows)]) for i, h in enumerate(headers)]
    for row in [headers, *rows]:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())


def main() -> None:
    parser = argparse.ArgumentParser(prog="origami")
    sub = parser.add_subparsers(dest="command", required=True)
    p_create = sub.add_parser("create-user")
    p_create.add_argument("username")
    p_email = sub.add_parser("set-email", help="set the notification email of a user")
    p_email.add_argument("username")
    p_email.add_argument("email")
    sub.add_parser("test-email", help="send a test email to every user with an email")
    sub.add_parser("list-models", help="list the models offered by the LLM_MODEL provider")
    args = parser.parse_args()
    if args.command == "create-user":
        create_user(args.username)
    elif args.command == "set-email":
        set_email(args.username, args.email)
    elif args.command == "test-email":
        send_test_email()
    elif args.command == "list-models":
        print_models()


if __name__ == "__main__":
    main()
