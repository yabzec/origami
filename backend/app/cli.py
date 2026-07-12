import argparse
import getpass

from sqlmodel import Session, select

from app.db import engine
from app.models import User
from app.services.auth import hash_password


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="origami")
    sub = parser.add_subparsers(dest="command", required=True)
    p_create = sub.add_parser("create-user")
    p_create.add_argument("username")
    args = parser.parse_args()
    if args.command == "create-user":
        create_user(args.username)


if __name__ == "__main__":
    main()
