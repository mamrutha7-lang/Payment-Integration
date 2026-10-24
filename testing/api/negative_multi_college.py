"""Negative-scenario sweep across 10 colleges, each with different branches.

Run against a LIVE dev stack (ENV=dev, dev seed applied, API on :3300), never
production and never beside pytest -- it signs in as the seeded admin, student
and mentor, and REEP keeps one live session per account:

    cd apps/api-py && .venv/bin/python ../../testing/api/negative_multi_college.py out.json

It builds 10 colleges (C1..C10, a run suffix keeps codes unique) with 1-4
departments each, a course, a specialization and a batch per department, then
fires ~165 negative probes: authorisation, blank/duplicate/oversized input on
every rung of the spine, cross-college ids on batches and applications,
bad files on /register, review decisions in the wrong order, faculty and
mentor assignment across colleges, moving students across colleges, and the
deletion guards. Each probe records the status it got and the status a correct
API should give; any 5xx is a failure. Results: testing/docs/06-negative-multi-college-report.md.

Registration is rate-limited per client address, so every /register call
sends a random X-Forwarded-For (uvicorn trusts it from 127.0.0.1 by default),
the way 10 colleges' applicants arrive from different addresses in production.
"""
import io, json, random, string, sys, uuid
import httpx

API = "http://localhost:3300/api"
RESULTS = []
RUN = uuid.uuid4().hex[:4].upper()


def rec(area, name, resp, expect, note=""):
    got = resp.status_code if hasattr(resp, "status_code") else resp
    ok = (got in expect) if isinstance(expect, (set, tuple, list)) else got == expect
    if isinstance(got, int) and got >= 500:
        ok = False
    body = ""
    try:
        body = resp.text[:220]
    except Exception:
        pass
    RESULTS.append(dict(area=area, name=name, got=got, expect=str(expect), ok=ok, note=note, body=body))
    return resp


def client():
    return httpx.Client(base_url=API, timeout=30)


def login(email, pw):
    c = client()
    r = c.post("/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, (email, r.text)
    return c


admin = login("admin@bgscet.ac.in", "admin123")
anon = client()

COLLEGES = [
    ("C1", "Alpha Institute of Technology", ["CSE", "ECE", "MECH"]),
    ("C2", "Beta Engineering College", ["CIVIL", "EEE"]),
    ("C3", "Gamma College of Engineering", ["ISE", "AIML", "CSE"]),
    ("C4", "Delta School of Management", ["MBA"]),
    ("C5", "Epsilon Polytechnic", ["MECH", "AUTO", "CHEM"]),
    ("C6", "Zeta Institute", ["MCA", "CSE"]),
    ("C7", "Eta Women's College", ["ECE", "BT", "EEE", "CSE"]),
    ("C8", "Theta Technical University", ["AERO", "MECH"]),
    ("C9", "Iota College", ["CSDS", "CSBS"]),
    ("C10", "Kappa Institute of Science", ["PHY", "MATHS", "CHEM"]),
]

tree = {}
for code, name, branches in COLLEGES:
    ccode = f"{code}{RUN}"
    r = admin.post("/admin/colleges", json={"code": ccode, "name": name, "email_domains": [f"{code.lower()}{RUN.lower()}.edu.in"]})
    rec("setup", f"create college {ccode}", r, 201)
    cid = r.json()["id"]
    tree[code] = {"id": cid, "code": ccode, "domain": f"{code.lower()}{RUN.lower()}.edu.in", "depts": {}}
    for b in branches:
        r = admin.post(f"/admin/colleges/{cid}/departments", json={"code": b, "name": f"{b} dept"})
        rec("setup", f"{ccode} dept {b}", r, 201)
        did = r.json()["id"]
        r = admin.post(f"/admin/departments/{did}/academic-courses", json={"code": "BE", "name": f"B.E. {b}", "duration_months": 48})
        rec("setup", f"{ccode}/{b} course", r, 201)
        crid = r.json()["id"]
        r = admin.post(f"/admin/academic-courses/{crid}/academic-specializations", json={"code": "GEN", "name": f"{b} General"})
        rec("setup", f"{ccode}/{b} spec", r, 201)
        sid = r.json()["id"]
        r = admin.post(f"/admin/departments/{did}/cohorts", json={
            "code": f"{ccode}-{b}-BE-2026-30", "name": "2026-30", "batch_label": "2026-30", "degree_level": "UG",
            "entry_date": "2026-07-01", "expected_completion": "2030-06-30", "specialization_id": sid})
        rec("setup", f"{ccode}/{b} batch", r, 201)
        tree[code]["depts"][b] = {"id": did, "course": crid, "spec": sid, "cohort": r.json().get("id")}

c1, c2 = tree["C1"], tree["C2"]
d_c1 = c1["depts"]["CSE"]; d_c2 = c2["depts"]["CIVIL"]

# ---------------------------------------------------------------- authz ----
for who, cl in (("anonymous", anon),):
    rec("authz", f"{who} lists colleges", cl.get("/admin/colleges"), 401)
    rec("authz", f"{who} creates college", cl.post("/admin/colleges", json={"code": "X", "name": "X"}), 401)
stu = login("student@bgscet.ac.in", "student123")
men = login("mentor@bgscet.ac.in", "mentor123")
for who, cl in (("student", stu), ("mentor", men)):
    rec("authz", f"{who} lists colleges", cl.get("/admin/colleges"), 403)
    rec("authz", f"{who} creates college", cl.post("/admin/colleges", json={"code": "X" + RUN, "name": "X"}), 403)
    rec("authz", f"{who} adds dept to C1", cl.post(f"/admin/colleges/{c1['id']}/departments", json={"code": "Z", "name": "Z"}), 403)
    rec("authz", f"{who} patches C1", cl.patch(f"/admin/colleges/{c1['id']}", json={"name": "pwned"}), 403)
rec("authz", "forged cookie", httpx.get(API + "/admin/colleges", cookies={"reep_session": "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiQURNSU4ifQ.x"}), 401)
rec("authz", "alg=none cookie", httpx.get(API + "/admin/colleges", cookies={"reep_session": "eyJhbGciOiJub25lIn0.eyJyb2xlIjoiQURNSU4iLCJ1c2VySWQiOiJ4In0."}), 401)

# ------------------------------------------------------------- colleges ----
rec("college", "duplicate code", admin.post("/admin/colleges", json={"code": c1["code"], "name": "dup"}), 409)
rec("college", "duplicate code lowercase", admin.post("/admin/colleges", json={"code": c1["code"].lower(), "name": "dup"}), 409)
rec("college", "duplicate code padded", admin.post("/admin/colleges", json={"code": f"  {c1['code']} ", "name": "dup"}), 409)
r = rec("college", "whitespace-only code", admin.post("/admin/colleges", json={"code": "   ", "name": "Blank code"}), 422)
r = rec("college", "whitespace-only name", admin.post("/admin/colleges", json={"code": "WS" + RUN, "name": "   "}), 422)
rec("college", "empty code", admin.post("/admin/colleges", json={"code": "", "name": "x"}), 422)
rec("college", "code 33 chars", admin.post("/admin/colleges", json={"code": "A" * 33, "name": "x"}), 422)
rec("college", "name 201 chars", admin.post("/admin/colleges", json={"code": "LN" + RUN, "name": "n" * 201}), 422)
rec("college", "missing name", admin.post("/admin/colleges", json={"code": "MN" + RUN}), 422)
rec("college", "code is number", admin.post("/admin/colleges", json={"code": 123, "name": "x"}), 422)
rec("college", "code with SQL", admin.post("/admin/colleges", json={"code": "X';DROP--", "name": "x"}), {201, 409, 422})
rec("college", "invalid domain 'not a domain'", admin.post("/admin/colleges", json={"code": "BD" + RUN, "name": "Bad domain", "email_domains": ["not a domain", "http://x.com/", "@@", "gmail.com"]}), 422,
    note="junk / public-mail domains should be refused")
rec("college", "body not JSON", admin.post("/admin/colleges", content=b"{bad", headers={"content-type": "application/json"}), 422)
rec("college", "PATCH unknown id", admin.patch("/admin/colleges/nope", json={"name": "x"}), 404)
rec("college", "PATCH status garbage", admin.patch(f"/admin/colleges/{c1['id']}", json={"status": "DELETED"}), 422)
rec("college", "PATCH name null", admin.patch(f"/admin/colleges/{c1['id']}", json={"name": None}), 422)
rec("college", "PATCH code -> other college's code", admin.patch(f"/admin/colleges/{c1['id']}", json={"code": c2["code"].lower()}), 409)
rec("college", "PATCH code whitespace", admin.patch(f"/admin/colleges/{c2['id']}", json={"code": "   "}), 422)
rec("college", "PATCH name whitespace", admin.patch(f"/admin/colleges/{c2['id']}", json={"name": "   "}), 422)
rec("college", "PATCH unknown field", admin.patch(f"/admin/colleges/{c2['id']}", json={"id": "hack"}), {200, 422})

# ---------------------------------------------------------- departments ----
rec("dept", "dup code same college", admin.post(f"/admin/colleges/{c1['id']}/departments", json={"code": "CSE", "name": "x"}), 409)
rec("dept", "dup code lowercase", admin.post(f"/admin/colleges/{c1['id']}/departments", json={"code": "cse", "name": "x"}), 409)
rec("dept", "same code different college ok", admin.post(f"/admin/colleges/{c2['id']}/departments", json={"code": "CSE", "name": "CSE at C2"}), 201)
rec("dept", "under missing college", admin.post("/admin/colleges/nope/departments", json={"code": "Q", "name": "x"}), 404)
rec("dept", "whitespace code", admin.post(f"/admin/colleges/{c1['id']}/departments", json={"code": "  ", "name": "x"}), 422)
rec("dept", "whitespace name", admin.post(f"/admin/colleges/{c1['id']}/departments", json={"code": "WSN", "name": "  "}), 422)
rec("dept", "PATCH code to sibling's code", admin.patch(f"/admin/departments/{c1['depts']['ECE']['id']}", json={"code": "mech"}), 409)
rec("dept", "PATCH status garbage", admin.patch(f"/admin/departments/{d_c1['id']}", json={"status": "zzz"}), 422)
rec("dept", "list depts of missing college", admin.get("/admin/colleges/nope/departments"), 404)
r = admin.post(f"/admin/colleges/{c1['id']}/departments", json={"code": "WSN2", "name": "x"})
rec("dept", "PATCH code whitespace", admin.patch(f"/admin/departments/{r.json()['id']}", json={"code": "   "}), 422)

# -------------------------------------------------------------- courses ----
rec("course", "duration 0", admin.post(f"/admin/departments/{d_c1['id']}/academic-courses", json={"code": "Z0", "name": "z", "duration_months": 0}), 422)
rec("course", "duration -12", admin.post(f"/admin/departments/{d_c1['id']}/academic-courses", json={"code": "Z1", "name": "z", "duration_months": -12}), 422)
rec("course", "duration 121", admin.post(f"/admin/departments/{d_c1['id']}/academic-courses", json={"code": "Z2", "name": "z", "duration_months": 121}), 422)
rec("course", "dup code same dept", admin.post(f"/admin/departments/{d_c1['id']}/academic-courses", json={"code": "be", "name": "dup"}), 409)
rec("course", "missing dept", admin.post("/admin/departments/nope/academic-courses", json={"code": "Z3", "name": "z"}), 404)
rec("course", "whitespace code", admin.post(f"/admin/departments/{d_c1['id']}/academic-courses", json={"code": "  ", "name": "z"}), 422)
rec("course", "PATCH duration 0", admin.patch(f"/admin/academic-courses/{d_c1['course']}", json={"duration_months": 0}), 422)
rec("course", "PATCH status garbage", admin.patch(f"/admin/academic-courses/{d_c1['course']}", json={"status": "nope"}), 422)
rec("spec", "dup code same course", admin.post(f"/admin/academic-courses/{d_c1['course']}/academic-specializations", json={"code": "gen", "name": "dup"}), 409)
rec("spec", "missing course", admin.post("/admin/academic-courses/nope/academic-specializations", json={"code": "X", "name": "x"}), 404)
rec("spec", "whitespace code", admin.post(f"/admin/academic-courses/{d_c1['course']}/academic-specializations", json={"code": "   ", "name": "x"}), 422)

# -------------------------------------------------------------- cohorts ----
base = {"name": "neg", "batch_label": "2026-30", "degree_level": "UG", "entry_date": "2026-07-01", "expected_completion": "2030-06-30"}
def coh(dept, **kw):
    b = dict(base, code=f"NEG-{uuid.uuid4().hex[:6]}"); b.update(kw)
    return admin.post(f"/admin/departments/{dept}/cohorts", json=b)
rec("cohort", "end before start", coh(d_c1["id"], entry_date="2030-01-01", expected_completion="2026-01-01"), 422)
rec("cohort", "end == start", coh(d_c1["id"], entry_date="2026-07-01", expected_completion="2026-07-01"), 422)
rec("cohort", "spec from other college", coh(d_c1["id"], specialization_id=d_c2["spec"]), 422)
rec("cohort", "course from other college", coh(d_c1["id"], course_id=d_c2["course"]), 422)
rec("cohort", "spec of course A + course B", coh(d_c1["id"], course_id=d_c1["course"], specialization_id=c1["depts"]["ECE"]["spec"]), 422)
rec("cohort", "nonexistent spec id", coh(d_c1["id"], specialization_id="nope"), {404, 422})
rec("cohort", "duplicate cohort code", coh(d_c1["id"], code=f"{c1['code']}-CSE-BE-2026-30"), 409)
rec("cohort", "duplicate cohort code lowercase", coh(d_c1["id"], code=f"{c1['code']}-CSE-BE-2026-30".lower()), 409)
rec("cohort", "degree_level garbage", coh(d_c1["id"], degree_level="PHD"), 422)
rec("cohort", "invalid date", coh(d_c1["id"], entry_date="2026-02-30"), 422)
rec("cohort", "whitespace code", coh(d_c1["id"], code="   "), 422)
rec("cohort", "whitespace name", coh(d_c1["id"], name="   "), 422)
rec("cohort", "missing dept", admin.post("/admin/departments/nope/cohorts", json=dict(base, code="NEGX")), 404)
cid_c1 = d_c1["cohort"]
rec("cohort", "PATCH end before start", admin.patch(f"/admin/cohorts/{cid_c1}", json={"expected_completion": "2020-01-01"}), 422)
rec("cohort", "PATCH move to other college's dept but keep spec", admin.patch(f"/admin/cohorts/{cid_c1}", json={"department_id": d_c2["id"]}), 422)
rec("cohort", "PATCH name null", admin.patch(f"/admin/cohorts/{cid_c1}", json={"name": None}), 422)
rec("cohort", "PATCH name whitespace", admin.patch(f"/admin/cohorts/{cid_c1}", json={"name": "   "}), 422)
rec("cohort", "PATCH unknown cohort", admin.patch("/admin/cohorts/nope", json={"name": "x"}), 404)
rec("cohort", "DELETE unknown cohort", admin.delete("/admin/cohorts/nope"), 404)

# --------------------------------------------------------- registration ----
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")

def reg(college, dept, cv=PDF, photo=PNG, cvname="cv.pdf", cvtype="application/pdf", phname="p.png", phtype="image/png", **kw):
    usn = "1" + "".join(random.choices(string.ascii_uppercase + string.digits, k=9))
    data = {
        "name": "Test Student", "email": f"{usn.lower()}@{college['domain']}", "usn": usn, "phone": "9876543210",
        "personal_email": f"{usn.lower()}@gmail.com", "linkedin_url": "https://www.linkedin.com/in/test",
        "degree_level": "UG", "college_id": college["id"], "department_id": dept["id"],
        "course_id": dept["course"], "specialization_ids": dept["spec"], "requested_cohort_id": dept["cohort"],
    }
    data.update(kw)
    data = {k: v for k, v in data.items() if v is not ...}
    files = {}
    if cv is not None: files["cv"] = (cvname, cv, cvtype)
    if photo is not None: files["photo"] = (phname, photo, phtype)
    ip = f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"
    return anon.post("/register", data=data, files=files, headers={"X-Forwarded-For": ip}), data

r, ok_data = reg(c1, d_c1)
rec("register", "baseline valid application C1/CSE", r, 201)
for code in ("C3", "C5", "C7", "C10"):
    dd = list(tree[code]["depts"].values())[-1]
    rec("register", f"baseline valid application {code}", reg(tree[code], dd)[0], 201)
rec("register", "duplicate submission same email", reg(c1, d_c1, email=ok_data["email"])[0], 409)
rec("register", "duplicate email UPPERCASE", reg(c1, d_c1, email=ok_data["email"].upper())[0], 409)
rec("register", "no CV", reg(c1, d_c1, cv=None)[0], 422)
rec("register", "no photo", reg(c1, d_c1, photo=None)[0], 422)
rec("register", "CV is text renamed .pdf", reg(c1, d_c1, cv=b"hello world", cvname="cv.pdf")[0], 415)
rec("register", "CV empty file", reg(c1, d_c1, cv=b"")[0], {415, 422})
rec("register", "photo is a PDF", reg(c1, d_c1, photo=PDF, phname="p.png", phtype="image/png")[0], 415)
rec("register", "CV 11 MB", reg(c1, d_c1, cv=PDF + b"0" * (11 * 1024 * 1024))[0], 413)
rec("register", "CV is HTML with script", reg(c1, d_c1, cv=b"<html><script>alert(1)</script>", cvname="cv.pdf")[0], 415)
rec("register", "dept of other college", reg(c1, d_c2)[0], 422)
rec("register", "college C1 + course of C2", reg(c1, d_c1, course_id=d_c2["course"])[0], 422)
rec("register", "spec of other dept", reg(c1, d_c1, specialization_ids=c1["depts"]["ECE"]["spec"])[0], 422)
rec("register", "cohort of other college", reg(c1, d_c1, requested_cohort_id=d_c2["cohort"])[0], 422)
rec("register", "nonexistent college", reg(c1, d_c1, college_id="nope")[0], 422)
rec("register", "invalid college email", reg(c1, d_c1, email="not-an-email")[0], 422)
rec("register", "college email is gmail", reg(c1, d_c1, email="someone123@gmail.com")[0], 422)
rec("register", "personal == college email", reg(c1, d_c1, personal_email=ok_data["email"].replace(ok_data['usn'].lower(), "zz9"), email=ok_data["email"].replace(ok_data['usn'].lower(), "zz9"))[0], 422)
rec("register", "LinkedIn is instagram", reg(c1, d_c1, linkedin_url="https://instagram.com/test")[0], 422)
rec("register", "LinkedIn javascript:", reg(c1, d_c1, linkedin_url="javascript:alert(1)//linkedin.com/in/x")[0], 422)
rec("register", "name whitespace only", reg(c1, d_c1, name="    ")[0], 422)
rec("register", "USN whitespace", reg(c1, d_c1, usn="   ")[0], 422)
rec("register", "USN 33 chars", reg(c1, d_c1, usn="1" * 33)[0], 422)
rec("register", "phone letters", reg(c1, d_c1, phone="call me maybe")[0], 422)
rec("register", "name with HTML", reg(c1, d_c1, name="<img src=x onerror=alert(1)>")[0], {201, 422})
c3 = tree["C3"]; c3cse = c3["depts"]["CSE"]
extra = [admin.post(f"/admin/academic-courses/{c3cse['course']}/academic-specializations", json={"code": f"S{i}", "name": f"Stream {i}"}).json()["id"] for i in (2, 3)]
rec("register", "3 distinct specializations", reg(c3, c3cse, specialization_ids=[c3cse["spec"], *extra], requested_cohort_id=...)[0], 422)
admin.patch(f"/admin/departments/{c3cse['id']}", json={"status": "ARCHIVED"})
rec("register", "archived department", reg(c3, c3cse)[0], 422, note="an archived branch should not take applications")
admin.patch(f"/admin/departments/{c3cse['id']}", json={"status": "ACTIVE"})
rec("register", "degree_level garbage", reg(c1, d_c1, degree_level="PHD")[0], 422)
rec("register", "submission_key bad pattern", reg(c1, d_c1, submission_key="short")[0], 422)
rec("register", "archived college", (admin.patch(f"/admin/colleges/{tree['C9']['id']}", json={"status": "ARCHIVED"}), reg(tree["C9"], tree["C9"]["depts"]["CSDS"])[0])[1], 422,
    note="an archived college should not take applications")
admin.patch(f"/admin/colleges/{tree['C9']['id']}", json={"status": "ACTIVE"})
rec("register", "USN reused at another email", reg(c1, d_c1, usn=ok_data["usn"])[0], {409, 422, 201}, note="duplicate USN")

# --------------------------------------------------------- review queue ----
q = admin.get("/register/pending")
rows = q.json() if q.status_code == 200 else []
byemail = {x["email"]: x for x in rows}
def pending_for(email):
    return byemail.get(email.lower()) or byemail.get(email)
mine = pending_for(ok_data["email"])
student_c1 = None
if mine:
    rid = mine["id"]
    rec("review", "reject without reason", admin.post(f"/register/{rid}/decision", json={"decision": "REJECT"}), 422)
    rec("review", "reject whitespace reason", admin.post(f"/register/{rid}/decision", json={"decision": "REJECT", "note": "   "}), 422)
    rec("review", "decision garbage", admin.post(f"/register/{rid}/decision", json={"decision": "MAYBE"}), 422)
    rl = rec("review", "decision lowercase approve", admin.post(f"/register/{rid}/decision", json={"decision": "approve"}), {200, 422})
    rec("review", "student decides", stu.post(f"/register/{rid}/decision", json={"decision": "APPROVE"}), 403)
    rec("review", "mentor decides", men.post(f"/register/{rid}/decision", json={"decision": "APPROVE"}), 403)
    r = rec("review", "approve", admin.post(f"/register/{rid}/decision", json={"decision": "APPROVE"}), {200, 409})
    student_c1 = next((x.json().get("approved_student_id") for x in (rl, r) if x.status_code == 200), None)
    rec("review", "approve twice", admin.post(f"/register/{rid}/decision", json={"decision": "APPROVE"}), 409)
    rec("review", "reject after approve", admin.post(f"/register/{rid}/decision", json={"decision": "REJECT", "note": "x"}), 409)
    rec("review", "hold after approve", admin.post(f"/register/{rid}/hold", json={"note": "x"}), {409, 422})
    rec("review", "re-apply after approval", reg(c1, d_c1, email=ok_data["email"])[0], 409)
else:
    RESULTS.append(dict(area="review", name="find pending row", got=q.status_code, expect="row", ok=False, note="", body=q.text[:200]))
rec("review", "decide missing id", admin.post("/register/nope/decision", json={"decision": "APPROVE"}), 404)
rec("review", "anon lists pending", anon.get("/register/pending"), 401)
rec("review", "student lists pending", stu.get("/register/pending"), 403)

# reject then re-apply (should be allowed), approve a C2 applicant for later
r2, d2 = reg(c2, d_c2)
row2 = admin.get("/register/pending").json()
row2 = next((x for x in row2 if x["email"] == d2["email"]), None)
if row2:
    rec("review", "reject with reason", admin.post(f"/register/{row2['id']}/decision", json={"decision": "REJECT", "note": "USN typo"}), 200)
    rec("review", "re-apply after REJECT allowed", reg(c2, d_c2, email=d2["email"])[0], 201)
r3, d3 = reg(c2, d_c2)
row3 = next((x for x in admin.get("/register/pending").json() if x["email"] == d3["email"]), None)
student_c2 = None
if row3:
    rr = admin.post(f"/register/{row3['id']}/decision", json={"decision": "APPROVE"})
    student_c2 = rr.json().get("approved_student_id") if rr.status_code == 200 else None

# ------------------------------------------------- faculty across colleges ----
def fac(college, dept, **kw):
    b = {"name": "Prof Test", "email": f"prof{uuid.uuid4().hex[:6]}@{college['domain']}", "department_id": dept["id"]}
    b.update(kw)
    return admin.post("/admin/faculty", json=b)
r = rec("faculty", "create faculty C1/CSE", fac(c1, d_c1), 201)
fac_c1 = r.json().get("id") or r.json().get("user_id") if r.status_code == 201 else None
r = rec("faculty", "create faculty C2/CIVIL", fac(c2, d_c2), 201)
fac_c2 = r.json().get("id") or r.json().get("user_id") if r.status_code == 201 else None
rec("faculty", "email off college domain", fac(c1, d_c1, email=f"x{uuid.uuid4().hex[:5]}@gmail.com"), {403, 422})
rec("faculty", "email of C2 domain filed under C1", fac(c1, d_c1, email=f"x{uuid.uuid4().hex[:5]}@{c2['domain']}"), {403, 422})
rec("faculty", "external w/o reason", fac(c1, d_c1, email=f"x{uuid.uuid4().hex[:5]}@gmail.com", allow_external=True), 422)
rec("faculty", "blank name", fac(c1, d_c1, name="   "), 422)
rec("faculty", "bad email", fac(c1, d_c1, email="nope"), 422)
rec("faculty", "missing department_id", admin.post("/admin/faculty", json={"name": "x", "email": f"a@{c1['domain']}"}), 422)
rec("faculty", "nonexistent department", fac(c1, {"id": "nope"}), {404, 422})
rec("faculty", "duplicate email", admin.post("/admin/faculty", json={"name": "x", "email": "mentor@bgscet.ac.in", "department_id": d_c1["id"]}), {409, 422})
rec("faculty", "email = existing student's", admin.post("/admin/faculty", json={"name": "x", "email": ok_data["email"], "department_id": d_c1["id"]}), 409)

if student_c1 and fac_c2:
    rec("mentor", "assign C2 faculty to C1 student", admin.post(f"/admin/students/{student_c1}/mentor", json={"mentor_user_id": fac_c2, "reason": "Testing a negative scenario"}), 422)
if student_c1 and fac_c1:
    rec("mentor", "assign w/o reason", admin.post(f"/admin/students/{student_c1}/mentor", json={"mentor_user_id": fac_c1}), 422)
    rec("mentor", "assign whitespace reason", admin.post(f"/admin/students/{student_c1}/mentor", json={"mentor_user_id": fac_c1, "reason": "   "}), 422)
    rec("mentor", "assign same-college", admin.post(f"/admin/students/{student_c1}/mentor", json={"mentor_user_id": fac_c1, "reason": "ok"}), 204)
    rec("mentor", "assign student user id as mentor", admin.post(f"/admin/students/{student_c1}/mentor", json={"mentor_user_id": student_c1, "reason": "Testing a negative scenario"}), {404, 422})
    rec("mentor", "assign the ADMIN as mentor", admin.post(f"/admin/students/{student_c1}/mentor", json={"mentor_user_id": admin.get('/auth/me').json()['userId'], "reason": "Testing a negative scenario"}), {404, 422})
rec("mentor", "assign to missing student", admin.post("/admin/students/nope/mentor", json={"mentor_user_id": fac_c1, "reason": "Testing a negative scenario"}), 404)
if student_c1:
    rec("seat", "bulk-move C1 batch into C2 batch", admin.post(f"/admin/cohorts/{d_c1['cohort']}/students/bulk", json={"action": "move", "cohort_id": c2["depts"]["EEE"]["cohort"]}), 422,
        note="bulk move carries the C1 mentor across the college fence")
    admin.put(f"/admin/students/{student_c1}/cohort", json={"cohort_id": d_c1["cohort"]})
    rec("seat", "seat C1 student into C2 batch (PUT cohort)", admin.put(f"/admin/students/{student_c1}/cohort", json={"cohort_id": d_c2["cohort"]}), 422,
        note="moving a student to another college's batch while their C1 mentor stays")
    rec("seat", "PATCH student into C2 batch", admin.patch(f"/admin/students/{student_c1}", json={"cohort_id": d_c2["cohort"]}), 422)
    rec("seat", "seat into missing batch", admin.put(f"/admin/students/{student_c1}/cohort", json={"cohort_id": "nope"}), 404)
    rec("seat", "PATCH USN to existing USN", admin.patch(f"/admin/students/{student_c1}", json={"usn": "1BG24MBA001"}), 409)
    rec("seat", "PATCH email to admin's", admin.patch(f"/admin/students/{student_c1}", json={"email": "admin@bgscet.ac.in"}), {409, 422})
    rec("seat", "PATCH semester 0", admin.patch(f"/admin/students/{student_c1}", json={"current_semester": 0}), 422)
    rec("seat", "PATCH semester 99", admin.patch(f"/admin/students/{student_c1}", json={"current_semester": 99}), 422)
    rec("seat", "PATCH name blank", admin.patch(f"/admin/students/{student_c1}", json={"name": "  "}), 422)
    rec("seat", "POST create student (should be 405)", admin.post("/admin/students", json={"name": "x"}), 405)
    rec("seat", "DELETE student (should be 405)", admin.delete(f"/admin/students/{student_c1}"), 405)

# ------------------------------------------ delete a college with people ----
plan = rec("deletion", "C1 delete plan (has a student)", admin.get(f"/admin/colleges/{c1['id']}/delete-plan"), 200)
rec("deletion", "delete C1 with wrong code", admin.post(f"/admin/colleges/{c1['id']}/delete", json={"code": "000000", "reason": "Testing a negative scenario"}), {403, 409})
rec("deletion", "delete with code of 5 chars", admin.post(f"/admin/colleges/{c1['id']}/delete", json={"code": "12345", "reason": "Testing a negative scenario"}), 422)
rec("deletion", "delete w/o reason", admin.post(f"/admin/colleges/{c1['id']}/delete", json={"code": "123456"}), 422)
rec("deletion", "mentor deletes college", men.post(f"/admin/colleges/{c1['id']}/delete", json={"code": "123456", "reason": "Testing a negative scenario"}), 403)
rec("deletion", "delete missing college", admin.post("/admin/colleges/nope/delete", json={"code": "123456", "reason": "Testing a negative scenario"}), {403, 404})
rec("deletion", "delete-plan missing college", admin.get("/admin/colleges/nope/delete-plan"), 404)
if student_c1:
    rec("deletion", "remove student w/o reason", admin.post(f"/admin/students/{student_c1}/remove", json={}), 422)
    rec("deletion", "restore not-removed student", admin.post(f"/admin/students/{student_c1}/restore", json={}), {409, 422})
    rec("deletion", "delete student wrong code", admin.post(f"/admin/students/{student_c1}/delete", json={"code": "000000", "reason": "Testing a negative scenario"}), 403)

# ------------------------------------------------------- leave negatives ----
for body, nm in (({"leave_type": "CASUAL", "from_date": "2026-12-20", "to_date": "2026-12-10", "reason": "Testing a negative scenario"}, "dates backwards"),
                 ({"leave_type": "CASUAL", "from_date": "2026-12-20", "to_date": "2026-12-21"}, "no reason")):
    rec("leave", nm, men.post("/leaves", json=body), 422)

# --------------------------------------------- wider: public hierarchy ----
h = anon.get("/register/hierarchy")
rec("public", "hierarchy reachable", h, 200)
if h.status_code == 200:
    txt = h.text
    rec("public", "archived/blank-name colleges not offered", 200 if '"name":""' not in txt else 299, 200,
        note="blank-named colleges/departments created above leak into the public form")
json.dump(RESULTS, open(sys.argv[1] if len(sys.argv) > 1 else "results.json", "w"), indent=1)
fails = [r for r in RESULTS if not r["ok"]]
print(f"{len(RESULTS)} probes, {len(fails)} failures, run={RUN}")
for f in fails:
    print(f"FAIL [{f['area']}] {f['name']}: got {f['got']} expected {f['expect']} :: {f['body'][:160]}")
