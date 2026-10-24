"""Create (or refresh) N STUDENT accounts for performance testing.

    cd apps/api-py
    .venv/bin/python ../../testing/tools/create_load_users.py --count 100

Writes ``loadtest001@bgscet.ac.in`` ... with the password ``LoadTest#2026``
and a CSV (``email,password``) that JMeter and the Selenium runner read.

Why distinct accounts: REEP keeps ONE live session per account (AGENTS.md,
"ONE DEVICE AT A TIME"). A load test that signs 100 virtual users in as the
same seeded student measures 99 users being signed out, not the system.

Refuses to run when ENV=prod, for app.seed's reason: these are accounts with a
password published in this repository.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2] / "apps" / "api-py"
sys.path.insert(0, str(API_ROOT))
os.chdir(API_ROOT)  # so pydantic-settings finds apps/api-py/.env

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models.student_profile import StudentProfile  # noqa: E402
from app.models.user import Role, Student, User  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "LoadTest#2026"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument(
        "--reserve", type=int, default=10,
        help="extra accounts NOT written to the CSV (loadtest101...), for the API and "
             "Selenium suites, so a functional run can never retire a JMeter virtual user's session",
    )
    ap.add_argument(
        "--csv",
        default=str(Path(__file__).resolve().parents[1] / "jmeter" / "data" / "users.csv"),
    )
    args = ap.parse_args()
    if settings.is_prod:
        sys.exit("REFUSED: ENV=prod. Load-test accounts carry a published password.")

    digest = hash_password(PASSWORD)  # one scrypt, reused: the salt is per-hash, not per-user
    rows: list[tuple[str, str]] = []
    db = SessionLocal()
    try:
        for i in range(1, args.count + args.reserve + 1):
            email = f"loadtest{i:03d}@bgscet.ac.in"
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, name=f"Load Test {i:03d}", role=Role.STUDENT, password_hash=digest)
                db.add(user)
                db.flush()
                stu = Student(user_id=user.id)
                db.add(stu)
                db.flush()
                db.add(StudentProfile(student_id=stu.id, city="Bengaluru"))
            else:
                user.password_hash = digest
            if i <= args.count:
                rows.append((email, PASSWORD))
        db.commit()
    finally:
        db.close()

    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
    with open(args.csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["email", "password"])
        w.writerows(rows)
    print(f"{len(rows)} load-test students in {args.csv}; {args.reserve} reserved (not in the CSV)")


if __name__ == "__main__":
    main()
