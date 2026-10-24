"""Data isolation between individuals and between colleges (IDOR sweep).

Run against a LIVE dev stack (ENV=dev, dev seed applied, API on :3300), on a
throwaway database, never beside pytest:

    cd apps/api-py && DATABASE_URL=... .venv/bin/python ../../testing/api/data_isolation_multi_college.py out.json

Cast: college X (students A and B, faculty FX who mentors A, faculty FZ who
mentors nobody), college Y (student C, faculty FY who mentors C), and the
seeded alumnus. Each owner first creates real records (an upload, a resume
version, a leave request, a mentor note), then every other account tries to
read, change or delete them by id. The owner and the assigned mentor are the
positive controls: if they cannot reach their own data, the probe is broken,
not the fence.

Passwords are set straight onto the rows with the app's own hash_password,
because accounts minted by approval hold the SSO sentinel and the dev login
door needs a real hash. That is a test-only shortcut on a dev database.
"""
import json, random, string, sys, uuid

import httpx
from sqlalchemy import create_engine, text

sys.path.insert(0, ".")
from app.config import settings  # noqa: E402
from app.security import hash_password  # noqa: E402

API = "http://localhost:3300/api"
RUN = uuid.uuid4().hex[:4].upper()
RESULTS = []
PW = "Isolation-test-pw-2026"
engine = create_engine(settings.database_url)


def rec(area, name, resp, expect, who=""):
    got = resp.status_code
    ok = (got in expect) if isinstance(expect, (set, tuple, list)) else got == expect
    if got >= 500:
        ok = False
    RESULTS.append(dict(area=area, name=name, who=who, got=got, expect=str(expect), ok=ok, body=resp.text[:200]))
    return resp


def login(email):
    c = httpx.Client(base_url=API, timeout=30)
    r = c.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, (email, r.text)
    return c


def set_pw(email):
    with engine.begin() as cx:
        cx.execute(text("update users set password_hash=:h where email=:e"), {"h": hash_password(PW), "e": email})


admin = httpx.Client(base_url=API, timeout=30)
assert admin.post("/auth/login", json={"email": "admin@bgscet.ac.in", "password": "admin123"}).status_code == 200
anon = httpx.Client(base_url=API, timeout=30)

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def college(tag):
    code = f"I{tag}{RUN}"
    dom = f"{code.lower()}.edu.in"
    c = admin.post("/admin/colleges", json={"code": code, "name": f"Isolation {tag}", "email_domains": [dom]}).json()
    d = admin.post(f"/admin/colleges/{c['id']}/departments", json={"code": "CSE", "name": "CSE"}).json()
    b = admin.post(f"/admin/departments/{d['id']}/cohorts", json={
        "code": f"{code}-CSE-2026-30", "name": "2026-30", "batch_label": "2026-30", "degree_level": "UG",
        "entry_date": "2026-07-01", "expected_completion": "2030-06-30"}).json()
    return dict(c=c, d=d, b=b, dom=dom)


def student(col):
    usn = "1" + "".join(random.choices(string.ascii_uppercase + string.digits, k=9))
    email = f"{usn.lower()}@{col['dom']}"
    ip = f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"
    r = anon.post("/register", headers={"X-Forwarded-For": ip}, data={
        "name": f"Student {usn}", "email": email, "usn": usn, "phone": "9876543210",
        "personal_email": f"{usn.lower()}@gmail.com", "linkedin_url": "linkedin.com/in/x", "degree_level": "UG",
        "college_id": col["c"]["id"], "department_id": col["d"]["id"], "requested_cohort_id": col["b"]["id"]},
        files={"cv": ("cv.pdf", PDF, "application/pdf"), "photo": ("p.png", PNG, "image/png")})
    assert r.status_code == 201, r.text
    reg_id = r.json()["id"]
    sid = admin.post(f"/register/{reg_id}/decision", json={"decision": "APPROVE"}).json()["approved_student_id"]
    set_pw(email)
    return dict(email=email, sid=sid, reg=reg_id, cl=login(email))


def faculty(col, name):
    email = f"{name.lower()}{RUN.lower()}@{col['dom']}"
    r = admin.post("/admin/faculty", json={"name": name, "email": email, "department_id": col["d"]["id"]})
    assert r.status_code == 201, r.text
    set_pw(email)
    return dict(email=email, uid=r.json()["user_id"])


X, Y = college("X"), college("Y")
A, B, C = student(X), student(X), student(Y)
FX, FZ, FY = faculty(X, "FX"), faculty(X, "FZ"), faculty(Y, "FY")
for s, f in ((A, FX), (C, FY)):
    r = admin.post(f"/admin/students/{s['sid']}/mentor", json={"mentor_user_id": f["uid"], "reason": "isolation test setup"})
    assert r.status_code == 204, r.text
for f in (FX, FZ, FY):
    f["cl"] = login(f["email"])
alum = httpx.Client(base_url=API, timeout=30)
assert alum.post("/auth/login", json={"email": "alumni@bgscet.ac.in", "password": "alumni123"}).status_code == 200

# ------------------------------------------------ records owned by A ----
up = A["cl"].post("/student/uploads", data={"kind": "DOCUMENT", "title": "A marksheet"}, files={"file": ("m.pdf", PDF, "application/pdf")})
assert up.status_code == 201, up.text
up_id = up.json()["id"]
rs = A["cl"].post("/student/resume/generate", json={"title": "A resume"})
assert rs.status_code in (200, 201), rs.text
rs_id = rs.json().get("id") or rs.json().get("resume", {}).get("id")
lv = A["cl"].post("/leaves", json={"from_date": "2026-12-01", "to_date": "2026-12-02", "reason": "A private medical reason", "leave_kind": "CASUAL"})
lv_id = lv.json().get("id") if lv.status_code == 201 else None
note = FX["cl"].post(f"/mentor/students/{A['sid']}/notes", json={"note_text": "Private note about A"})
note_id = note.json().get("id") if note.status_code in (200, 201) else None
RESULTS.append(dict(area="setup", name="records created", who="A/FX", got=0, expect="", ok=all([up_id, rs_id, lv_id, note_id]),
                    body=f"upload={up_id} resume={rs_id} leave={lv_id}({lv.status_code}) note={note_id}({note.status_code} {note.text[:80]})"))

# ------------------------------------------------ positive controls ----
rec("control", "A reads own upload", A["cl"].get(f"/student/uploads/{up_id}/file"), 200, "A")
rec("control", "A reads own resume", A["cl"].get(f"/student/resume/{rs_id}"), 200, "A")
if lv_id:
    rec("control", "A reads own leave paper", A["cl"].get(f"/leaves/{lv_id}/paper.pdf"), 200, "A")
rec("control", "FX reads mentee A notes", FX["cl"].get(f"/mentor/students/{A['sid']}/notes"), 200, "FX")
rec("control", "FX reads mentee A upload", FX["cl"].get(f"/mentor/uploads/{up_id}/file"), 200, "FX")

# ------------------------------------- student B (same college) vs A ----
for who, cl in (("B same college", B["cl"]), ("C other college", C["cl"]), ("alumnus", alum), ("anonymous", anon)):
    bad = {401, 403, 404}
    rec("student-data", "read A's upload file", cl.get(f"/student/uploads/{up_id}/file"), bad, who)
    rec("student-data", "delete A's upload", cl.delete(f"/student/uploads/{up_id}"), bad, who)
    rec("student-data", "read A's resume", cl.get(f"/student/resume/{rs_id}"), bad, who)
    rec("student-data", "A's resume pdf", cl.get(f"/student/resume/{rs_id}/pdf"), bad, who)
    rec("student-data", "rename A's resume", cl.patch(f"/student/resume/{rs_id}", json={"title": "hacked"}), bad, who)
    rec("student-data", "regenerate A's resume", cl.post(f"/student/resume/{rs_id}/regenerate"), bad, who)
    rec("student-data", "delete A's resume", cl.delete(f"/student/resume/{rs_id}"), bad, who)
    if lv_id:
        rec("leave", "A's leave paper", cl.get(f"/leaves/{lv_id}/paper.pdf"), bad, who)
        rec("leave", "A's leave attachments", cl.get(f"/leaves/{lv_id}/attachments"), bad, who)
        rec("leave", "cancel A's leave", cl.post(f"/leaves/{lv_id}/cancel"), bad, who)
        rec("leave", "decide A's leave", cl.post(f"/leaves/{lv_id}/decision", json={"decision": "APPROVE"}), bad | {422}, who)
        rec("leave", "A's leave balance", cl.get(f"/leaves/{lv_id}/balance"), bad, who)
    rec("registration", "A's application CV", cl.get(f"/register/{A['reg']}/documents/cv/file"), bad, who)
    rec("registration", "A's application photo", cl.get(f"/register/{A['reg']}/documents/photo/file"), bad, who)
    for path in ("notes", "ledger", "ledger/summary", "english-baseline", "badges", "growth", "skill-profile", "interviews", "focus"):
        rec("mentor-view", f"/mentor/students/A/{path}", cl.get(f"/mentor/students/{A['sid']}/{path}"), bad, who)
    rec("mentor-view", "/admin/students/A/360", cl.get(f"/admin/students/{A['sid']}/360"), bad, who)

# list endpoints must not leak A to B
lst = B["cl"].get("/student/uploads")
rec("student-data", "B's upload list excludes A's file", lst, 200, "B")
if lst.status_code == 200 and up_id in lst.text:
    RESULTS.append(dict(area="student-data", name="B's upload list CONTAINS A's file", who="B", got=200, expect="absent", ok=False, body=""))
lr = B["cl"].get("/student/resume")
if lr.status_code == 200 and rs_id in lr.text:
    RESULTS.append(dict(area="student-data", name="B's resume list CONTAINS A's resume", who="B", got=200, expect="absent", ok=False, body=""))
ll = B["cl"].get("/leaves")
if ll.status_code == 200 and lv_id and lv_id in ll.text:
    RESULTS.append(dict(area="leave", name="B's leave list CONTAINS A's leave", who="B", got=200, expect="absent", ok=False, body=""))

# leaderboard: C (college Y) must not appear on A's board, and vice versa
for who, cl, other in (("A", A["cl"], C["email"]), ("C", C["cl"], A["email"])):
    lb = cl.get("/student/leaderboards")
    other_name = "Student " + other.split("@")[0].upper()
    leaked = lb.status_code == 200 and other_name in lb.text
    RESULTS.append(dict(area="leaderboard", name=f"{who}'s board shows the other college's student", who=who, got=lb.status_code,
                        expect="absent", ok=not leaked and lb.status_code == 200, body=lb.text[:200] if leaked else ""))

# ------------------------------- faculty: other college / no mentees ----
for who, f in (("FY other college", FY), ("FZ same college, no mentees", FZ)):
    cl = f["cl"]
    bad = {403, 404}
    for path in ("notes", "ledger", "ledger/summary", "english-baseline", "badges", "growth", "skill-profile", "interviews", "interviews/progress", "focus"):
        rec("faculty", f"read A /{path}", cl.get(f"/mentor/students/{A['sid']}/{path}"), bad, who)
    rec("faculty", "write a note on A", cl.post(f"/mentor/students/{A['sid']}/notes", json={"note_text": "x"}), bad | {422}, who)
    if note_id:
        rec("faculty", "delete FX's note on A", cl.delete(f"/mentor/students/{A['sid']}/notes/{note_id}"), bad, who)
    rec("faculty", "read A's upload", cl.get(f"/mentor/uploads/{up_id}/file"), bad, who)
    rec("faculty", "review A's upload", cl.post(f"/mentor/uploads/{up_id}/review", json={"decision": "VERIFY"}), bad, who)
    rec("faculty", "award A a badge", cl.post(f"/mentor/students/{A['sid']}/badges/NEGOTIATION/award", json={"reason": "x"}), bad | {422}, who)
    rec("faculty", "assess A", cl.post(f"/mentor/students/{A['sid']}/assessments", json={"checkpoint": "T0", "scores": {"COMMUNICATION": 5}}), bad, who)
    rec("faculty", "notebook entries for A", cl.get(f"/v1/mentor/notebook/students/{A['sid']}/entries"), bad, who)
    rec("faculty", "A's leave paper", cl.get(f"/leaves/{lv_id}/paper.pdf"), bad, who)
    rec("faculty", "/admin/students/A/360", cl.get(f"/admin/students/{A['sid']}/360"), bad, who)
    rec("faculty", "A's application CV", cl.get(f"/register/{A['reg']}/documents/cv/file"), bad, who)
    m = cl.get("/mentor/mentees")
    leaked = m.status_code == 200 and A["sid"] in m.text
    RESULTS.append(dict(area="faculty", name="mentee list contains A", who=who, got=m.status_code, expect="absent", ok=not leaked, body=""))
fy_m = FY["cl"].get("/mentor/mentees")
RESULTS.append(dict(area="control", name="FY sees own mentee C", who="FY", got=fy_m.status_code, expect="C present",
                    ok=fy_m.status_code == 200 and C["sid"] in fy_m.text, body=""))

# students cannot use staff surfaces at all
for path in ("/mentor/mentees", "/admin/students", "/register/pending", "/admin/mail", "/admin/audit"):
    rec("role", f"student GET {path}", A["cl"].get(path), {403, 404}, "A")

# ----------------------------------------------------- one device ----
second = login(A["email"])
r = A["cl"].get("/student/uploads")
rec("session", "older device signed out after a new sign-in", r, 401, "A")
RESULTS.append(dict(area="session", name="retired header present", who="A", got=r.status_code, expect="X-Reep-Session: retired",
                    ok=r.headers.get("x-reep-session") == "retired", body=""))

json.dump(dict(run=RUN, a=A["sid"], b=B["sid"], c=C["sid"], upload=up_id, resume=rs_id, leave=lv_id, note=note_id,
               results=RESULTS), open(sys.argv[1] if len(sys.argv) > 1 else "isolation.json", "w"), indent=1)
fails = [r for r in RESULTS if not r["ok"]]
print(f"{len(RESULTS)} probes, {len(fails)} failures, run={RUN}")
for f in fails:
    print(f"FAIL [{f['area']}] ({f['who']}) {f['name']}: got {f['got']} expected {f['expect']} :: {f['body'][:150]}")
