"""The Email delivery screen's three endpoints answer the Main Admin, and only them.

Regression: `routers/admin_mail.py` handed the plain function
`require_admin(session: dict)` to `Depends`, so FastAPI read `session` as a
required JSON body and every request - the office's included, anonymous
included - was a 422 before authentication ran. `/admin/mail` could never
load. No test called the endpoints; the Swagger contract suite in
`testing/api` found it by calling every documented GET.
"""
from __future__ import annotations

from conftest import requires_db

from app.models.user import Role


@requires_db
def test_the_mail_log_answers_the_office_and_refuses_everybody_else(client, make_user):
    admin = make_user("mail-adm", Role.ADMIN)
    mentor = make_user("mail-men", Role.MENTOR)
    student = make_user("mail-stu")

    r = client.get("/api/admin/mail-log", headers=admin.headers)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
    assert client.get("/api/admin/mail-log", params={"failed_only": True, "limit": 5},
                      headers=admin.headers).status_code == 200

    for who in (mentor, student):
        assert client.get("/api/admin/mail-log", headers=who.headers).status_code == 403
    client.cookies.clear()
    assert client.get("/api/admin/mail-log").status_code == 401


@requires_db
def test_the_suppression_question_reaches_its_handler(client, make_user):
    admin = make_user("mail-adm2", Role.ADMIN)
    r = client.get("/api/admin/mail-log/suppression", params={"email": "someone@bgscet.ac.in"},
                   headers=admin.headers)
    # No transport on a test machine: the handler answers "could not ask",
    # never the dependency's 422 about a body.
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "someone@bgscet.ac.in"
    student = make_user("mail-stu2")
    assert client.get("/api/admin/mail-log/suppression", params={"email": "x@bgscet.ac.in"},
                      headers=student.headers).status_code == 403
    lift = client.delete("/api/admin/mail-log/suppression", params={"email": "someone@bgscet.ac.in"},
                         headers=admin.headers)
    assert lift.status_code != 422, lift.text
