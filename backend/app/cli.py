"""Служебные команды: python -m app.cli create-admin <email> <пароль> [ФИО]."""

import sys

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.models import Organization, User, UserRole
from app.services.auth import get_user_by_email, normalize_email

PLATFORM_BIN = "000000000000"


def create_admin(email: str, password: str, full_name: str = "Администратор платформы") -> None:
    with get_sessionmaker()() as db:
        org = db.scalar(select(Organization).where(Organization.bin == PLATFORM_BIN))
        if org is None:
            org = Organization(name="Платформа ТехОценка", bin=PLATFORM_BIN)
            db.add(org)
            db.flush()
        user = get_user_by_email(db, email)
        if user is None:
            user = User(org_id=org.id, email=normalize_email(email), full_name=full_name, role=UserRole.admin)
            db.add(user)
        user.role = UserRole.admin
        user.password_hash = hash_password(password)
        db.commit()
        print(f"Администратор {user.email} готов")


def main(argv: list[str]) -> None:
    if len(argv) >= 3 and argv[0] == "create-admin":
        create_admin(*argv[1:4])
        return
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
