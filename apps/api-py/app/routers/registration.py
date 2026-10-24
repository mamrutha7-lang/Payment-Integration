"""Programme sign-up flow (ported from the Next.js registration path).

Public submission runs the data-driven rule engine: among enabled rules, every
*populated* condition (email domain, USN regex, degree level) must match, and the
lowest `priority` among the matches decides. A matching auto-approve rule waves
the application through (AUTO_APPROVED) and assigns its cohort; a matching
non-auto rule routes it to a human (PENDING_REVIEW) with a label; no match falls
to manual review. The Main Admin then approves/rejects the queue.

APPROVAL PROVISIONS. It was once true that it did not — this docstring said so
for the whole life of the file, three lines from the top, and it was the
original of the false claim B11.4 exists to strike. `decide` (APPROVE) mints the
User row, the Student row seated in the rule's cohort and a profile row, moves
the CV and photo onto that student's own uploads and emails the onboarding link,
all in one transaction; `_apply_rule` does the same for an auto-approved
application. Nothing exists before that decision.

WHAT APPROVAL DOES NOT DO IS LET ANYONE IN. The account carries the unusable
`SSO_ONLY_PASSWORD_HASH` sentinel, and the applicant must still open the emailed
link, type their address back, read a six-digit code, spend it for a ticket and
set a password before they can sign in (`app/routers/onboarding.py`, three
steps). "Approved" and "active" are two different facts and any copy that says
otherwise — here, or on the applicant's own result card — is wrong.
"""

import hashlib
import hmac
import logging
import re
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, NamedTuple, Sequence

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fastapi.responses import RedirectResponse

from .. import account_links, batch_labels, dual_specialization
from ..clock import local_today
from ..semester_bounds import semester_on
from ..config import settings
from ..db import get_db
from ..identity import get_current_session
from ..models.job import DegreeLevel
from ..institution_domains import (
    college_id_for_cohort,
    college_ids_for_cohorts,
    PUBLIC_MAIL_DOMAINS,
    domain_of,
    normalise_domain,
    provisionable_domains_for,
)
from ..models.cohort import Cohort
from ..models.institution import (
    HIERARCHY_LEVELS,
    STATUS_ACTIVE,
    AcademicCourse,
    AcademicSpecialization,
    College,
    Department,
)
from ..models.registration import (
    DOCUMENT_KIND_CV,
    DOCUMENT_KIND_PHOTO,
    PENDING_QUEUE_STATUSES,
    Registration,
    RegistrationDocument,
    RegistrationRule,
    RegistrationStatus,
)
from ..models.student_profile import StudentProfile
from ..models.user import Role, Student, User
from ..student_placement import resolve_student_department
from ..usn import normalise_usn, usn_is
from ..governance import require_capability
from ..policies import scope_filter
from ..scope_views import (
    registration_rule_scope_clause,
    registration_scope_clause,
    scope_header,
)
from ..architecture_events import record_change
from ..document_manifest import DocumentFacts, reattribute, release, save_and_record
from ..models.archived_document import DocumentOwnerKind
from ..document_store import (
    MAX_BYTES,
    QuotaRejected,
    VolumeQuota,
    content_disposition,
    read_bytes,
    sniff,
)
from ..document_store import delete as delete_stored
from ..models.upload import Upload, UploadKind

router = APIRouter(prefix="/register", tags=["registration"])

log = logging.getLogger(__name__)


# --- usn_pattern is DB-sourced regex run on an UNAUTHENTICATED endpoint -------
#
# `RegistrationRule.usn_pattern` is a regular expression that arrives from the
# database and is executed on POST /register, which has no cookie and no roster
# check in front of it. Two ways that ends badly, and in both the *rule* is the
# thing that is broken while the *front door* is the thing that falls over:
#
#   * an invalid pattern (a bare "[") raises re.error inside the engine, so every
#     USN-carrying application 500s and nothing in the response — or, before
#     this, in the log — names the rule that did it;
#   * a catastrophic pattern ("(a+)+$") backtracks exponentially: a ReDoS an
#     anonymous caller can fire at will, one HTTP request at a time.
#
# Planting either needs an admin/DB write, so this is a blast-radius problem
# rather than an entry point. It is defended twice anyway, because a rule row can
# always be edited straight in psql and skip whatever the write path checks:
#
#   1. validate_usn_pattern() at rule-WRITE time. app/seed.py is the only writer
#      today (the RegistrationRule(...) block); any future admin-facing rule
#      editor MUST call it before commit and hand the ValueError back to the
#      author, who is the only person able to fix the pattern.
#   2. _usn_matcher() at MATCH time, which refuses to run a pattern that fails
#      the same check and drops that one rule instead of the request.

# A USN format is a short fixed shape ("^1BG2[0-9]MBA[0-9]{3}$"). Nothing
# legitimate needs more than this, and the cap is what stops a pathological
# pattern being pasted in at all.
MAX_USN_PATTERN_LENGTH = 200

# Backtracking cost is a function of the SUBJECT length, so the engine is never
# handed a long one. RegisterIn already caps `usn` at 32, but _rule_matches is a
# module-level helper with its own tests and callers: it must not depend on its
# caller for the bound that keeps it cheap.
MAX_USN_MATCH_LENGTH = 64

# A quantifier applied to a group — "(...)+", "(...)*", "(...){2,}" — is the shape
# behind essentially every real-world ReDoS, including the overlapping-alternation
# form "(a|aa)+" that a nested-quantifier check alone would miss. Deciding whether
# an arbitrary regex backtracks catastrophically is not something to attempt in a
# router, and a USN pattern has no honest use for a quantified group, so the whole
# shape is refused. If you ever genuinely need one, the rule engine is the wrong
# place for it — match in code, not in a row an operator can edit.
_QUANTIFIED_GROUP = re.compile(r"\)[*+{]")


def validate_usn_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a rule's `usn_pattern`, refusing what the public endpoint cannot
    afford to run. Raises ValueError with a message written for whoever is
    authoring the rule — they are the only person who can fix it."""
    if len(pattern) > MAX_USN_PATTERN_LENGTH:
        raise ValueError(f"usn_pattern is longer than {MAX_USN_PATTERN_LENGTH} characters")
    if _QUANTIFIED_GROUP.search(pattern):
        raise ValueError(
            "usn_pattern applies a quantifier to a group — refused as a "
            "catastrophic-backtracking risk on the public registration endpoint"
        )
    try:
        # IGNORECASE (2026-10-16): `RegisterIn` upper-cases every USN now, so a
        # rule written in lower case (`^1mp25`) would otherwise match nobody
        # and send every applicant it was meant for to manual review, silently.
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"usn_pattern is not a valid regular expression: {exc}") from exc


@lru_cache(maxsize=256)
def _usn_matcher(pattern: str) -> re.Pattern[str] | None:
    """The compiled matcher for a rule, or None for a pattern we refuse to run.

    The cache does two jobs. It keeps regex compilation off the path of every
    application, and it makes the "unusable rule" WARNING fire once per distinct
    pattern per process rather than once per applicant — a log flooded by the
    hundredth copy is a log in which nobody finds the one line naming the bad
    rule. Fix the pattern in the database and the new string is a new cache key,
    so the fix takes effect with no restart; the WARNING reappears once if the
    new pattern is also unusable.
    """
    try:
        return validate_usn_pattern(pattern)
    except ValueError as exc:
        log.warning(
            "Registration rule has an unusable usn_pattern (%s) — that rule is "
            "SKIPPED, not matched: %r",
            exc,
            pattern[:120],
        )
        return None


def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def _rule_matches(rule: RegistrationRule, email: str, usn: str | None, degree: DegreeLevel) -> bool:
    """All populated conditions must hold; an empty condition is a wildcard."""
    if rule.email_domain and _email_domain(email) != rule.email_domain.lower():
        return False
    if rule.usn_pattern:
        matcher = _usn_matcher(rule.usn_pattern)
        if matcher is None:
            # An unusable pattern is a broken RULE, not a broken APPLICATION.
            # Skipping it lets the engine fall through to the next rule — worst
            # case, manual review — where raising took every USN-carrying
            # submission down with it.
            return False
        if not usn or len(usn) > MAX_USN_MATCH_LENGTH:
            # Longer than any real USN. Refused rather than truncated: a
            # truncated subject would let "^...$" match a prefix of something
            # that is not a USN at all.
            return False
        try:
            if not matcher.search(usn):
                return False
        except (re.error, RecursionError) as exc:
            # Compilation already succeeded, so this is the rare match-time
            # failure. Same verdict as an unusable pattern: lose the rule, keep
            # the endpoint.
            log.warning(
                "Registration rule usn_pattern failed at match time (%s) — rule "
                "SKIPPED: %r",
                exc,
                rule.usn_pattern[:120],
            )
            return False
    if rule.degree_level is not None and rule.degree_level != degree:
        return False
    return True


# --- The one unauthenticated write in the app ---------------------------------
#
# POST /register takes no cookie, consults no roster, and INSERTs a row. Without
# a limit it is both a database flood anyone can start and — together with the
# duplicate-email branch in submit() — a free "has this person applied here"
# lookup for anyone holding a list of names.
#
# Fixed window, keyed on the socket peer, held in this process. Three caveats
# stated plainly so nobody reads more into it than it gives:
#
#   * PER WORKER. N uvicorn workers give N times this limit, and a restart
#     forgets every window. It raises the cost of a flood; it is not a quota, and
#     a real one needs shared state (Postgres or Redis) this endpoint does not
#     have.
#   * The key is request.client.host, the SOCKET peer. Behind a reverse proxy
#     that is the proxy, so an entire campus can share one bucket — which is why
#     the limit is deliberately generous rather than tight: a class of sixty
#     applying from one NAT on results day must not lock itself out. If you put a
#     trusted proxy in front, read the real client from ITS header and say so
#     here. X-Forwarded-For is not trusted as it stands, because a header the
#     caller sets is a limiter the caller can switch off.
#   * The table is bounded, so the limiter cannot itself become the memory
#     exhaustion it exists to prevent. An attacker with thousands of live source
#     addresses can flush it — but they can also just spend the limit from each
#     of those addresses, so the table size was never the control.
_RATE_WINDOW_SECONDS = 600
_RATE_MAX_PER_WINDOW = 20
_RATE_MAX_KEYS = 4096
_rate_windows: dict[str, tuple[float, int]] = {}
#: `submit` and `attach_document` are plain `def` handlers since 2026-09-29, so
#: they run on the threadpool and two of them can count at once. The
#: read-modify-write below is not atomic, and without the lock two attempts
#: can both read `count` and both write `count + 1`.
_rate_lock = threading.Lock()


def _rate_limit_retry_after(client_ip: str) -> int | None:
    """Count this attempt. Returns None to proceed, or seconds until the window
    resets when the caller has spent it."""
    with _rate_lock:
        return _count_attempt(client_ip)


def _count_attempt(client_ip: str) -> int | None:
    now = time.monotonic()
    window_start, count = _rate_windows.get(client_ip, (now, 0))
    if now - window_start >= _RATE_WINDOW_SECONDS:
        window_start, count = now, 0
    if count >= _RATE_MAX_PER_WINDOW:
        # Not recorded: a caller who keeps hammering must not extend their own
        # window, or a refusal would become a permanent block.
        return max(1, int(_RATE_WINDOW_SECONDS - (now - window_start)))
    if client_ip not in _rate_windows and len(_rate_windows) >= _RATE_MAX_KEYS:
        for key, (started, _n) in list(_rate_windows.items()):
            if now - started >= _RATE_WINDOW_SECONDS:
                del _rate_windows[key]
        if len(_rate_windows) >= _RATE_MAX_KEYS:
            _rate_windows.clear()  # every window still live: start over, never grow
    _rate_windows[client_ip] = (window_start, count + 1)
    return None


def _pick_rule(
    db: Session, email: str, usn: str | None, degree: DegreeLevel
) -> RegistrationRule | None:
    """Lowest priority among the enabled rules that match wins (ties broken by
    creation order, so a rule added later can't silently outrank an equal)."""
    candidates = db.scalars(
        select(RegistrationRule)
        .where(RegistrationRule.enabled.is_(True))
        .order_by(RegistrationRule.priority, RegistrationRule.created_at)
    ).all()
    for rule in candidates:
        if _rule_matches(rule, email, usn, degree):
            return rule
    return None


def _looks_like_email(value: str) -> bool:
    """The same light shape check `submit` applies to the college address:
    an `@` with a dotted domain after it. Deliberately not the email-validator
    dependency; the domain is what the rule engine keys on."""
    return "@" in value and "." in value.rsplit("@", 1)[-1]


_PHONE_CHARS = re.compile(r"^[0-9+()\-. ]+$")


def _college_email_problem(college_email: str, personal_email: str) -> str | None:
    """Why the COLLEGE box cannot be what the student meant, or None.

    Reported 2026-09-29: the office saw only a Gmail address on applications
    whose students said they had given both. The college box carried
    `autocomplete="email"`, so a phone filled it with the student's saved
    address - their Gmail - and the application reached the queue with that
    address in both places. Approval refuses an address off the college's
    domains, so such an application could never be approved, and the student
    was told nothing.

    Only the two cases that are certainly wrong are refused here. An address
    merely off THIS college's domain list is left to the reviewer's
    domain check, as before, because a college that has not listed its
    domains yet must not have every applicant refused at the form.
    """
    if college_email == (personal_email or "").strip().lower():
        return (
            "Your college email and personal email are the same. Enter your college "
            "address (for example, your USN at your college's domain) in the college "
            "email box."
        )
    if domain_of(college_email) in PUBLIC_MAIL_DOMAINS:
        return (
            f"{college_email} is a personal address. Enter the email your college "
            "gave you in the college email box, and your personal address in the "
            "personal email box."
        )
    return None


#: How many specializations one application may name. TWO, because the office's
#: word for it is "dual specialization" and `registrations` has exactly two
#: columns for it (`specialization_id`, `second_specialization_id`); a bigger
#: number here would be a promise the row cannot keep. Served to the form by
#: `GET /register/hierarchy` (`max_specializations`) so the checklist's cap and
#: the schema's are one constant, not two.
MAX_SPECIALIZATIONS_PER_APPLICATION = 2


class RegisterIn(BaseModel):
    """EVERY FIELD THE FORM ASKS FOR IS REQUIRED HERE TOO (2026-09-16), except
    the claim of where the applicant belongs, which the hierarchy decides.

    The owner's rule for the public form is "everything is compulsory except
    Specialization". A rule the form keeps and the API does not is one a
    curl, an older bundle or a retry after a half-loaded page walks straight
    past, and the reviewer then meets an application with no phone number and
    no way to reach the person except the address they are still proving they
    own. So USN, phone, the personal address and the LinkedIn profile are
    required at the schema, stripped, and refused blank; the two columns
    they land in stay NULLABLE, because requiredness is a rule about new rows
    and nullability is a promise about the ones already written.

    THE CV AND THE PHOTO ARE REQUIRED HERE TOO, SINCE 2026-09-22, on
    `RegisterForm` below - the shape `POST /register` actually takes. They
    used to be posted to `attach_document` AFTER the 201, keyed on the id this
    endpoint minted, and the reviewer's checklist (`CHECK_DOCUMENTS`) named
    whichever was missing. That is how the office came to hold applications
    with no CV and no photo from students who had filled in every box: the
    application existed the moment the JSON landed, and the two uploads that
    followed could fail on their own - the edge refusing a body over its
    limit, a phone losing its connection, the applicant closing the tab at
    "Try attaching again" - and, for every application a rule AUTO-APPROVED
    at submit time, they were refused by design, because `attach_document`
    accepts no file on a decided application and the rule had decided it a
    moment before the upload arrived. So the files travel IN the request that
    creates the application, are judged before a row is written, and land in
    the same transaction; an application cannot exist without them.
    """

    name: str = Field(min_length=1, max_length=200)
    # A plain string with a light shape check (avoids the email-validator dep);
    # the domain is what the rule engine actually keys on.
    email: str = Field(min_length=3, max_length=200)
    usn: str = Field(min_length=1, max_length=32)
    phone: str = Field(min_length=1, max_length=32)
    #: A second address the office can reach when the college one is not yet
    #: live, or stops being. Never what the account is keyed on.
    personal_email: str = Field(min_length=3, max_length=200)
    #: A LinkedIn profile URL or handle; copied onto the student's profile at
    #: approval, where the placement record already asks for one.
    linkedin_url: str = Field(min_length=1, max_length=300)
    degree_level: DegreeLevel = DegreeLevel.PG

    @field_validator("name", "usn", "phone", "personal_email", "linkedin_url")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # `name` joined this list on 2026-10-13: `min_length=1` alone let a
        # name of spaces through, and the review queue showed an applicant
        # with no name at all.
        value = value.strip()
        if not value:
            raise ValueError("this field is required")
        return value

    @field_validator("usn")
    @classmethod
    def _usn_upper_case(cls, value: str) -> str:
        """Upper case, as the roster stores it (2026-10-16). Stored as typed,
        `1mp25ba033` was not matched against a student holding `1MP25BA033`,
        and approving it seated a second student with the same number. See
        app/usn.py. Runs after `_not_blank`, so the value is already stripped."""
        return value.upper()

    @field_validator("phone")
    @classmethod
    def _phone_shape(cls, value: str) -> str:
        """Digits with the usual separators, and a phone number's worth of
        digits. The value is copied onto the student's placement profile at
        approval, where "call me maybe" is a number nobody can ring."""
        if not _PHONE_CHARS.match(value):
            raise ValueError("enter a phone number using digits, spaces, + - ( ) only")
        digits = sum(ch.isdigit() for ch in value)
        if not 7 <= digits <= 15:
            raise ValueError("a phone number has between 7 and 15 digits")
        return value

    @field_validator(
        "college_id", "department_id", "course_id", "specialization_id", "requested_cohort_id",
        mode="before",
    )
    @classmethod
    def _blank_id_is_none(cls, value: object) -> object:
        """A picker left on "Choose ..." arrives as `null` from a JSON client and
        as `""` from a multipart one - the form's shape since 2026-09-22 - and
        the second must not reach `_resolve_claim` as a college id that "does
        not exist"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("personal_email")
    @classmethod
    def _personal_email_shape(cls, value: str) -> str:
        value = value.lower()
        if not _looks_like_email(value):
            raise ValueError("enter a valid personal email address")
        return value

    @field_validator("linkedin_url")
    @classmethod
    def _linkedin_shape(cls, value: str) -> str:
        """Accepts `linkedin.com/in/asha`, `www.linkedin.com/in/asha` or the
        full https URL and stores the https form; anything without a
        linkedin.com path is refused, because a free-text box labelled LinkedIn
        collects Instagram handles otherwise.

        The app's Share button hands out `in.linkedin.com/in/...` (the India
        subdomain), `m.linkedin.com/...` or a `lnkd.in/...` short link, and all
        three were refused with a 422 the form showed only as "(422)" - which
        is how students with a real profile were stopped at the last box
        (2026-09-29). Any linkedin.com subdomain is folded onto www, since it is
        the same profile; a short link is kept as given, because only
        LinkedIn can say where it points."""
        bare = value.strip()
        lowered = bare.lower()
        if lowered.startswith("http://"):
            bare = bare[len("http://"):]
        elif lowered.startswith("https://"):
            bare = bare[len("https://"):]
        host, _, path = bare.partition("/")
        host = host.lower()
        path = path.split("?", 1)[0].split("#", 1)[0].strip("/")
        if not path:
            raise ValueError("enter your LinkedIn profile link, e.g. linkedin.com/in/your-name")
        if host == "lnkd.in":
            return "https://lnkd.in/" + path
        if host == "linkedin.com" or host.endswith(".linkedin.com"):
            return "https://www.linkedin.com/" + path
        raise ValueError("enter your LinkedIn profile link, e.g. linkedin.com/in/your-name")
    # WHERE THE APPLICANT SAYS THEY BELONG, from the hierarchy the admin built.
    # Send the DEEPEST level known; the API derives the ancestors and refuses a
    # contradiction. All optional at the API - the form requires College and
    # Department, but an older client, a rule or a reviewer may still fill them.
    college_id: str | None = None
    department_id: str | None = None
    course_id: str | None = None
    specialization_id: str | None = None
    #: THE CHECKLIST (2026-09-22). The form's Specialization box is a list of
    #: tick boxes now, because a student who opted for a DUAL specialization
    #: has two to name and a <select> let them name one. At most
    #: MAX_SPECIALIZATIONS_PER_APPLICATION, all under one course, in the order
    #: ticked. `specialization_id` above is still accepted for an older client
    #: and is folded in FIRST by `_merge_specialization_picks`, so after
    #: validation THIS list is the whole claim and `_resolve_claim` reads
    #: nothing else.
    specialization_ids: list[str] = []
    requested_cohort_id: str | None = None

    @model_validator(mode="after")
    def _merge_specialization_picks(self) -> "RegisterIn":
        """One list, stripped, de-duplicated in the order ticked, capped.

        The legacy single field leads so that a client sending both (one pick
        in each) reads as one pick, not two; a box ticked twice is one box.
        The cap is a 422 here rather than a silent truncation, because a
        student who ticked three and was recorded with two has been told a
        different fact about themselves than they typed.
        """
        picks: list[str] = []
        for raw in (self.specialization_id, *self.specialization_ids):
            value = (raw or "").strip()
            if value and value not in picks:
                picks.append(value)
        if len(picks) > MAX_SPECIALIZATIONS_PER_APPLICATION:
            raise ValueError(
                f"tick at most {MAX_SPECIALIZATIONS_PER_APPLICATION} specializations: one, "
                "or two if you opted for a dual specialization"
            )
        self.specialization_ids = picks
        self.specialization_id = picks[0] if picks else None
        return self


class RegisterForm(RegisterIn):
    """What `POST /register` takes: every field of `RegisterIn` as a
    `multipart/form-data` part, plus the two files, BOTH REQUIRED.

    The files are fields of the model rather than separate `File()` parameters
    on the endpoint because FastAPI embeds a form model under its parameter
    name the moment a second body parameter appears, and the form would then
    have to post `body[name]`. One model, one flat multipart body.

    `RegisterIn` stays as the JSON-shaped schema for the two things that read
    it without a request: `_resolve_claim`, which takes either, and the tests
    that pin the field rules without a database.
    """

    #: The CV, a PDF (`_DOCUMENT_ROUTES["cv"]`), up to `MAX_BYTES`.
    cv: UploadFile
    #: The headshot, a PNG or a JPEG (`_DOCUMENT_ROUTES["photo"]`), same cap.
    photo: UploadFile
    #: The form's own random name for this application, the same on every
    #: submit of one filled-in form (`newSubmissionKey` in the client's
    #: form-checks.ts). OPTIONAL, so an older bundle still works: without it a
    #: retry meets the opaque 409 exactly as before. See `_duplicate_answer`.
    submission_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{16,64}$")


# --- the check vocabulary (B11.1) --------------------------------------------
#
# DEFINED ONCE, HERE. The pending queue draws these, and the Hold screen and the
# by-status tabs read the same words; a second vocabulary invented beside this
# one is two things on screen that look identical and mean different things.
#
# THREE VERDICTS, AND THE MIDDLE ONE IS THE POINT.
#
#   "blocked"  Approve WILL refuse this application, right now, with a 4xx. Every
#              blocked check is one-to-one with a guard in `_provision_student`,
#              and that is the whole contract: a blocked check the reviewer can
#              click past, or a guard with no check in front of it, is the queue
#              lying about what the button does.
#   "warn"     Approve will succeed. Something is still worth a human's eye —
#              no USN, no rule matched, a batch the applicant did not ask for.
#   "ok"       Nothing to report.
#
# Collapsing "warn" into "blocked" is the specific mistake to avoid: an applicant
# who ALREADY HOLDS A STUDENT ACCOUNT is the ordinary re-application path, which
# Approve handles by reusing the account. One "duplicate" chip makes that read
# like a wall.
#
# THE CHECKS ARE LIVE, and a `decision_reason` is HISTORY. A row whose reason
# begins "Rule 'X' would auto-approve, but:" was refused by a guard at submit
# time; if the college has added the domain since, that row's `domain` check is
# green and the reason is stale. The two do not contradict each other — they are
# answers to the same question at two different moments, and the check is the one
# that says what Approve will do now.
CHECK_OK = "ok"
CHECK_WARN = "warn"
CHECK_BLOCKED = "blocked"

#: Every key this endpoint can emit. The set on any one row is NOT fixed —
#: `usn_pattern` appears only where a matched rule declares a pattern to check
#: against, and `prior_applications` only where this address has been REJECTED
#: before, because a line saying "no pattern to check" or "never applied
#: before" is noise in a panel whose job is to be read in two seconds. Clients
#: render what they are given and key off `key`, never off position.
CHECK_RULE = "rule"
CHECK_DOMAIN = "domain"
CHECK_DUPLICATE_ACCOUNT = "duplicate_account"
CHECK_USN_UNIQUE = "usn_unique"
CHECK_USN_PATTERN = "usn_pattern"
#: A WARN, never a block: since 2026-09-16 a rejected address may apply again
#: (see `submit` and `uq_registration_live_email`), and the reviewer deciding
#: the new application is the one person who needs to know it is not the first
#: - and what the office said last time. Approve is not refused by it.
CHECK_PRIOR_APPLICATIONS = "prior_applications"
#: A WARN, never a block. Since 2026-09-22 `POST /register` refuses an
#: application that does not carry both files, so this line can only fire on
#: a row written BEFORE that - one whose upload failed after the 201, or was
#: refused because a rule had already auto-approved it. Those rows are still in
#: the queue and still decidable; the office can hold one and ask for the file
#: (`attach_document` still takes a replacement on an undecided application),
#: and the reviewer must see the gap before pressing the button, not after
#: the student has no resume.
CHECK_DOCUMENTS = "documents"
#: A WARN, never a block, and only where a SECOND specialization is on the row
#: (2026-09-22): the applicant ticked two on the form's checklist. It is on the
#: list because seating is by BATCH and a batch hangs on one specialization at
#: most, so the reviewer choosing where this person sits should know there are
#: two choices to weigh and that the second stays on the application.
CHECK_DUAL_SPECIALIZATION = "dual_specialization"


class CheckOut(BaseModel):
    """One line of the reviewer's pre-decision checklist."""

    #: Stable machine name — one of the CHECK_* constants above. The client
    #: branches on this and never on the label, which is prose and will change.
    key: str
    #: "ok" | "warn" | "blocked", as defined above.
    status: str
    #: The headline, already written for a human. Five words or so.
    label: str
    #: One sentence saying what it means for the decision about to be made.
    detail: str
    #: The student a check is about, where it names one (2026-10-16). Set only
    #: on `usn_unique` when ANOTHER student holds the USN and the reviewer's
    #: reach is the whole programme, so the panel can open that record: "is
    #: this the same person, or a typo?" cannot be answered from a USN alone.
    #: Null everywhere else, including for a scoped reviewer, whose reach may
    #: not cover the student holding the number.
    student_id: str | None = None


class RegistrationOut(BaseModel):
    id: str
    name: str
    email: str
    usn: str | None
    degree_level: str
    status: str
    cohort_id: str | None
    matched_rule_id: str | None
    decision_reason: str | None
    reviewed_by_id: str | None
    reviewed_at: datetime | None
    review_note: str | None
    approved_student_id: str | None
    created_at: datetime
    #: How to reach the applicant (2026-09-16): the phone and the personal
    #: address they typed, and the LinkedIn profile. Staff-only for free, like
    #: the hold stamp below - `PublicRegistrationOut` declares none of them.
    phone: str | None = None
    personal_email: str | None = None
    linkedin_url: str | None = None
    #: The HOLD stamp (B11.2) — who parked this application, when, and why. All
    #: three are null on every row that is not, and has never been, on hold.
    #: Staff-only for free: `PublicRegistrationOut` declares none of them, and
    #: `_public_out_one` narrows by `model_fields`. That is the point of a hold
    #: note — it is written ABOUT the applicant for colleagues, never TO them.
    hold_note: str | None = None
    held_by_id: str | None = None
    held_at: datetime | None = None
    # The applicant's claim, ids and resolved names (names for the queue and the
    # result card; ids so a client can re-select). requested_cohort_id is the
    # batch THEY picked; cohort_id above is the rule's, and wins.
    college_id: str | None = None
    department_id: str | None = None
    course_id: str | None = None
    specialization_id: str | None = None
    #: The other tick of a dual specialization (2026-09-22); null on every
    #: application that named one or none. `specialization_id` is unchanged.
    second_specialization_id: str | None = None
    requested_cohort_id: str | None = None
    college_name: str | None = None
    department_name: str | None = None
    course_name: str | None = None
    specialization_name: str | None = None
    second_specialization_name: str | None = None
    requested_batch: str | None = None
    #: Kinds attached with the application - "CV", "PHOTO" - so the queue can
    #: show a reviewer what is there before they open anything.
    documents: list[str] = []
    #: The pre-decision checklist (B11.1). NULL MEANS NOT COMPUTED, and that is
    #: deliberate — `GET /auth/me`'s `google_linked` precedent. Only the queue
    #: builds these, and only for a status somebody can still decide (see
    #: DECIDABLE_STATUSES); the single-row responses from `decision`, `hold` and
    #: `reopen` leave it null rather than answering `[]`, because an empty list
    #: reads as "nothing to report" and a client that renders it that way would
    #: show a clean bill of health for an application nobody checked.
    #:
    #: Absent from `PublicRegistrationOut` for free: `_public_out_one` narrows by
    #: `model_fields`. A check naming a duplicate account is the reviewer's side
    #: of the record and must never travel to the applicant.
    checks: list[CheckOut] | None = None


class PublicRegistrationOut(BaseModel):
    """What an APPLICANT is told about their own application.

    The two public endpoints on this router — the form itself and the two
    document uploads that follow it — answer with THIS, not with
    `RegistrationOut`. Neither caller is signed in: the bearer is the
    application id, an unguessable uuid4 handed back on the 201, and the same
    trust the emailed confirmation link carries. That is enough to let someone
    finish their own application; it is not enough to be shown the reviewer's
    side of it.

    So five fields are absent, and their absence is the point:
    `review_note` and `reviewed_by_id` / `reviewed_at` are the Main Admin writing
    ABOUT this person for colleagues, and `matched_rule_id` /
    `approved_student_id` are internal plumbing the result card never renders.

    `cohort_id` IS here, unlike those: it is the batch this applicant was
    actually placed in, which is their own fact about themselves and no more
    private than the `requested_cohort_id` they typed on the form.

    Today none of them could travel anyway — the upload endpoint refuses an
    application that has been decided, and `reopen` clears the reviewer's stamp
    on the way back into the queue. That is exactly why this is a SHAPE and not
    a filter: both of those are one small edit away from being untrue, and the
    edit that breaks them will not look like it touches a public response. A
    schema that never carried the field cannot start carrying it by accident.

    `decision_reason` IS here, and deliberately: it is the rule engine's verdict
    written FOR the applicant, and the result card is the only place it is ever
    shown ("Domain matched — auto-approved", "Held for review").
    """

    id: str
    name: str
    email: str
    usn: str | None
    degree_level: str
    status: str
    cohort_id: str | None
    decision_reason: str | None
    created_at: datetime
    college_id: str | None = None
    department_id: str | None = None
    course_id: str | None = None
    specialization_id: str | None = None
    second_specialization_id: str | None = None
    requested_cohort_id: str | None = None
    college_name: str | None = None
    department_name: str | None = None
    course_name: str | None = None
    specialization_name: str | None = None
    second_specialization_name: str | None = None
    requested_batch: str | None = None
    documents: list[str] = []


def _doc_kinds(db: Session, registration_ids: Sequence[str]) -> dict[str, list[str]]:
    """Which document kinds each application holds - one query for a whole list,
    so the queue does not fire a SELECT per row."""
    if not registration_ids:
        return {}
    out: dict[str, list[str]] = {}
    for reg_id, kind in db.execute(
        select(RegistrationDocument.registration_id, RegistrationDocument.kind).where(
            RegistrationDocument.registration_id.in_(list(registration_ids))
        )
    ).all():
        out.setdefault(reg_id, []).append(kind)
    return out


#: URL segment -> stored kind -> the mime types that kind may be. The store
#: sniffs magic bytes and hands back the real mime; this table is what turns a
#: PNG posted as a "cv" into a 415 instead of a CV nobody can open.
_DOCUMENT_ROUTES: dict[str, tuple[str, frozenset[str], str]] = {
    "cv": (DOCUMENT_KIND_CV, frozenset({"application/pdf"}), "a PDF"),
    "photo": (DOCUMENT_KIND_PHOTO, frozenset({"image/png", "image/jpeg"}), "a PNG or JPG"),
}

#: The title the application's CV carries once it is the student's upload.
#: The office's Student 360 page reads it to tell that CV apart from one the
#: student uploaded later (`admin_student_360.Document360Out.from_application`).
APPLICATION_CV_TITLE = "CV from application"

#: Where a moved document lands in the student's own uploads on approval.
_DOCUMENT_TO_UPLOAD_KIND: dict[str, tuple[UploadKind, str]] = {
    DOCUMENT_KIND_CV: (UploadKind.RESUME, APPLICATION_CV_TITLE),
    DOCUMENT_KIND_PHOTO: (UploadKind.PROFILE_PHOTO, "Photo from application"),
}


def _move_documents_to_uploads(db: Session, reg: Registration, student: Student) -> int:
    """On APPROVE: the application's files become the student's first uploads.

    A MOVE, not a copy - the Upload row takes the same stored_name, so the bytes
    are never duplicated and the RegistrationDocument row is simply deleted
    (rows only: the file now belongs to the Upload). `uploads.stored_name` is
    unique, and so is ours, so a name can never end up owned twice.

    THE MANIFEST ROW MOVES WITH THE FILE. It was written when an applicant with
    no account posted the bytes, so it says REGISTRATION_DOCUMENT and owns
    nobody; the file is a named student's resume from this line onwards, and a
    manifest that still says otherwise is an index entry that cannot answer the
    only question it exists for once the live row is gone. `reattribute`
    explains why that is an UPDATE rather than a release and a second record -
    the short version is that `stored_name` is unique, so there is no second row
    to write, and releasing would claim the student's resume was deleted on the
    day they were admitted.
    """
    docs = db.scalars(
        select(RegistrationDocument).where(RegistrationDocument.registration_id == reg.id)
    ).all()
    for d in docs:
        kind, title = _DOCUMENT_TO_UPLOAD_KIND.get(d.kind, (UploadKind.DOCUMENT, "From application"))
        db.add(
            Upload(
                student_id=student.id,
                kind=kind,
                cert_code=None,
                title=title,
                original_name=d.original_name,
                stored_name=d.stored_name,
                mime_type=d.mime_type,
                size_bytes=d.size_bytes,
            )
        )
        # `students.id`, matching every other STUDENT_UPLOAD row the manifest
        # holds (routers/student.py writes the same column), so "everything this
        # student ever had" finds the file it did not find a moment ago.
        reattribute(
            db,
            d.stored_name,
            kind=DocumentOwnerKind.STUDENT_UPLOAD,
            owner_id=student.id,
            reason="application approved; document moved to the student's uploads",
        )
        db.delete(d)
    db.flush()
    return len(docs)


_CLAIM_KEYS = (
    "college_id",
    "department_id",
    "course_id",
    "specialization_id",
    "second_specialization_id",
    "requested_cohort_id",
)
_CLAIM_NOUN = {
    "college_id": "college",
    "department_id": "department",
    "course_id": "course",
    "specialization_id": "specialization",
    "second_specialization_id": "second specialization",
    "requested_cohort_id": "batch",
}


def _claim_names(db: Session, rows: Sequence[Registration]) -> dict[str, dict[str, str | None]]:
    """Resolved names for every application's claim - five IN queries for a
    whole page, never one per row."""
    def ids(attr: str) -> list[str]:
        return list({getattr(r, attr) for r in rows if getattr(r, attr)})

    colleges = {c.id: c.name for c in db.scalars(select(College).where(College.id.in_(ids("college_id")))).all()} if ids("college_id") else {}
    departments = {d.id: d.name for d in db.scalars(select(Department).where(Department.id.in_(ids("department_id")))).all()} if ids("department_id") else {}
    courses = {c.id: c.name for c in db.scalars(select(AcademicCourse).where(AcademicCourse.id.in_(ids("course_id")))).all()} if ids("course_id") else {}
    # Both ticks of a dual specialization come out of the ONE query: the two
    # columns name rows of the same table.
    spec_ids = list({*ids("specialization_id"), *ids("second_specialization_id")})
    specs = {x.id: x.name for x in db.scalars(select(AcademicSpecialization).where(AcademicSpecialization.id.in_(spec_ids))).all()} if spec_ids else {}
    # THE BATCH'S OWN course and specialization, not the application's. The two
    # dicts above are keyed on what the APPLICANT named, and a reviewer reading
    # "Approving seats them in ..." needs the words for the batch they will
    # actually land in — the check right beside it exists to catch the case
    # where those two disagree. One query, as before: the spine comes down the
    # links (`batch_labels.compose`), never out of `cohorts.name`.
    batches = {
        b.id: batch_labels.compose(course_name, spec_name, b.name, b.batch_label)
        for b, course_name, spec_name in db.execute(
            select(Cohort, AcademicCourse.name, AcademicSpecialization.name)
            .outerjoin(AcademicCourse, AcademicCourse.id == Cohort.course_id)
            .outerjoin(
                AcademicSpecialization,
                AcademicSpecialization.id == Cohort.specialization_id,
            )
            .where(Cohort.id.in_(ids("requested_cohort_id")))
        ).all()
    } if ids("requested_cohort_id") else {}
    return {
        r.id: {
            "college_name": colleges.get(r.college_id) if r.college_id else None,
            "department_name": departments.get(r.department_id) if r.department_id else None,
            "course_name": courses.get(r.course_id) if r.course_id else None,
            "specialization_name": specs.get(r.specialization_id) if r.specialization_id else None,
            "second_specialization_name": (
                specs.get(r.second_specialization_id) if r.second_specialization_id else None
            ),
            "requested_batch": batches.get(r.requested_cohort_id) if r.requested_cohort_id else None,
        }
        for r in rows
    }


# --- the domain verdict: ONE writer for GUARD 1 and for the queue's check -----
#
# GUARD 1 in `_provision_student` refuses an approval whose address is not on the
# college's list. The review queue draws the same verdict as a check BEFORE the
# reviewer clicks anything (B11.1). That is one question asked at two moments,
# and written twice the two disagree the first time `provisionable_domains_for`'s
# fallback rule changes — with the disagreement surfacing as a GREEN CHECK on a
# row that Approve then refuses with a 422, which is the worst way to learn about
# it. So there is one function and GUARD 1 CALLS IT.


class DomainVerdict(NamedTuple):
    """Whether this application's address is one the college will admit.

    `allowed` travels with the answer because both callers need it: the 422 names
    the domains so the reviewer knows what the address should have been, and the
    check's detail line says the same thing on screen.
    """

    ok: bool
    domain: str
    allowed: frozenset[str]


def college_for_registration(db: Session, reg: Registration) -> str | None:
    """Whose fence applies to this application.

    The college the applicant NAMED on the form, else the one their rule-assigned
    batch sits under. None is the pre-spine case — an application that named no
    college and was seated in no batch — and `provisionable_domains_for` answers
    that with the deployment's list, which is what every address was fenced by
    before colleges had domains of their own.
    """
    return reg.college_id or college_id_for_cohort(db, reg.cohort_id)


def domain_verdict(
    db: Session, reg: Registration, *, allowed: frozenset[str] | None = None
) -> DomainVerdict:
    """GUARD 1's question, asked once.

    `allowed` is an escape hatch for a LIST: `provisionable_domains_for` is one
    `db.get(College, ...)` and `college_for_registration` is a three-table join,
    so a queue that called this per row would fire two queries per application.
    `pending` resolves the distinct colleges of a page once and hands the answer
    in. Passing nothing resolves it here, which is what every single-row caller
    (GUARD 1 included) does.
    """
    domain = domain_of((reg.email or "").strip().lower())
    if allowed is None:
        allowed = provisionable_domains_for(db, college_for_registration(db, reg))
    return DomainVerdict(ok=bool(domain) and domain in allowed, domain=domain, allowed=allowed)


def _checks_for(
    db: Session,
    rows: Sequence[Registration],
    *,
    docs: dict[str, list[str]] | None = None,
    names: dict[str, dict[str, str | None]] | None = None,
    reveal_holders: bool = False,
) -> dict[str, list[CheckOut]]:
    """The pre-decision checklist for a whole page, in a handful of queries.

    `reveal_holders` names the student who already holds an application's USN
    (one more query, only when asked). The caller passes True only for a
    programme-wide reach: a scoped reviewer is told the USN is taken and not
    by whom, since that student may be outside their grant.

    BATCHED BY `IN`, NEVER PER ROW — `_doc_kinds` and `_claim_names` above are the
    house pattern and this follows it, because the queue has no LIMIT and the two
    lookups behind the domain verdict (`college_ids_for_cohorts`, a three-table
    join, and `provisionable_domains_for`, a `db.get`) would otherwise fire twice
    per application on a two-hundred-row queue.

    Four lookups, whatever the page size: the colleges of the batches nobody
    named a college for, the accounts already holding these addresses, the
    students holding those accounts or these USNs, and the rules that routed
    them. `provisionable_domains_for` is then called once per DISTINCT college,
    which keeps `domain_verdict` the one writer of the fence.
    """
    if not rows:
        return {}

    # -- what was attached (the queue passes its own lookup; one query else) --
    if docs is None:
        docs = _doc_kinds(db, [r.id for r in rows])
    # -- the claim by name (same rule: the queue already resolved these) ------
    if names is None:
        names = _claim_names(db, rows)

    # -- the college fence, per distinct college ------------------------------
    cohort_colleges = college_ids_for_cohorts(
        db, [r.cohort_id for r in rows if not r.college_id and r.cohort_id]
    )
    # `college_for_registration`'s rule, applied from the batched lookup: the
    # college the applicant NAMED, else the one their rule-assigned batch sits
    # under, else None (the deployment's list).
    college_by_reg: dict[str, str | None] = {}
    for r in rows:
        if r.college_id:
            college_by_reg[r.id] = r.college_id
        elif r.cohort_id:
            college_by_reg[r.id] = cohort_colleges.get(r.cohort_id)
        else:
            college_by_reg[r.id] = None
    allowed_by_college: dict[str | None, frozenset[str]] = {
        cid: provisionable_domains_for(db, cid) for cid in set(college_by_reg.values())
    }

    # -- the accounts these addresses already hold ----------------------------
    emails = {(r.email or "").strip().lower() for r in rows if (r.email or "").strip()}
    users_by_email: dict[str, User] = {}
    if emails:
        for u in db.scalars(select(User).where(func.lower(User.email).in_(list(emails)))).all():
            users_by_email[(u.email or "").lower()] = u

    # -- the students behind those accounts, and whoever holds these USNs -----
    usns = {u for u in (normalise_usn(r.usn) for r in rows) if u}
    user_ids = {u.id for u in users_by_email.values()}
    student_by_user: dict[str, Student] = {}
    student_by_usn: dict[str, Student] = {}
    clauses = []
    if user_ids:
        clauses.append(Student.user_id.in_(list(user_ids)))
    if usns:
        # Upper case on both sides (app/usn.py): a lowercase USN written before
        # 2026-10-16 is still the same number.
        clauses.append(func.upper(Student.usn).in_(list(usns)))
    if clauses:
        for st in db.scalars(select(Student).where(or_(*clauses))).all():
            student_by_user[st.user_id] = st
            if st.usn:
                student_by_usn[st.usn.upper()] = st

    # -- who those USN holders are, for a reviewer allowed to be told --------
    holder_users: dict[str, User] = {}
    if reveal_holders and student_by_usn:
        holder_ids = {st.user_id for st in student_by_usn.values()}
        for u in db.scalars(select(User).where(User.id.in_(list(holder_ids)))).all():
            holder_users[u.id] = u

    # -- the rejected applications these addresses made before ----------------
    # Newest decision first, so `prior[0]` is the one the reviewer should read.
    # Only REJECTED rows can share an address with a live one (the partial
    # unique index says so), so this is the whole history by construction.
    prior_by_email: dict[str, list[Registration]] = {}
    if emails:
        earlier = db.scalars(
            select(Registration)
            .where(
                Registration.status == RegistrationStatus.REJECTED,
                func.lower(Registration.email).in_(list(emails)),
            )
            .order_by(Registration.reviewed_at.desc().nullslast(), Registration.created_at.desc())
        ).all()
        for p in earlier:
            prior_by_email.setdefault((p.email or "").lower(), []).append(p)

    # -- the rules that routed them -------------------------------------------
    rule_ids = {r.matched_rule_id for r in rows if r.matched_rule_id}
    rules_by_id: dict[str, RegistrationRule] = (
        {
            rule.id: rule
            for rule in db.scalars(
                select(RegistrationRule).where(RegistrationRule.id.in_(list(rule_ids)))
            ).all()
        }
        if rule_ids
        else {}
    )

    return {
        r.id: _checks_for_one(
            r,
            verdict=domain_verdict(db, r, allowed=allowed_by_college[college_by_reg[r.id]]),
            account=users_by_email.get((r.email or "").strip().lower()),
            student_by_user=student_by_user,
            student_by_usn=student_by_usn,
            holder_users=holder_users,
            rule=rules_by_id.get(r.matched_rule_id) if r.matched_rule_id else None,
            # Not itself: a row in the Rejected tab is never in `rows` here
            # (DECIDABLE_STATUSES), but the exclusion costs nothing and keeps
            # the helper honest if that ever changes.
            prior=[p for p in prior_by_email.get((r.email or "").strip().lower(), []) if p.id != r.id],
            docs=docs.get(r.id, ()),
            specializations=_specialization_names(r, names.get(r.id)),
        )
        for r in rows
    }


def _specialization_names(r: Registration, names: dict[str, str | None] | None) -> list[str]:
    """The ticked specializations by name, in the order the row holds them.

    A pointer whose row has since been ARCHIVED resolves to no name and is left
    out rather than printed as None: the claim is still on the row, but there
    is no word to put on the screen for it.
    """
    names = names or {}
    out: list[str] = []
    for column, key in (
        ("specialization_id", "specialization_name"),
        ("second_specialization_id", "second_specialization_name"),
    ):
        name = names.get(key)
        if getattr(r, column) and name:
            out.append(name)
    return out


def _checks_for_one(
    r: Registration,
    *,
    verdict: DomainVerdict,
    account: User | None,
    student_by_user: dict[str, Student],
    student_by_usn: dict[str, Student],
    rule: RegistrationRule | None,
    holder_users: dict[str, User] | None = None,
    prior: Sequence[Registration] = (),
    docs: Sequence[str] = (),
    specializations: Sequence[str] = (),
) -> list[CheckOut]:
    """One application's checklist, from facts the caller already resolved.

    PURE, and takes no Session on purpose: every lookup it could make is one the
    batched caller above has already made for the whole page, and a helper that
    can reach the database is a helper somebody calls in a loop.

    `prior` is this address's REJECTED applications, newest decision first.
    `docs` is the kinds attached ("CV", "PHOTO"), from the queue's own lookup.
    `specializations` is the ticked specializations BY NAME, in the row's
    order; two of them is a dual specialization and earns a line.
    `holder_users` is the accounts behind `student_by_usn`, keyed by user id,
    and is filled only for a reviewer who may be told who holds a USN.
    """
    checks: list[CheckOut] = []
    reason = (r.decision_reason or "").strip()

    # ---- applied before? ----------------------------------------------------
    # First on the list when it applies, because it changes how every line
    # below should be read: this is the SECOND (or third) time the office is
    # looking at this person, and what it said last time is the context.
    if prior:
        last = prior[0]
        when = last.reviewed_at.strftime("%d %b %Y") if last.reviewed_at else "an earlier date"
        note = (last.review_note or "").strip()
        times = "once" if len(prior) == 1 else f"{len(prior)} times"
        checks.append(
            CheckOut(
                key=CHECK_PRIOR_APPLICATIONS,
                status=CHECK_WARN,
                label=f"Applied before - rejected {times}, last on {when}",
                detail=(
                    (f"The reason given then: \"{note}\" " if note else "No reason was recorded then. ")
                    + "This is a fresh application; the earlier one is on the Rejected tab."
                ),
            )
        )

    # ---- the rule engine's own verdict --------------------------------------
    if rule is None:
        checks.append(
            CheckOut(
                key=CHECK_RULE,
                status=CHECK_WARN,
                label="No seating rule matched" if r.matched_rule_id is None else "The rule that routed this is gone",
                detail=reason or "Nothing routed this application, so the batch is a human decision.",
            )
        )
    elif rule.auto_approve and r.status is RegistrationStatus.PENDING_REVIEW:
        # `_apply_rule` catches a GUARD refusal and queues the application with
        # the refusal as its reason rather than dropping or forcing it. So this
        # row is here BECAUSE a guard said no — the checks below say whether it
        # still would.
        checks.append(
            CheckOut(
                key=CHECK_RULE,
                status=CHECK_WARN,
                label=f"Rule '{rule.name}' would auto-approve, but a guard refused it",
                detail=reason or "Auto-approval was refused at submit time and the row was queued.",
            )
        )
    else:
        checks.append(
            CheckOut(
                key=CHECK_RULE,
                status=CHECK_OK,
                label=f"Routed by rule '{rule.name}'",
                detail=reason or "The rule engine matched this application when it was submitted.",
            )
        )

    # ---- GUARD 1: the college's fence ---------------------------------------
    named = ", ".join(sorted(verdict.allowed)) or "no domain at all"
    if verdict.ok:
        checks.append(
            CheckOut(
                key=CHECK_DOMAIN,
                status=CHECK_OK,
                label=f"{verdict.domain} is a college domain",
                detail="Approving mints the sign-in account on this address.",
            )
        )
    else:
        checks.append(
            CheckOut(
                key=CHECK_DOMAIN,
                status=CHECK_BLOCKED,
                label=(
                    f"{verdict.domain} is not a college domain"
                    if verdict.domain
                    else "That address has no domain"
                ),
                detail=(
                    f"Approve refuses this (422). Only {named} may become a sign-in "
                    "account here; a college's own list is set on Colleges."
                ),
            )
        )

    # ---- GUARD 2: whose account this address already is ---------------------
    email = (r.email or "").strip().lower()
    if account is None:
        checks.append(
            CheckOut(
                key=CHECK_DUPLICATE_ACCOUNT,
                status=CHECK_OK,
                label="No account on this address",
                detail="Approving creates one and emails the onboarding link.",
            )
        )
    elif account.role is Role.STUDENT:
        # THE MIDDLE VERDICT. Approve REUSES this account — it is the ordinary
        # re-application path, and the state `python -m app.seed_roster` leaves
        # behind for every enrolled student. Rendering it as a blocker would send
        # reviewers to reject applications the queue handles correctly.
        checks.append(
            CheckOut(
                key=CHECK_DUPLICATE_ACCOUNT,
                status=CHECK_WARN,
                label="A student account already exists on this address",
                detail="Approving reuses it rather than creating a second — no duplicate is made.",
            )
        )
    else:
        checks.append(
            CheckOut(
                key=CHECK_DUPLICATE_ACCOUNT,
                status=CHECK_BLOCKED,
                label=f"{email} belongs to a {account.role.value} account",
                detail=(
                    "Approve refuses this (409). Attaching a student record would widen "
                    "that account's reach; reject it, or correct the address first."
                ),
            )
        )

    # ---- GUARD 3: the USN is the roster key and it is unique ----------------
    #
    # THIS FOLLOWS `_provision_student`'S CONTROL FLOW, NOT JUST ITS RULE. GUARD 3
    # runs only where a Student row is about to be INSERTED — an applicant who
    # already has one keeps it, USN and all, and the number typed on the
    # application is never written. So "somebody else holds this USN" is a
    # BLOCKER for a new record and merely a remark for an existing one, and a
    # check that skipped that branch would report a refusal on an approval that
    # in fact succeeds. That is the same defect as a green check on a row Approve
    # refuses, in the other direction.
    usn = normalise_usn(r.usn) or ""
    own_student = student_by_user.get(account.id) if account is not None else None
    holder = student_by_usn.get(usn) if usn else None
    if not usn:
        checks.append(
            CheckOut(
                key=CHECK_USN_UNIQUE,
                status=CHECK_WARN,
                label="No USN on this application",
                detail="Approving seats them without one; Students & batches can add it later.",
            )
        )
    elif own_student is not None and (own_student.usn or "").upper() == usn:
        checks.append(
            CheckOut(
                key=CHECK_USN_UNIQUE,
                status=CHECK_OK,
                label=f"USN {usn} is already this applicant's own",
                detail="Approving reuses their existing record and leaves the USN alone.",
            )
        )
    elif own_student is not None:
        checks.append(
            CheckOut(
                key=CHECK_USN_UNIQUE,
                status=CHECK_WARN,
                label=f"Their record already reads {own_student.usn or 'no USN'}",
                detail=(
                    f"Approving reuses that record, so {usn} is NOT copied onto it. Change "
                    "the USN on Students & batches if this one is the right one."
                ),
            )
        )
    elif holder is not None:
        # WHO HOLDS IT IS THE WHOLE DECISION (2026-10-16). A mistyped USN, the
        # same person applying under a second address, and another record
        # carrying the wrong number all looked identical here, and the line
        # sent the reviewer to "correct the USN on this application" when no
        # screen could. The name, the address and a link to the record let the
        # reviewer tell them apart; `PATCH /register/{id}` is the correction.
        # Never put the holder in `_provision_student`'s 409: `_apply_rule`
        # writes that sentence into the public `decision_reason`.
        owner = (holder_users or {}).get(holder.user_id)
        if owner is not None:
            removed = " - a removed account" if owner.deleted_at is not None else ""
            checks.append(
                CheckOut(
                    key=CHECK_USN_UNIQUE,
                    status=CHECK_BLOCKED,
                    label=f"USN {usn} already belongs to {owner.name} ({owner.email}){removed}",
                    detail=(
                        "Approve refuses this (409). If the USN here is mistyped, use Correct "
                        "USN. If that record is this same person, reject this as a duplicate. "
                        "If that record's USN is wrong, fix it on Students first."
                    ),
                    student_id=holder.id,
                )
            )
        else:
            checks.append(
                CheckOut(
                    key=CHECK_USN_UNIQUE,
                    status=CHECK_BLOCKED,
                    label=f"USN {usn} already belongs to another student",
                    detail=(
                        "Approve refuses this (409). If the USN here is mistyped, use Correct "
                        "USN; otherwise reject this as a duplicate."
                    ),
                )
            )
    else:
        checks.append(
            CheckOut(
                key=CHECK_USN_UNIQUE,
                status=CHECK_OK,
                label=f"USN {usn} is free",
                detail="No other student on the roster holds it.",
            )
        )

    # ---- the matched rule's own USN condition, re-run -----------------------
    # Only where there is a pattern to check against. Free: `_usn_matcher` is
    # lru_cached on the pattern string, so this is a dictionary hit and a
    # `search` over a string RegisterIn caps at 32 characters.
    if rule is not None and rule.usn_pattern:
        matcher = _usn_matcher(rule.usn_pattern)
        if matcher is None:
            checks.append(
                CheckOut(
                    key=CHECK_USN_PATTERN,
                    status=CHECK_WARN,
                    label=f"Rule '{rule.name}' has a USN pattern nothing will run",
                    detail="It is refused as a backtracking risk, so the rule is skipped at match time.",
                )
            )
        elif usn and len(usn) <= MAX_USN_MATCH_LENGTH and matcher.search(usn):
            checks.append(
                CheckOut(
                    key=CHECK_USN_PATTERN,
                    status=CHECK_OK,
                    label=f"The USN matches rule '{rule.name}'",
                    detail=f"It still satisfies the rule's pattern {rule.usn_pattern}.",
                )
            )
        else:
            checks.append(
                CheckOut(
                    key=CHECK_USN_PATTERN,
                    status=CHECK_WARN,
                    label=f"The USN no longer matches rule '{rule.name}'",
                    detail=(
                        "The rule routed this application and has been edited since, or the "
                        "USN has. Approving still seats them in the rule's batch."
                    ),
                )
            )

    # ---- the CV and the photo ------------------------------------------------
    # Both are required by the form (2026-09-16). A row missing one arrived
    # through something other than a completed form - an upload that failed
    # after the 201, or a direct POST - and the reviewer decides what that
    # means; the check only makes sure they decide it knowingly.
    missing = [
        noun
        for kind, noun in ((DOCUMENT_KIND_CV, "CV"), (DOCUMENT_KIND_PHOTO, "photo"))
        if kind not in docs
    ]
    if missing:
        checks.append(
            CheckOut(
                key=CHECK_DOCUMENTS,
                status=CHECK_WARN,
                label=(" and ".join(missing) + " missing") if len(missing) > 1 else f"No {missing[0]} attached",
                detail=(
                    "The form requires a CV (PDF) and a photo (PNG or JPG). Hold the "
                    "application and ask the applicant for the file, or approve without it "
                    "and they start with no " + " or ".join(missing) + " on their uploads."
                ),
            )
        )
    else:
        checks.append(
            CheckOut(
                key=CHECK_DOCUMENTS,
                status=CHECK_OK,
                label="CV and photo attached",
                detail="Both files came with the application; Approve moves them onto the student's uploads.",
            )
        )

    # ---- a dual specialization -----------------------------------------------
    # Only where there are two: one is the ordinary case and earns no line, and
    # a line reading "one specialization" on every row is noise in a panel
    # meant to be read in two seconds.
    if len(specializations) > 1:
        checks.append(
            CheckOut(
                key=CHECK_DUAL_SPECIALIZATION,
                status=CHECK_WARN,
                label="Opted for a dual specialization",
                detail=(
                    "They ticked " + " and ".join(specializations) + ". A batch hangs on one "
                    "specialization at most, so Approve seats them by the batch and keeps "
                    "the other as their second specialization."
                ),
            )
        )

    return checks


def _out_one(db: Session, r: Registration) -> RegistrationOut:
    """One row with everything the client needs: documents and claim names."""
    return _out(r, _doc_kinds(db, [r.id]).get(r.id, ()), _claim_names(db, [r]).get(r.id))


def _public_out_one(db: Session, r: Registration) -> PublicRegistrationOut:
    """The applicant's own view, built by NARROWING the full one.

    Reusing `_out` rather than assembling a second dict keeps one writer for the
    claim names and the document kinds; `model_dump` then drops whatever
    `PublicRegistrationOut` does not declare. A field added to the staff model
    tomorrow is therefore private by default, which is the direction the mistake
    should fall in.
    """
    full = _out_one(db, r)
    fields = PublicRegistrationOut.model_fields.keys()
    return PublicRegistrationOut(**{k: v for k, v in full.model_dump().items() if k in fields})


def _out(
    r: Registration,
    docs: Sequence[str] = (),
    names: dict[str, str | None] | None = None,
    checks: list[CheckOut] | None = None,
) -> RegistrationOut:
    names = names or {}
    return RegistrationOut(
        documents=sorted(docs),
        checks=checks,
        college_id=r.college_id,
        department_id=r.department_id,
        course_id=r.course_id,
        specialization_id=r.specialization_id,
        second_specialization_id=r.second_specialization_id,
        requested_cohort_id=r.requested_cohort_id,
        college_name=names.get("college_name"),
        department_name=names.get("department_name"),
        course_name=names.get("course_name"),
        specialization_name=names.get("specialization_name"),
        second_specialization_name=names.get("second_specialization_name"),
        requested_batch=names.get("requested_batch"),
        id=r.id,
        name=r.name,
        email=r.email,
        usn=r.usn,
        phone=r.phone,
        personal_email=r.personal_email,
        linkedin_url=r.linkedin_url,
        degree_level=r.degree_level.value,
        status=r.status.value,
        cohort_id=r.cohort_id,
        matched_rule_id=r.matched_rule_id,
        decision_reason=r.decision_reason,
        reviewed_by_id=r.reviewed_by_id,
        reviewed_at=r.reviewed_at,
        review_note=r.review_note,
        approved_student_id=r.approved_student_id,
        created_at=r.created_at,
        hold_note=r.hold_note,
        held_by_id=r.held_by_id,
        held_at=r.held_at,
    )


class PublicSpecializationOut(BaseModel):
    id: str
    code: str
    name: str


class PublicCourseOut(BaseModel):
    id: str
    code: str
    name: str
    specializations: list[PublicSpecializationOut]


class PublicBatchOut(BaseModel):
    id: str
    code: str
    name: str
    batch_label: str
    department_id: str | None
    course_id: str | None
    specialization_id: str | None
    #: The spine, resolved from the links above. A batch is a YEAR (`name`);
    #: which course and which specialization it belongs to are these pointers,
    #: and every picker that offers batches has to show them or "2026-28"
    #: appears four times with nothing to tell them apart.
    course_name: str | None
    specialization_name: str | None
    #: The spine and the year put together by the one rule
    #: (`batch_labels.compose`): "General MBA - Finance · 2026-28". Served
    #: rather than composed per screen — six templates were concatenating
    #: `name · batch_label` themselves, which printed the course twice back
    #: when the course was manufactured into the name.
    display_label: str
    degree_level: str
    #: Still running (end date ahead). The form lists current batches first and
    #: greys the rest; it does not hide them - a late applicant to a batch that
    #: ended last month is the Main Admin's call, not the form's.
    current: bool


class PublicDepartmentOut(BaseModel):
    id: str
    code: str
    name: str
    courses: list[PublicCourseOut]
    batches: list[PublicBatchOut]


class PublicCollegeOut(BaseModel):
    id: str
    code: str
    name: str
    departments: list[PublicDepartmentOut]


class PublicLevelOut(BaseModel):
    key: str
    label: str
    required: bool


class PublicHierarchyOut(BaseModel):
    levels: list[PublicLevelOut]
    colleges: list[PublicCollegeOut]
    #: How many specializations the form's checklist may take -
    #: MAX_SPECIALIZATIONS_PER_APPLICATION, served so the form and the schema
    #: refuse at the same number.
    max_specializations: int


@router.get("/hierarchy", response_model=PublicHierarchyOut)
def hierarchy(db: Session = Depends(get_db)) -> PublicHierarchyOut:
    """What the register form's pickers offer - the hierarchy the admin built.

    PUBLIC, like the form. It carries institutional STRUCTURE only: ids, codes
    and names of ACTIVE colleges, departments, courses and specializations, and
    every batch's label - never a student, a count of students, a head of
    department or a contact. Rule 1 is about student data; a college's own
    prospectus is not that. `levels` says which pickers the form must require:
    College and Department always (every batch sits under a department), then
    Course and Specialization per HIERARCHY_LEVELS - the same one-line switch
    the admin console reads - and Batch never, because an applicant may not
    know theirs and the Main Admin can seat them.
    """
    now = datetime.now(timezone.utc)
    colleges = db.scalars(
        select(College).where(College.status == STATUS_ACTIVE).order_by(College.name)
    ).all()
    departments = db.scalars(
        select(Department).where(Department.status == STATUS_ACTIVE).order_by(Department.name)
    ).all()
    courses = db.scalars(
        select(AcademicCourse).where(AcademicCourse.status == STATUS_ACTIVE).order_by(AcademicCourse.name)
    ).all()
    specs = db.scalars(
        select(AcademicSpecialization)
        .where(AcademicSpecialization.status == STATUS_ACTIVE)
        .order_by(AcademicSpecialization.name)
    ).all()
    batches = db.scalars(select(Cohort).order_by(Cohort.start_date.desc(), Cohort.name)).all()

    specs_by_course: dict[str, list[PublicSpecializationOut]] = {}
    for sp in specs:
        specs_by_course.setdefault(sp.course_id, []).append(
            PublicSpecializationOut(id=sp.id, code=sp.code, name=sp.name)
        )
    courses_by_dept: dict[str, list[PublicCourseOut]] = {}
    for co in courses:
        courses_by_dept.setdefault(co.department_id, []).append(
            PublicCourseOut(id=co.id, code=co.code, name=co.name, specializations=specs_by_course.get(co.id, []))
        )
    # The spine a batch hangs off, by id. Both lists are already loaded above
    # and are ACTIVE-only, so a batch under an archived course resolves to None
    # and reads as the year alone — which is honest: the picker cannot name a
    # course this hierarchy no longer serves.
    course_names = {co.id: co.name for co in courses}
    spec_names = {sp.id: sp.name for sp in specs}
    batches_by_dept: dict[str, list[PublicBatchOut]] = {}
    for b in batches:
        if b.department_id is None:
            continue  # unseated in the hierarchy; the admin console lists these to fix
        course_name = course_names.get(b.course_id) if b.course_id else None
        spec_name = spec_names.get(b.specialization_id) if b.specialization_id else None
        batches_by_dept.setdefault(b.department_id, []).append(
            PublicBatchOut(
                id=b.id, code=b.code, name=b.name, batch_label=b.batch_label,
                department_id=b.department_id, course_id=b.course_id,
                specialization_id=b.specialization_id,
                course_name=course_name, specialization_name=spec_name,
                display_label=batch_labels.compose(
                    course_name, spec_name, b.name, b.batch_label
                ),
                degree_level=b.degree_level.value, current=b.end_date >= now,
            )
        )
    depts_by_college: dict[str, list[PublicDepartmentOut]] = {}
    for d in departments:
        depts_by_college.setdefault(d.college_id, []).append(
            PublicDepartmentOut(
                id=d.id, code=d.code, name=d.name,
                courses=courses_by_dept.get(d.id, []), batches=batches_by_dept.get(d.id, []),
            )
        )
    levels = [
        PublicLevelOut(key="college", label="College", required=True),
        PublicLevelOut(key="department", label="Department", required=True),
        *[PublicLevelOut(key=lvl.key, label=lvl.label, required=lvl.required) for lvl in HIERARCHY_LEVELS],
        PublicLevelOut(key="batch", label="Batch", required=False),
    ]
    return PublicHierarchyOut(
        levels=levels,
        colleges=[
            PublicCollegeOut(id=c.id, code=c.code, name=c.name, departments=depts_by_college.get(c.id, []))
            for c in colleges
        ],
        max_specializations=MAX_SPECIALIZATIONS_PER_APPLICATION,
    )


def _resolve_claim(db: Session, body: RegisterIn) -> dict[str, str | None]:
    """The applicant's claim, walked UP from the deepest level they named.

    The same discipline as routers/admin.py::_resolve_ancestry, for the same
    reason: five columns that can be set independently are five chances to
    disagree, and the disagreement surfaces as the Main Admin seating a student in
    a department their batch is not in. So the batch pins its specialization,
    course and department; a specialization pins its course; a course its
    department; a department its college - and any level the applicant ALSO
    named that differs from what was derived is a 422 naming the deeper choice,
    never a silent pick. An id that does not exist is a 422 too: this is a
    public form, and "unknown college" is input validation, not a missing page.

    THE SPECIALIZATION IS A LIST OF AT MOST TWO (2026-09-22), already merged
    and capped by `RegisterIn`. The first tick goes into `specialization_id`
    and settles the course exactly as one pick always has; the other into
    `second_specialization_id`, which must sit under THE SAME course - two
    specializations from two courses is not a dual specialization, it is two
    applications. Where the requested batch pins a specialization, that one is
    moved to the front so the column the batch has always been checked against
    still agrees with it; a batch pinning a specialization the applicant did
    not tick at all is the same contradiction it was when the box was a
    <select>, refused by the same sentence.
    """
    chain: dict[str, str | None] = {k: None for k in _CLAIM_KEYS}

    def settle(key: str, value: str | None, because: str) -> None:
        if value is None:
            return
        if chain[key] is not None and chain[key] != value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"The {_CLAIM_NOUN[key]} you chose contradicts the {because} you chose. "
                    f"Pick a {_CLAIM_NOUN[key]} under it, or clear the {because}."
                ),
            )
        chain[key] = value

    def load(model, ident: str | None, noun: str):
        if ident is None:
            return None
        row = db.get(model, ident)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"That {noun} does not exist. Reload the form and choose again.",
            )
        # The hierarchy this form is drawn from lists ACTIVE colleges,
        # departments, courses and specializations only; an id for an
        # archived one can only come from a stale page or a hand-made request,
        # and approving it would seat a student somewhere the office has
        # closed. A batch is not judged here: the hierarchy serves every batch
        # whatever its lifecycle status, and the form and this check must agree.
        if model is not Cohort and getattr(row, "status", STATUS_ACTIVE) != STATUS_ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"That {noun} is not taking applications. Reload the form and choose again.",
            )
        return row

    batch = load(Cohort, body.requested_cohort_id, "batch")
    if batch is not None:
        chain["requested_cohort_id"] = batch.id
        settle("specialization_id", batch.specialization_id, "batch")
        settle("course_id", batch.course_id, "batch")
        settle("department_id", batch.department_id, "batch")

    picks = list(body.specialization_ids)
    pinned = chain["specialization_id"]
    if pinned is not None and pinned in picks:
        picks = [pinned, *[p for p in picks if p != pinned]]
    if picks:
        settle("specialization_id", picks[0], "batch")
        if len(picks) > 1:
            chain["second_specialization_id"] = picks[1]
    spec = load(AcademicSpecialization, chain["specialization_id"], "specialization")
    if spec is not None:
        settle("course_id", spec.course_id, "specialization")
    second = load(AcademicSpecialization, chain["second_specialization_id"], "specialization")
    if second is not None and second.course_id != chain["course_id"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "The two specializations you ticked belong to different courses. "
                "A dual specialization is two streams of one course - tick both under it."
            ),
        )

    settle("course_id", body.course_id, "specialization or batch")
    course = load(AcademicCourse, chain["course_id"], "course")
    if course is not None:
        settle("department_id", course.department_id, "course")

    settle("department_id", body.department_id, "course or batch")
    department = load(Department, chain["department_id"], "department")
    if department is not None:
        settle("college_id", department.college_id, "department")

    settle("college_id", body.college_id, "department")
    load(College, chain["college_id"], "college")
    return chain


def log_form_refusal(errors: Sequence[dict]) -> None:
    """Log WHICH boxes a schema refusal named, and nothing they held.

    Called by app/main.py's RequestValidationError handler for POST /register
    only. Each error contributes `field:type` — the last string in its `loc`
    and pydantic's error type ("missing", "value_error",
    "string_pattern_mismatch") — and never `input` or `msg`, which carry what
    the applicant typed. Without this a run of 422s was a count with no cause
    (2026-10-01): the reason went back to the phone and nowhere else.
    """
    parts: list[str] = []
    for err in errors:
        loc = [p for p in err.get("loc", ()) if isinstance(p, str) and p != "body"]
        part = f"{loc[-1] if loc else '?'}:{err.get('type', '?')}"
        if part not in parts:
            parts.append(part)
    log.info("POST /api/register refused (422, schema): %s", ", ".join(parts) or "?")


def _submission_key_hash(key: str | None) -> str | None:
    """sha256 hex of the form's submission key, or None when it sent none."""
    return hashlib.sha256(key.encode("ascii")).hexdigest() if key else None


def _duplicate_answer(
    db: Session, email: str, key_hash: str | None
) -> PublicRegistrationOut | None:
    """None when no LIVE application holds this address; otherwise the answer.

    THE SAME FORM'S RETRY IS ANSWERED WITH ITS OWN APPLICATION (2026-10-01). A
    phone that loses its signal can lose the REPLY after the row was saved,
    and the student's only move is to press Submit again. Until this, that
    press met the opaque 409 below, which reads as a refusal to somebody who
    has just applied. A repeat carrying the key the first submit carried gets
    the original application's 201 instead, built exactly as the first reply
    was. Only the browser that filled in the form holds that key (random, 128
    bits, never shown), and the row only ever stores its hash, so this reveals
    nothing to anybody who did not already submit the application.

    Everything else — no key, an older row with none, a different key — gets
    the 409 exactly as before; its reasoning follows. Called from the guard
    before anything is written, and again when a concurrent submit of the same
    address loses the race at the unique index, so the loser is told the same
    thing as a later retry rather than meeting a 500.
    """
    existing = db.scalar(
        select(Registration).where(
            Registration.email == email,
            Registration.status != RegistrationStatus.REJECTED,
        )
    )
    if existing is None:
        return None
    if (
        key_hash is not None
        and existing.submission_key_hash is not None
        and hmac.compare_digest(existing.submission_key_hash, key_hash)
    ):
        log.info(
            "POST /api/register: a retry of application %s with its own submission key; "
            "answered with the original application",
            existing.id,
        )
        return _public_out_one(db, existing)
    # Deliberately does NOT confirm that an application exists for this
    # address. The old wording ("An application with this email already
    # exists.") turned the public form into a "has X applied to this college"
    # lookup for anyone with a list of names, and applying somewhere is not
    # something an applicant chose to publish.
    #
    # Be honest about what is left: the STATUS CODE still separates the two
    # cases — a fresh submission 201s, this 409s — and the only complete fix
    # is to answer every submission identically and send the real outcome out
    # of band by email, which this endpoint does not do. The rate limit in
    # `submit` is what makes walking a roster through the remaining tell slow rather
    # than free. The real application id goes to the log, not the response, so
    # support can still find it.
    log.info(
        "POST /api/register refused a duplicate application (registration id=%s)",
        existing.id,
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "This application could not be accepted. If you have already applied, "
            "or you think this is a mistake, contact the placement office."
        ),
    )


@router.post("", response_model=PublicRegistrationOut, status_code=status.HTTP_201_CREATED)
def submit(
    # `media_type` stated, or the spec documents this body as urlencoded, which
    # cannot carry a file; the runtime parses multipart either way.
    body: Annotated[RegisterForm, Form(media_type="multipart/form-data")],
    request: Request,
    db: Session = Depends(get_db),
) -> PublicRegistrationOut:
    """Public: submit an application. No auth — the applicant is not a user yet.

    ONE MULTIPART REQUEST, THE CV AND THE PHOTO IN IT, AND NOTHING IS WRITTEN
    UNTIL BOTH HAVE BEEN JUDGED (2026-09-22). The application used to be
    created from a JSON body and the two files posted afterwards, one request
    each, keyed on the id the 201 handed back - and every way that second
    half could fail left an application in the queue with no CV and no photo,
    from a student who had filled in every compulsory box. The one that was
    not even a failure: a rule that AUTO-APPROVES at submit time decided the
    application before the uploads arrived, and `attach_document` refuses a
    decided application, so every auto-admitted student's CV was rejected by
    design. Now the row, its two document rows and their bytes land in one
    transaction after both files have passed the size and type checks; a
    refused file is a 413 or 415 with no application behind it, so the
    applicant retries the same form and never meets the duplicate guard.

    A PLAIN `def`, NOT `async def` (2026-09-29). Everything below is blocking
    I/O - the database, the file write to EFS, the archive PUT to S3 - and in
    an `async def` it ran ON the event loop, so while one student's files were
    being saved the whole API process answered nobody. On a results-day rush
    the queue behind it passed CloudFront's 60 s origin timeout and students
    were shown 504 for an application that often landed anyway. As a `def`
    FastAPI runs it on the threadpool, and the loop keeps serving everyone
    else; `tests/test_registration_concurrency.py` pins that.
    """
    # Limit BEFORE the database is touched: the point is that a flood never
    # reaches Postgres, not that Postgres survives it.
    client_ip = request.client.host if request.client else "unknown"
    retry_after = _rate_limit_retry_after(client_ip)
    if retry_after is not None:
        log.warning(
            "POST /api/register rate-limited (%s attempts in %ss) for %s",
            _RATE_MAX_PER_WINDOW,
            _RATE_WINDOW_SECONDS,
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts from this network. Please try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )

    email = body.email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        log.info("POST /api/register refused (422): email:invalid")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A valid email is required."
        )
    college_email_problem = _college_email_problem(email, body.personal_email)
    if college_email_problem is not None:
        # The reason code, never the address: which of the two refusals it was
        # is all support needs to tell a phone's autofill from a typo.
        same = email == (body.personal_email or "").strip().lower()
        log.info(
            "POST /api/register refused (422): email:%s",
            "same_as_personal" if same else "public_mail_domain",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=college_email_problem
        )
    # A LIVE application on the address blocks a new one; a REJECTED one does
    # not (2026-09-16). Until then this read every row, and `registrations.email`
    # was UNIQUE outright, so an address the office had rejected - for a
    # mistyped USN, say - could never apply again: the corrected form met the
    # 409 below, whose words are the same for a live duplicate, and the
    # rejection mail had sent them to the same office. A rejection is a
    # decision about ONE application and the row stays as its record; the
    # address is not spent with it. `uq_registration_live_email` (partial, on
    # `status <> 'REJECTED'`) is the same rule in the database, so two
    # submissions racing this read cannot both land. Reviewers see the history:
    # `_checks_for` puts a `prior_applications` line on the new row.
    key_hash = _submission_key_hash(body.submission_key)
    duplicate = _duplicate_answer(db, email, key_hash)
    if duplicate is not None:
        return duplicate

    # THE RULE IS APPLIED HERE, and the application reaches the review queue
    # immediately (2026-09-10).
    #
    # It briefly was not. Round 4 moved the rule behind an emailed confirmation
    # link so that no address could auto-approve itself onto the roster, and the
    # reasoning was sound — but the mail is the half that fails. On a deployment
    # whose SES account is still sandboxed NOTHING can be delivered to a student
    # address, so every applicant sat in PENDING_VERIFICATION: the student saw a
    # successful submission, the admin saw an empty queue, and the retry hit the
    # duplicate guard's deliberately opaque 409, which reads as a broken form.
    # A gate nobody can pass is not a gate, it is an outage.
    #
    # THE MAILBOX PROOF DID NOT GO AWAY — IT MOVED PAST THE DECISION. An
    # approved applicant is emailed a setup link and must then confirm the
    # address with a one-time code before they can set a password
    # (routers/onboarding.py). So an application still cannot become a usable
    # account without somebody reading that mailbox; what changed is that a
    # HUMAN now sees the application first, and a provisioned-but-unconfirmed
    # row is inert — its password hash is the unusable sentinel, and Google
    # sign-in needs the Google account itself.
    # THE FILES ARE JUDGED BEFORE ANYTHING IS WRITTEN - after the duplicate
    # guard, so an address that cannot apply is refused without its bytes
    # ever being read past the cap, and before the claim is resolved, so a
    # wrong file and a contradictory batch both leave nothing behind.
    cv = _read_document(body.cv, "cv")
    photo = _read_document(body.photo, "photo")

    reg = Registration(
        name=body.name.strip(),
        email=email,
        usn=body.usn,
        phone=body.phone,
        personal_email=body.personal_email,
        linkedin_url=body.linkedin_url,
        degree_level=body.degree_level,
        status=RegistrationStatus.PENDING_REVIEW,
        cohort_id=None,
        **_resolve_claim(db, body),
        matched_rule_id=None,
        decision_reason=None,
        submission_key_hash=key_hash,
    )
    db.add(reg)
    try:
        db.flush()  # mints reg.id for the two document rows
    except IntegrityError:
        # Two submits of one address raced past the guard above, and the
        # partial unique index `uq_registration_live_email` refused the second
        # INSERT once the first committed. That used to escape as a 500. The
        # loser is answered as a retry would be: its own application if it
        # carried the winner's key (a double-tapped Submit), the opaque 409
        # otherwise. Nothing has been stored yet, so a rollback is all of it.
        db.rollback()
        duplicate = _duplicate_answer(db, email, key_hash)
        if duplicate is not None:
            return duplicate
        raise
    stored: list[str] = []
    try:
        for kind, (content, original_name) in (("cv", cv), ("photo", photo)):
            stored.append(_store_document(db, reg, kind, content, original_name))
        db.commit()
    except Exception:
        # The row and both document rows roll back together; the bytes are on
        # the volume already and would be a file nobody can name, so they go
        # too. The archive copy `save_and_record` may have made is Object-Locked
        # and stays, unnamed, which is the price of archiving before the commit
        # and the reason the type is decided BEFORE the store is asked.
        db.rollback()
        for name in stored:
            try:
                delete_stored(name)
            except FileNotFoundError:
                pass
        raise
    db.refresh(reg)
    _apply_rule(db, reg)
    db.commit()
    db.refresh(reg)
    # The id and never the address: the log carries no applicant by name (a
    # reviewer reads who it is from the queue, which is behind require_admin).
    log.info("application %s is %s", reg.id, reg.status.value)
    return _public_out_one(db, reg)


#: The statuses this endpoint will list. DRAFT and PENDING_VERIFICATION are
#: deliberately absent: nothing has written either for a year (see
#: `RegistrationStatus`), so accepting them would answer an empty list forever
#: and read as "nobody was ever in that state" rather than "that state does not
#: exist". A 422 naming the five says which it is.
LISTABLE_STATUSES: tuple[RegistrationStatus, ...] = (
    RegistrationStatus.PENDING_REVIEW,
    RegistrationStatus.HOLD,
    RegistrationStatus.AUTO_APPROVED,
    RegistrationStatus.APPROVED,
    RegistrationStatus.REJECTED,
)

#: The statuses whose rows a human can still decide, and therefore the only ones
#: `checks[]` is computed for. A check on a decided row would be a live answer to
#: a question that was settled last month — "Approve will refuse this" about an
#: application already approved — so those rows answer `checks: null`, which the
#: contract above `CheckOut` defines as NOT COMPUTED.
DECIDABLE_STATUSES: tuple[RegistrationStatus, ...] = PENDING_QUEUE_STATUSES

#: The bound on the by-status path, and it is NOT OPTIONAL. This endpoint has
#: never had a LIMIT, which was survivable while it answered one status that an
#: office works through; AUTO_APPROVED grows without bound for the life of the
#: deployment, so the tab that lists it would hand back every application ever
#: submitted, with its claim names and document kinds, on every page load.
DEFAULT_QUEUE_LIMIT = 100
MAX_QUEUE_LIMIT = 500


@router.get("/pending", response_model=list[RegistrationOut])
def pending(
    response: Response,
    # ALIASED, NOT NAMED `status`. The module-level `status` here is FastAPI's
    # status-code namespace, which every raise in this file reads; a parameter
    # of that name shadows it inside this function only, so the first
    # HTTPException added later would be an AttributeError at runtime and
    # nowhere else.
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int | None = None,
    offset: int | None = None,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[RegistrationOut]:
    """The review queue — applications a human still needs to decide.

    WITH NO `?status=` THIS ANSWERS EXACTLY WHAT IT ALWAYS HAS: every
    PENDING_REVIEW application in reach, oldest first, unbounded, with the same
    scope headers. That is load-bearing rather than politeness — the Phase 2
    console is built against this array and the Pending tab is the screen's
    default view, so a page size appearing here would silently truncate the one
    list the office actually works from.

    `?status=` IS THE OTHER THREE TABS (B11.2, decision 2). One of
    `LISTABLE_STATUSES`; anything else is a 422 that names them. The bound comes
    with it and is not optional: see DEFAULT_QUEUE_LIMIT.

    WHY THE ORDER DEPENDS ON THE STATUS. A queue and a log are read from
    opposite ends. PENDING_REVIEW and HOLD are work waiting, and the row that
    matters is the applicant who has waited longest, so they come oldest first —
    the order this endpoint has always used. AUTO_APPROVED, APPROVED and
    REJECTED are a record of what happened, where the row that matters is the
    most recent one, and paging an unbounded history from the far end would put
    a deployment's first-ever application on page one forever.

    SCOPED BY THE CLAIM (B1.4), through `registration_scope_clause`. An
    application is not a student yet, so there is no `students` row for
    `Reach.student_ids()` to narrow: what it hangs under is what the applicant
    NAMED on the public form, walked up the spine by `_resolve_claim` and stored
    on the row. See app/scope_views.py for why that projection lives there.

    AN APPLICATION THAT NAMED NOTHING IS THE MAIN ADMIN'S. Every level on the
    form is optional, so a row with all five pointers null hangs under nothing
    and is reached by no scoped grant — the same answer `reaches_target` gives
    any unfiled thing, and the same reason: if "named nothing" were visible to
    everybody, it would be the way around every scope in the system, and it is
    a state a public form can produce on purpose.

    EVERY ROW CARRIES ITS CHECKS (B11.1) — the rule engine's verdict, the college
    domain fence, whose account this address already is, and the USN. Three of
    them are the guards `_provision_student` will apply when Approve is pressed,
    read out BEFORE the press rather than after: see `_checks_for` for the
    batching and the check vocabulary above `CheckOut` for what the three
    verdicts mean.
    """
    require_capability(db, session, "admin.registrations")
    wanted = _queue_status(status_filter)
    page = _queue_page(status_filter, limit, offset)
    reach = scope_filter(db, session, "admin.registrations")
    scope_header(response, reach)
    if reach.nothing:
        return []
    stmt = select(Registration).where(
        Registration.status == wanted, registration_scope_clause(reach)
    )
    if wanted in PENDING_QUEUE_STATUSES:
        stmt = stmt.order_by(Registration.created_at)
    else:
        stmt = stmt.order_by(Registration.created_at.desc())
    if page is not None:
        stmt = stmt.limit(page[0]).offset(page[1])
    rows = db.scalars(stmt).all()
    kinds = _doc_kinds(db, [r.id for r in rows])
    names = _claim_names(db, rows)
    # Only a row somebody can still decide gets a checklist. See
    # DECIDABLE_STATUSES: null here means NOT COMPUTED, never "nothing wrong".
    checks = (
        _checks_for(db, rows, docs=kinds, names=names, reveal_holders=reach.everything)
        if wanted in DECIDABLE_STATUSES
        else {}
    )
    return [_out(r, kinds.get(r.id, ()), names.get(r.id), checks.get(r.id)) for r in rows]


def _queue_status(requested: str | None) -> RegistrationStatus:
    """Which status the queue was asked for. None is PENDING_REVIEW, unchanged."""
    if requested is None:
        return RegistrationStatus.PENDING_REVIEW
    for candidate in LISTABLE_STATUSES:
        if requested.upper() == candidate.value:
            return candidate
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            "status must be one of "
            + ", ".join(s.value for s in LISTABLE_STATUSES)
            + "."
        ),
    )


def _queue_page(
    requested_status: str | None, limit: int | None, offset: int | None
) -> tuple[int, int] | None:
    """`(limit, offset)` for the by-status path, or None for the default one.

    PAGING IS PART OF `?status=` AND IS REFUSED WITHOUT IT, rather than quietly
    ignored. A client that sends `?limit=25` to the default queue believes it is
    paging; ignoring the parameter hands it every row and it renders the first
    25, so the office reads a queue that is missing applications and nothing on
    the screen says so. A 422 is a bug report; a silent full list is a defect
    that surfaces as "we never saw that application".
    """
    if requested_status is None:
        if limit is None and offset is None:
            return None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "limit and offset are only accepted with ?status=. The default "
                "queue is the whole pending list on purpose."
            ),
        )
    # Clamped, not refused: a client asking for 10 000 rows gets the biggest page
    # this endpoint will build rather than an error it has to learn to handle.
    size = DEFAULT_QUEUE_LIMIT if limit is None else max(1, min(int(limit), MAX_QUEUE_LIMIT))
    start = 0 if offset is None else max(0, int(offset))
    return size, start


class DecisionIn(BaseModel):
    decision: str  # "APPROVE" | "REJECT"
    note: str | None = None


#: The statuses a HUMAN decision is already over for — the exact pair this guard
#: has always tested, lifted to a name so that what is NOT in it is a statement
#: rather than a silence.
#:
#: HOLD is not in it: a hold is a bookmark, not an outcome, and Approve and
#: Reject are the verbs that end one.
#:
#: AUTO_APPROVED is not in it either, and that is unchanged behaviour, not an
#: oversight this constant introduces. A rule-approved application can still be
#: stamped by a reviewer, which `_provision_student` survives because it is
#: idempotent by lookup rather than by flag — it finds the account the rule
#: already made and returns it.
ALREADY_DECIDED_STATUSES: tuple[RegistrationStatus, ...] = (
    RegistrationStatus.APPROVED,
    RegistrationStatus.REJECTED,
)


#: Unusable-password sentinel. Identical to grant_access.SSO_ONLY_PASSWORD_HASH
#: and seed_roster's: it is NOT a "scrypt:<salt>:<digest>" string, so
#: verify_password can never match it. A provisioned account therefore cannot be
#: signed into with a password until one is deliberately set — which is the
#: activation flow's job, not this endpoint's.
SSO_ONLY_PASSWORD_HASH = "google-only"


def _provisioned_department(db: Session, cohort_id: str | None, department_id: str | None) -> str | None:
    """The department to file a newly provisioned student under.

    TOLERANT OF A DEPARTMENT THAT IS GONE, and that is the difference between
    this and the console's version. `resolve_student_department` raises for an
    id it cannot find so an admin who mistypes gets a 422 instead of a silent
    null — right there, wrong here: the id in an application was valid when the
    form was submitted, and a department archived in the meantime must not make
    an APPROVAL fail. An unfiled student the Main Admin can file is a far better
    outcome than an approval that cannot be completed at all.
    """
    try:
        return resolve_student_department(
            db, cohort_id=cohort_id, department_id=department_id, department_sent=False
        )
    except LookupError:
        return None


def _provision_student(db: Session, reg: Registration) -> Student:
    """Create the User + Student an approved application earns.

    THIS IS THE STEP THAT DID NOT EXIST. The endpoint below used to stamp a
    status and three audit columns and stop; its own docstring called
    provisioning "a separate follow-up step", and that step was nowhere in the
    codebase. `registrations.approved_student_id` was declared, returned by the
    API, and never written. The only ways a Student came into existence were
    `python -m app.seed_roster`, `python -m app.grant_access` and the dev-only
    `python -m app.seed` — all shell-only, none of which read this table.

    IDEMPOTENT BY LOOKUP, not by flag. A double-clicked Approve must not create
    two students. The email is unique on `users`, so an existing row is found
    and reused rather than inserted again; and if the registration already
    carries an approved_student_id, that student is returned untouched. Relying
    on the caller not to double-click is not a design.

    Reached twice, this function inserts nothing twice — and that is tested
    DIRECTLY (test_institutional_spine.py calls it twice in one session),
    because through the endpoint the already-decided 409 fires first and every
    lookup below could be deleted with the endpoint test still passing.

    TWO THINGS IT REFUSES, both because the application it is reading arrived
    from an unauthenticated public form: an address off the college domain, and
    an address that already belongs to a non-STUDENT account. See the guards
    inline — each one is a door into the roster that this function would
    otherwise open.

    NO PASSWORD IS SET. The account gets the unusable sentinel. Sign-in is
    Google-only in production, and where a password is wanted it arrives through
    activation — an invitation the student redeems — not from an admin reading
    one out.

    THE COHORT COMES FROM THE RULE. `registrations.cohort_id` was stamped by the
    matched registration rule at submit time. Copying it onto the student here
    is what finally connects the rule engine to a seat; before this it was
    written to the registrations row and read by nothing.
    """
    if reg.approved_student_id:
        existing = db.get(Student, reg.approved_student_id)
        if existing is not None:
            return existing

    email = (reg.email or "").strip().lower()

    # ------------------------------------------------------------------ #
    # GUARD 1: the domain. This endpoint MINTS A ROSTER ROW, and the roster
    # IS the access control (app/google_auth.py: an account with no matching
    # row is refused, nothing self-provisions). POST /api/register is public,
    # unauthenticated, and validates the address no further than "has an @";
    # registrations.email_verified_at has no writer, so nobody has proved the
    # applicant can even read that mailbox.
    #
    # Without this check, "Approve" on an application from any address on the
    # internet grants that address a real Google sign-in to REEP. The Main Admin
    # clicking it sees a form that looks like every other application. That
    # made approval a self-service door into the roster, which is the one
    # thing google_auth.py's whole design exists to prevent.
    #
    # settings.provisionable_email_domains explains why a fence HERE is
    # consistent with there deliberately being none on sign-in.
    # ------------------------------------------------------------------ #
    # THE COLLEGE'S LIST, not the deployment's (B1.1). An application names a
    # college on the form, and the rule engine stamps a cohort; either resolves
    # the tenant whose fence this is. A college with no domains recorded falls
    # back to the environment, which is what every application was fenced by
    # before colleges had domains — so day one is unchanged.
    #
    # ONE WRITER: `domain_verdict` above is this guard's own reading, and the
    # queue's `domain` check calls the same function rather than restating it.
    verdict = domain_verdict(db, reg)
    if not verdict.ok:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "This application cannot be approved: its email address is not on a "
                f"college domain ({', '.join(sorted(verdict.allowed))}). Approving it would "
                "create a sign-in account. Staff accounts are created with "
                "`python -m app.grant_access`, not from this queue."
            ),
        )

    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        user = User(
            email=email,
            name=(reg.name or "").strip() or email,
            role=Role.STUDENT,
            password_hash=SSO_ONLY_PASSWORD_HASH,
        )
        db.add(user)
        db.flush()  # need user.id for the Student row, same transaction
    elif user.role is not Role.STUDENT:
        # ------------------------------------------------------------------ #
        # GUARD 2: the role. The lookup above is by EMAIL ALONE, and the
        # duplicate check on submit is against registrations.email, not
        # users.email — so an application naming a mentor's or the admin's
        # address is accepted by the public form and lands in this queue
        # looking ordinary.
        #
        # Attaching a Student row to that account is not cosmetic: _payload_for
        # mints `studentId` into the session for ANY user who has one, so the
        # next sign-in carries role=MENTOR *and* a studentId. Never widen an
        # existing account's reach from an unauthenticated form; rule 2 says
        # scope is decided by role, and this would let a form edit it.
        # ------------------------------------------------------------------ #
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{email} already belongs to a {user.role.value} account. Approving "
                "this application would attach a student record to it. Reject the "
                "application, or change the address on it first."
            ),
        )

    student = db.scalar(select(Student).where(Student.user_id == user.id))
    if student is None:
        # Upper case, and compared upper case (app/usn.py): an application
        # typed in lower case is the same number as the roster's.
        usn = normalise_usn(reg.usn)
        if usn is not None and db.scalar(select(Student.id).where(usn_is(Student.usn, usn))) is not None:
            # ------------------------------------------------------------------ #
            # GUARD 3: the USN. `students.usn` is `unique=True, nullable=True`,
            # and this line wrote it with no check at all — so two applications
            # carrying one USN meant the first approved and the second raised
            # IntegrityError on COMMIT. That is a 500, not the 409 this endpoint
            # promises, and it lands AFTER `decide` has already added its
            # `record_change` row: the rollback takes the audit row with it, so
            # the trail does not even show that anybody tried.
            #
            # Two applications can honestly carry one USN — a typo, a
            # re-application under a corrected address, two colleges with
            # overlapping formats — so this is a refusal a reviewer can act on,
            # not an impossible state.
            #
            # WHAT IT DOES NOT CLOSE, stated so nobody reads more into it: two
            # approvals racing on two DIFFERENT applications lock two different
            # registration rows, so the check can still be overtaken between here
            # and the commit. The unique index remains the backstop; this turns
            # the case that actually happens — the clash already exists — into an
            # answer the reviewer can read.
            #
            # It fires only where a Student row is about to be INSERTED. An
            # applicant who already has one keeps it untouched, USN included, so
            # the ordinary re-application path never trips over its own number.
            # ------------------------------------------------------------------ #
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"USN {usn} already belongs to another student. Correct the USN on "
                    "this application, or reject it as a duplicate — approving it would "
                    "fail on the roster's uniqueness rule."
                ),
            )
        cohort_id = reg.cohort_id or reg.requested_cohort_id
        student = Student(
            user_id=user.id,
            usn=usn,
            # The rule's cohort wins - it is policy. When no rule seated them, the
            # batch the applicant picked on the form is what the Main Admin approved.
            cohort_id=cohort_id,
            # THE DEPARTMENT THE APPLICANT WAS REQUIRED TO NAME (31f7a4c60b12).
            # This was dropped on the floor for the whole life of the form:
            # College and Department are REQUIRED while Course, Specialization
            # and Batch are optional, so an applicant to a college that has not
            # built its batches yet — every college on day one — was provisioned
            # with `cohort_id` NULL and nothing else, which resolved to an empty
            # profile card and made them invisible to every department-scoped
            # read. The batch still wins where it has a department; `sent=False`
            # because a stored application is not a human contradicting
            # themselves, so this derives and never refuses an approval.
            department_id=_provisioned_department(db, cohort_id, reg.department_id),
            # THE SEMESTER COMES FROM THE BATCH (2026-10-06). The form never asks
            # for one, and the column's default of 1 put every student who
            # registered mid-programme in semester 1 on every screen and export.
            current_semester=semester_on(db, cohort_id, local_today()),
            # THE DUAL SPECIALIZATION TRAVELS WITH THE STUDENT (2026-09-23).
            # The batch is the first of the two; the application's other tick
            # is written here, so the roster editor shows what the applicant
            # opted for instead of it living on this row alone.
            second_specialization_id=dual_specialization.second_for_seat(
                db, cohort_id, (reg.specialization_id, reg.second_specialization_id)
            ),
        )
        db.add(student)
        db.flush()

    # A profile row so GET /api/student/profile answers 200 instead of 404 on
    # the student's first sign-in. The endpoint 404s on a missing profile, which
    # would make a freshly provisioned account look broken.
    # Queried by student_id, NOT db.get(): StudentProfile's primary key is its
    # own `id` and student_id is a separate unique column, so db.get() would
    # always miss and insert a duplicate that the unique index then rejects.
    existing_profile = db.scalar(
        select(StudentProfile).where(StudentProfile.student_id == student.id)
    )
    if existing_profile is None:
        # The phone and LinkedIn the form required (2026-09-16) are the same
        # two the placement profile asks the student for on day one; typing
        # them twice was the only outcome of not copying them.
        db.add(
            StudentProfile(
                student_id=student.id,
                email=email,
                phone=reg.phone,
                linkedin_url=reg.linkedin_url,
            )
        )
    else:
        # A re-applicant keeps what they have; the application fills blanks only.
        if not existing_profile.phone and reg.phone:
            existing_profile.phone = reg.phone
        if not existing_profile.linkedin_url and reg.linkedin_url:
            existing_profile.linkedin_url = reg.linkedin_url

    reg.approved_student_id = student.id
    return student


def _apply_rule(db: Session, reg: Registration) -> None:
    """Decide a freshly submitted application: auto-approve and provision, or
    queue it for a human.

    RUNS AT SUBMIT TIME, ON AN ADDRESS NOBODY HAS PROVED. This docstring used to
    say the opposite — "at confirmation time, not submit time, so the rule engine
    only ever sees addresses somebody has proved they own" — and both halves have
    been false since 2026-09-10: `submit()` calls this directly, and the
    confirmation gate it named was removed (see `submit()`'s own note on why a
    gate nobody can pass is an outage). The mailbox proof did not vanish, it
    moved PAST the decision, into onboarding. So the engine does see unproven
    addresses, which is exactly why GUARD 1 and GUARD 2 exist in
    `_provision_student` and why their refusal is caught below rather than
    allowed to drop or force an application through.

    If the rule says approve but provisioning refuses — the domain fence, or an address that already belongs
    to a staff account — the application is NOT dropped and NOT force-approved:
    it lands in the review queue with the refusal as its reason, which is
    where a human decision belongs. That also closes the old gap where
    AUTO_APPROVED rows were never provisioned by anything.
    """
    rule = _pick_rule(db, reg.email, reg.usn, reg.degree_level)
    if rule is None:
        reg.status = RegistrationStatus.PENDING_REVIEW
        reg.decision_reason = "No rule matched — needs manual review."
        return
    reg.matched_rule_id = rule.id
    reg.cohort_id = rule.cohort_id
    if not rule.auto_approve:
        reg.status = RegistrationStatus.PENDING_REVIEW
        reg.decision_reason = f"Routed by rule '{rule.name}' — awaiting review."
        return
    try:
        student = _provision_student(db, reg)
    except HTTPException as refused:
        reg.status = RegistrationStatus.PENDING_REVIEW
        reg.decision_reason = f"Rule '{rule.name}' would auto-approve, but: {refused.detail}"
        log.warning("auto-approve of application %s refused: %s", reg.id, refused.detail)
        return
    db.flush()
    # THE SAME ACT `decide` PERFORMS ON APPROVE, and it was missing here. The
    # CV and the photo become the student's first uploads - moved, not copied.
    # Until 2026-09-22 nothing reached this line with a document on the row:
    # the files were posted after the 201, by which time this rule had already
    # decided the application and `attach_document` refused them. Now they
    # arrive with the application, and an auto-admitted student who still
    # started with no resume would be the old defect wearing a new shape.
    _move_documents_to_uploads(db, reg, student)
    reg.status = RegistrationStatus.AUTO_APPROVED
    reg.decision_reason = f"Auto-approved by rule '{rule.name}'."
    reg.reviewed_at = datetime.now(timezone.utc)
    db.flush()
    db.commit()
    user = db.get(User, student.user_id)
    if user is not None:
        # The same invite the Main Admin's APPROVE sends. One way in, however
        # the decision was reached.
        account_links.issue_onboarding(db, user)


# GET /register/verify IS GONE (2026-09-10). It consumed a confirmation token
# and applied the rule; both moved. Nothing writes PENDING_VERIFICATION any
# more and migration 9b2d47f0ce15 emptied `email_verifications`, so the route
# could only ever have answered "expired or used" — while still being a live
# endpoint that spends secrets, which is not a thing to leave lying around for
# a flow that no longer exists.


def _assert_reachable(db: Session, session: dict, reg: Registration) -> None:
    """Refuse a decision on an application this caller's grant does not reach.

    THE QUEUE AND THE BUTTON MUST OBEY ONE RULE. Narrowing `pending` alone would
    leave the act it feeds wide open: a scoped reviewer who cannot SEE an
    application could still approve it by id — and approving is the one path in
    REEP that mints a `users` row, which is the access control itself. Written
    as a re-select through `registration_scope_clause` rather than a second
    reading of the reach in Python, so the list and the gate are the same
    predicate by construction.

    403 rather than the 404 rule 2 flattens its refusals to. The caller is a
    known reviewer and the id came off a screen, so "this one is not yours" is
    true, safe and actionable; the membership-oracle argument that makes rule 2
    lie applies to students, not to applications a reviewer was shown a list of.
    """
    reach = scope_filter(db, session, "admin.registrations")
    if reach.everything:
        return
    covered = db.scalar(
        select(Registration.id).where(
            Registration.id == reg.id, registration_scope_clause(reach)
        )
    )
    if covered is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your Registrations capability does not reach this application. "
                "An administrator can widen it in Governance."
            ),
        )


@router.post("/{registration_id}/decision", response_model=RegistrationOut)
def decide(
    registration_id: str,
    body: DecisionIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RegistrationOut:
    """Main Admin: approve or reject a pending application.

    APPROVE stamps the reviewer AND provisions the account — the User row, the
    Student row seated in the rule's cohort, a profile row, and
    approved_student_id — all in one transaction. REJECT stamps only.

    AUDITED SINCE B2.7, and the gap it closes was a real one: this is the ONLY
    path that mints a student account, and the roster IS the access control, yet
    until now the only record of the decision was the stamp on the application
    itself — which `reopen` above can clear. `reopen` was audited and the
    decision it undoes was not, so the trail showed the undo of an act it had
    never recorded."""
    require_capability(db, session, "admin.registrations")
    # SELECT ... FOR UPDATE, not db.get(). The already-decided check below is a
    # read followed by a write, and two admins clicking Approve at the same
    # moment both read PENDING, both pass the check, and both provision — the
    # second failing on the unique email with a 500 rather than the 409 this
    # endpoint promises. The row lock makes the second request wait for the
    # first to commit, so it reads APPROVED and gets the 409.
    reg = db.scalar(
        select(Registration).where(Registration.id == registration_id).with_for_update()
    )
    if reg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    _assert_reachable(db, session, reg)
    # A HELD APPLICATION IS STILL DECIDABLE, and that is the point of the status
    # rather than an accident of this test. HOLD means "I read this and it is
    # waiting on something", so Approve and Reject are exactly the verbs that
    # end it — a hold that had to be released first would make the reviewer
    # press two buttons to do one thing, and the second press would look
    # optional. `ALREADY_DECIDED_STATUSES` is the set that is genuinely over;
    # HOLD is deliberately not in it, and a test pins that.
    if reg.status in ALREADY_DECIDED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Application already decided."
        )
    decision = body.decision.upper()
    # Read before the mutation below: the audit row's `before` must say what the
    # application actually was, not what it is usually assumed to have been.
    status_before = reg.status.value
    user: User | None = None
    if decision == "APPROVE":
        reg.status = RegistrationStatus.APPROVED
        # Approval now PROVISIONS. See _provision_student: same transaction as
        # the status stamp, so an application is never left approved with no
        # account behind it.
        student = _provision_student(db, reg)
        db.flush()
        # The CV and photo the applicant attached become the student's first
        # uploads - moved, not copied, so nothing is left behind on the
        # application and the bytes exist once.
        _move_documents_to_uploads(db, reg, student)
        user = db.get(User, student.user_id)
    elif decision == "REJECT":
        # THE REASON TRAVELS WITH THE REFUSAL. A rejected applicant is not a
        # user and never becomes one, so this mail is the only channel the
        # product has to them; a decision sent without the note the admin was
        # already made to type is a refusal they cannot answer.
        if not (body.note or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A reason is required when rejecting an application.",
            )
        reg.status = RegistrationStatus.REJECTED
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="decision must be APPROVE or REJECT.",
        )
    reg.reviewed_by_id = session["userId"]
    reg.reviewed_at = datetime.now(timezone.utc)
    reg.review_note = body.note
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="registration", entity_id=reg.id,
        action="APPROVED" if decision == "APPROVE" else "REJECTED",
        before={"status": status_before},
        after={
            "status": reg.status.value,
            # The account this decision created, or null for a rejection. It is
            # the join between "an application was approved" and "this person
            # can sign in", and nothing else records it.
            "provisioned_user_id": user.id if user is not None else None,
            "note": body.note,
        },
        event_type=f"registration.{decision.lower()}d",
        payload={"email": reg.email},
    )
    db.commit()
    db.refresh(reg)
    # MAIL AFTER THE COMMIT, and never inside it. Both issuers commit their own
    # token, so calling them above would split this decision across two
    # transactions and leave "approved, no account" reachable if the second
    # failed. A send that fails now is a decision that stands with a mail
    # nobody got — recoverable by re-sending, which is the right way round.
    if reg.status is RegistrationStatus.APPROVED and user is not None:
        account_links.issue_onboarding(db, user)
    elif reg.status is RegistrationStatus.REJECTED:
        account_links.send_registration_rejected(db, reg, (body.note or "").strip())
    return _out_one(db, reg)


def _read_document(upload: UploadFile, kind: str) -> tuple[bytes, str]:
    """Read one uploaded document and decide what it is, WITHOUT storing it.

    `(content, original_name)`, or a 413 / 415 naming the kind. The size cap
    is enforced on `len(content)` after a `read(MAX+1)` - never `read()` - so
    the process holds at most one byte past the cap of a body an anonymous
    caller chose the size of; the type is decided by the store's sniff and
    then narrowed to what THIS kind may be, so a PNG posted as a CV is a 415
    and never bytes on the volume. Shared by `submit` (both files, before a
    row exists) and `attach_document` (one replacement, on a row that does).
    """
    _doc_kind, allowed_mimes, wanted = _DOCUMENT_ROUTES[kind]
    # `upload.file` and not `await upload.read`: the callers are sync handlers
    # on the threadpool (see `submit`), and the spooled file under an
    # UploadFile reads synchronously.
    content = upload.file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="The " + kind + " is larger than " + str(MAX_BYTES // (1024 * 1024)) + " MB.",
        )
    # THE TYPE IS DECIDED BEFORE ANYTHING IS STORED. This used to store the
    # file, read the sniffed mime off the result and `delete_stored` it again
    # when the kind was wrong -- which stopped being survivable when
    # `save_bytes` gained the permanent archive: the delete cannot reach an
    # Object-Locked bucket, so every wrong-type upload left an unnamed object
    # there while its manifest row rolled back with the failed request.
    try:
        sniffed, _ext = sniff(content)
    except ValueError as exc:
        # The store could not recognise the bytes at all (not PDF/PNG/JPEG).
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc))
    if sniffed not in allowed_mimes:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="The " + kind + " must be " + wanted + ".",
        )
    return content, (upload.filename or "document")


def _store_document(
    db: Session, reg: Registration, kind: str, content: bytes, original_name: str
) -> str:
    """Store judged bytes and write - or replace - the application's row of
    that kind. Returns the stored name. NO COMMIT: `submit` writes two of
    these beside the application row in one transaction, and `attach_document`
    commits its one.

    The bytes go through app/document_store exactly as a student's own uploads
    do - magic-sniffed again on the way in, size-capped, no client path near
    the disk - through `save_and_record`, the one spelling routers may use.
    One of each kind: a second CV replaces the first, old bytes deleted first.
    """
    doc_kind, _allowed_mimes, _wanted = _DOCUMENT_ROUTES[kind]
    try:
        stored_name, mime, size = save_and_record(
            db,
            content,
            quota=VolumeQuota.single_slot(noun=kind),
            kind=DocumentOwnerKind.REGISTRATION_DOCUMENT,
            # NONE, AND STATED RATHER THAN OMITTED. An applicant has no account
            # yet -- that is the whole shape of the registration flow -- so the
            # address on the `registrations` row is the only identity there is,
            # and inventing an owner id here would be inventing a user.
            owner_id=None,
            original_name=original_name,
        )
    except QuotaRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:
        # The store could not recognise the bytes at all (not PDF/PNG/JPEG).
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc))
    existing = db.scalar(
        select(RegistrationDocument).where(
            RegistrationDocument.registration_id == reg.id,
            RegistrationDocument.kind == doc_kind,
        )
    )
    if existing is not None:
        # Replace in place: old bytes go first, so a failure between the two
        # writes leaves the row pointing at the NEW file, never at nothing.
        # The third replace-in-place store, after the staff signature and the
        # alumni resume: one document per (registration, kind), so the
        # superseded file is a pointer about to be overwritten below and no row
        # is left naming the old bytes. The manifest is where they keep their
        # name -- models/archived_document.py.
        #
        # Released BEFORE the unlink -- routers/student.py's delete carries the
        # full reasoning: a SELECT between an irreversible delete and the commit
        # turns any query failure into destroyed bytes plus a surviving row.
        release(
            db,
            existing.stored_name,
            reason="registration document replaced",
            facts=DocumentFacts(
                kind=DocumentOwnerKind.REGISTRATION_DOCUMENT,
                owner_id=None,
                original_name=existing.original_name,
                mime_type=existing.mime_type,
                size_bytes=existing.size_bytes,
                recorded_at=reg.created_at,
            ),
        )
        try:
            delete_stored(existing.stored_name)
        except FileNotFoundError:
            pass
        existing.original_name = original_name
        existing.stored_name = stored_name
        existing.mime_type = mime
        existing.size_bytes = size
    else:
        db.add(
            RegistrationDocument(
                registration_id=reg.id,
                kind=doc_kind,
                original_name=original_name,
                stored_name=stored_name,
                mime_type=mime,
                size_bytes=size,
            )
        )
    return stored_name


@router.post(
    "/{registration_id}/documents/{kind}",
    response_model=PublicRegistrationOut,
    status_code=status.HTTP_200_OK,
)
def attach_document(
    registration_id: str,
    kind: str,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> PublicRegistrationOut:
    """Replace the CV or the photo on an application that nobody has decided yet.

    PUBLIC, like the form that created the application - there is no account to
    sign in with yet. The application id is the bearer: a uuid4 the client was
    handed on the 201, unguessable, and the same trust the emailed confirmation
    link carries. It is accepted only while the application is undecided
    (PENDING_REVIEW or HOLD - and the dead PENDING_VERIFICATION; see the check
    itself), so a file can never be slipped onto a record the Main Admin has
    already ruled on.

    THE FORM NO LONGER CALLS THIS (2026-09-22). Both files arrive with the
    application itself (`submit`), so an application cannot exist without
    them; what remains here is the REPLACEMENT path - an applicant the office
    has HELD and asked for a better scan, and the rows written before that
    date that arrived without a file. The bytes are judged and stored by the
    same two helpers `submit` uses, so the two doors cannot disagree about
    what a CV is.

    Rate-limited per source address like POST /register: with no account on
    the request there is nothing else to key on, and the limiter's own note
    says why that is acceptable for this form and not for sign-in.
    """
    if kind not in _DOCUMENT_ROUTES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown document kind.")

    client_ip = request.client.host if request.client else "unknown"
    retry_after = _rate_limit_retry_after(client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many uploads from this connection. Wait a few minutes and try again.",
            headers={"Retry-After": str(retry_after)},
        )

    reg = db.scalar(
        select(Registration).where(Registration.id == registration_id).with_for_update()
    )
    if reg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    # UNDECIDED, WHICH NOW INCLUDES HOLD. "Held for a missing document" is the
    # main reason to hold an application at all, so an applicant who is told to
    # send their CV must be able to send it; refusing here would make the hold
    # note an instruction the product itself blocks.
    #
    # PENDING_VERIFICATION IS DEAD and is kept here only so a row written before
    # migration 9b2d47f0ce15 — if any survived it — is not locked out by this
    # check. Nothing has written it since; see `RegistrationStatus` for why the
    # value cannot simply be removed. Read this tuple as "the three statuses
    # nobody has ruled on", not as three live states.
    if reg.status not in (
        RegistrationStatus.PENDING_VERIFICATION,
        RegistrationStatus.PENDING_REVIEW,
        RegistrationStatus.HOLD,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This application has already been decided; documents can no longer be added.",
        )

    content, original_name = _read_document(file, kind)
    _store_document(db, reg, kind, content, original_name)
    db.commit()
    db.refresh(reg)
    return _public_out_one(db, reg)


@router.get("/{registration_id}/documents/{kind}/file")
def download_document(
    registration_id: str,
    kind: str,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> Response:
    """Hand the reviewer the CV or the photograph the applicant attached.

    THE QUEUE HAS ALWAYS SAID A CV EXISTS AND NEVER LET ANYBODY READ IT.
    `_doc_kinds` puts the kinds on every row of `pending` so the console can
    draw its document chips, and the checks beside them are there so the
    decision is made on evidence - but the evidence itself had no route out of
    this server at all. The reviewer was asked to approve or refuse an
    application whose one attachment they could not open, which is
    `mentor.download_student_upload`'s bug ("the queue was asking people to
    verify evidence they could not open") sitting on the other queue, unnoticed
    because this one never had a download to begin with.

    GATED EXACTLY LIKE THE DECISION IT FEEDS: `admin.registrations`, then
    `_assert_reachable`. That pairing is the point - a reviewer who cannot see
    an application in the queue must not be able to read its files by id, and
    writing the gate as the queue's own predicate re-selected is what makes the
    two the same rule rather than two readings of it.

    SO AN UNREACHABLE APPLICATION IS 403 HERE, NOT 404, and that is deliberate
    rather than an oversight. `_assert_reachable` argues the case in full: the
    caller is a known reviewer and the id came off a screen, so "this one is not
    yours" is true, safe and actionable, and the membership-oracle argument that
    makes rule 2 flatten its refusals applies to students and not to
    applications a reviewer was shown a list of. Flattening it here would put
    one endpoint's refusal out of step with every other verb on the same row.
    404 is kept for the three facts that really are absences: no such
    application, no such kind, no document of that kind attached.

    ATTACHMENT, NEVER INLINE. `content_disposition`'s default, for the reason it
    states: everything in this store is a file a stranger uploaded - here
    literally an unauthenticated stranger - and a PDF rendered inline runs its
    embedded JavaScript in the SPA's own origin. The magic-byte sniff cannot
    help, because the payload IS a valid PDF.

    A FILE MISSING FROM THE VOLUME IS A 404. `read_bytes` raises
    FileNotFoundError for a name nothing is behind, and the honest answer to
    "open this document" when the bytes are gone is that there is nothing to
    open - not a 500 that reads to the office as a broken console. The row
    naming it stays exactly where it is; this endpoint reads and repairs
    nothing.
    """
    require_capability(db, session, "admin.registrations")
    route = _DOCUMENT_ROUTES.get(kind)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown document kind.")
    doc_kind = route[0]
    reg = db.get(Registration, registration_id)
    if reg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    _assert_reachable(db, session, reg)
    doc = db.scalar(
        select(RegistrationDocument).where(
            RegistrationDocument.registration_id == reg.id,
            RegistrationDocument.kind == doc_kind,
        )
    )
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This application has no " + kind + " attached.",
        )
    try:
        content = read_bytes(doc.stored_name)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Stored file is missing."
        )
    return Response(
        content=content,
        media_type=doc.mime_type,
        # RFC 6266, same as every other download here: an applicant's own
        # filename interpolated raw raises inside Response.__init__ the moment
        # it leaves latin-1, which at this college is a matter of course.
        headers={"Content-Disposition": content_disposition(doc.original_name)},
    )


class RegistrationCorrectionIn(BaseModel):
    """What a reviewer may correct on an application still waiting for a
    decision: the USN, and nothing else. An unknown field is a 422 rather than
    ignored, so a client that sends `email` learns it was not applied."""

    model_config = ConfigDict(extra="forbid")

    usn: str = Field(min_length=1, max_length=32)

    @field_validator("usn")
    @classmethod
    def _clean_usn(cls, value: str) -> str:
        # Upper case, as the roster stores it (app/usn.py).
        value = normalise_usn(value)
        if value is None:
            raise ValueError("enter the USN")
        return value


@router.patch("/{registration_id}", response_model=RegistrationOut)
def correct(
    registration_id: str,
    body: RegistrationCorrectionIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RegistrationOut:
    """Correct the USN on an application waiting for a decision (2026-10-16).

    THE CHECKLIST AND THE 409 BOTH SAID "CORRECT THE USN ON THIS APPLICATION",
    AND NOTHING COULD. An application whose USN another student held could
    only be rejected, so an applicant who mistyped one digit was refused and
    had to apply again. This is that correction.

    Only PENDING_REVIEW and HOLD, the statuses Approve still accepts: on a
    decided application the USN is history, and an approved one's number lives
    on the student's record, which Students edits.

    A USN another student holds is refused here too (409), because correcting
    one clash into another helps nobody. The applicant's OWN record holding it
    is not a clash: Approve reuses that record (GUARD 3's control flow).

    Answers the row WITH its checklist, unlike `decision`, `hold` and `reopen`:
    the row is still in the queue, and the reviewer needs the new verdict to
    decide it.
    """
    require_capability(db, session, "admin.registrations")
    reg = db.scalar(
        select(Registration).where(Registration.id == registration_id).with_for_update()
    )
    if reg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    _assert_reachable(db, session, reg)
    if reg.status not in DECIDABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only an application waiting for a decision can be corrected.",
        )
    reveal = scope_filter(db, session, "admin.registrations").everything
    usn = body.usn
    before = reg.usn
    if usn != before:
        holder = db.scalar(select(Student).where(usn_is(Student.usn, usn)))
        if holder is not None:
            account = db.scalar(
                select(User).where(func.lower(User.email) == (reg.email or "").strip().lower())
            )
            if account is None or holder.user_id != account.id:
                owner = db.get(User, holder.user_id) if reveal else None
                who = f"{owner.name} ({owner.email})" if owner is not None else "another student"
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"USN {usn} already belongs to {who}. Check the number, or fix that "
                        "record on Students first."
                    ),
                )
        reg.usn = usn
        record_change(
            db, session=session, request=request, tenant_id=None,
            entity_type="registration", entity_id=reg.id, action="CORRECTED",
            before={"usn": before}, after={"usn": usn},
            event_type="registration.corrected", payload={"email": reg.email},
        )
        db.commit()
        db.refresh(reg)
    kinds = _doc_kinds(db, [reg.id])
    names = _claim_names(db, [reg])
    checks = _checks_for(db, [reg], docs=kinds, names=names, reveal_holders=reveal)
    return _out(reg, kinds.get(reg.id, ()), names.get(reg.id), checks.get(reg.id))


class HoldIn(BaseModel):
    note: str | None = None


@router.post("/{registration_id}/hold", response_model=RegistrationOut)
def hold(
    registration_id: str,
    body: HoldIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RegistrationOut:
    """Park an application a reviewer has read but cannot decide yet (B11.2).

    WHY THE STATUS EXISTS. Without it the queue carries two kinds of
    PENDING_REVIEW that look identical and mean opposite things: "nobody has
    looked at this" and "I looked, and it is waiting on a CV the applicant has
    not sent". The second was recorded by doing nothing, so the next reviewer
    read the application from scratch and the applicant waited twice.

    A NOTE IS REQUIRED - 422 without one. A HOLD carrying no words is
    indistinguishable from PENDING_REVIEW on every screen in the product, so the
    note is not decoration on the feature, it IS the feature. This is the same
    rule as B3.1's disable reason and for the same reason: nobody remembers in
    six weeks, and the row is the only place that could have said.

    IT IS INTERNAL, and that is a decision rather than an omission. Nothing is
    mailed, `decision_reason` is untouched, and the note does not travel to the
    applicant - `PublicRegistrationOut` declares none of the three hold columns
    and `_public_out_one` narrows by `model_fields`. Communicating a hold would
    need a mail that a sandboxed SES account cannot send, which is the exact
    failure that killed PENDING_VERIFICATION; a gate nobody can pass is an
    outage, and a notice nobody receives is worse than none.

    ONLY A PENDING APPLICATION CAN BE HELD. Re-holding one that is already on
    hold is a 409 naming reopen rather than an in-place edit of the note: two
    audit rows (released, held again) say what happened, where a silent
    overwrite loses the first reviewer's words entirely.

    Reached through `_assert_reachable`, the same predicate the queue is listed
    by, so a scoped reviewer cannot hold an application they cannot see.
    """
    require_capability(db, session, "admin.registrations")
    # SELECT ... FOR UPDATE for the same reason `decide` uses one: the status
    # check below is a read followed by a write, and two reviewers acting on one
    # row must not both pass it.
    reg = db.scalar(
        select(Registration).where(Registration.id == registration_id).with_for_update()
    )
    if reg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    _assert_reachable(db, session, reg)
    note = (body.note or "").strip()
    if not note:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A note is required when holding an application — say what it is waiting on.",
        )
    if reg.status is RegistrationStatus.HOLD:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This application is already on hold. Reopen it to put it back in the "
                "queue, then hold it again with the new note."
            ),
        )
    if reg.status is not RegistrationStatus.PENDING_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only an application waiting for review can be held.",
        )
    status_before = reg.status.value
    reg.status = RegistrationStatus.HOLD
    reg.hold_note = note
    reg.held_by_id = session["userId"]
    reg.held_at = datetime.now(timezone.utc)
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="registration", entity_id=reg.id, action="HELD",
        before={"status": status_before},
        after={"status": reg.status.value, "hold_note": note},
        event_type="registration.held", payload={"email": reg.email},
    )
    db.commit()
    db.refresh(reg)
    return _out_one(db, reg)


@router.post("/{registration_id}/reopen", response_model=RegistrationOut)
def reopen(
    registration_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RegistrationOut:
    """Put an application back in the queue - the design's Undo, and the way a
    hold is released.

    ONE VERB, ONE MEANING: "back in the queue, waiting for a decision". That is
    exactly what undoing a rejection does and exactly what releasing a hold does,
    so they are the same route rather than two that drift - a second endpoint
    doing the same thing to a different status is how one of them ends up
    forgetting to clear a stamp.

    Rejection only stamped the row, so reopening is exact: status back to
    PENDING_REVIEW, the reviewer stamp and remarks cleared, the attached
    documents untouched (they were never deleted on rejection, precisely so
    this is lossless). A hold is the same shape - its own three columns are
    cleared, and the row is a plain pending application again.

    An APPROVED application is refused with 409: approval PROVISIONED a User and
    a Student and sent them an enrolment notice, and quietly deleting an account
    someone was just told to sign in to is not an undo - it is a second,
    destructive decision that deserves its own tooling. THE BOARD'S "reopen
    within 24 h undoes an approval" IS WRONG ABOUT THIS SERVER, deliberately:
    deprovisioning is `python -m app.purge_students`, and it is not a button.

    Audited through record_change with the stamp it clears, so "who reopened
    this and what did the rejection or the hold say" stays answerable.
    """
    require_capability(db, session, "admin.registrations")
    reg = db.scalar(
        select(Registration).where(Registration.id == registration_id).with_for_update()
    )
    if reg is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    _assert_reachable(db, session, reg)
    if reg.status not in (RegistrationStatus.REJECTED, RegistrationStatus.HOLD):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Only a rejected or held application can be reopened. An approved one "
                "already has an account behind it."
            ),
        )
    before = {
        "status": reg.status.value,
        "reviewed_by_id": reg.reviewed_by_id,
        "reviewed_at": reg.reviewed_at.isoformat() if reg.reviewed_at else None,
        "review_note": reg.review_note,
        # The hold this released, when it released one. Null on every reopened
        # rejection, which is the honest answer rather than an absent key.
        "held_by_id": reg.held_by_id,
        "held_at": reg.held_at.isoformat() if reg.held_at else None,
        "hold_note": reg.hold_note,
    }
    reg.status = RegistrationStatus.PENDING_REVIEW
    reg.reviewed_by_id = None
    reg.reviewed_at = None
    reg.review_note = None
    reg.hold_note = None
    reg.held_by_id = None
    reg.held_at = None
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="registration", entity_id=reg.id, action="REOPENED",
        before=before, after={"status": reg.status.value},
        event_type="registration.reopened", payload={"email": reg.email},
    )
    db.commit()
    db.refresh(reg)
    return _out_one(db, reg)



# --- B11.3 · the seating rules themselves -------------------------------------
#
# A RULE WITH `auto_approve` AND A BATCH IS THE ONE CONTROL IN THIS PRODUCT THAT
# SEATS A STUDENT WITHOUT A HUMAN. `_apply_rule` stamps the batch and calls
# `_provision_student`, which mints the User row, the Student row and the
# profile, moves the documents across and emails the onboarding link — and the
# application never appears in anybody's queue. Every decision below is shaped by
# that one fact rather than by CRUD convention:
#
#   * `validate_usn_pattern` IS CALLED ON EVERY WRITE, and its ValueError comes
#     back as a 422 carrying that message verbatim. The message was written for
#     the rule AUTHOR, who is the only person who can fix the pattern. Not
#     calling it would let an admin store "(a+)+$" and turn POST /register — the
#     one unauthenticated write in the app — into a ReDoS anybody can fire. This
#     is rule-write defence #1 from the comment block at the top of this module;
#     `_usn_matcher`'s match-time refusal is #2 and stays exactly where it is,
#     because a rule row can always be edited straight in psql and skip whatever
#     this path checks.
#   * NOTHING TOUCHES `_usn_matcher`'s CACHE. It is keyed on the pattern STRING,
#     so an edited pattern is a new key and the fix takes effect with no restart;
#     a deleted rule leaves its compiled matcher behind, and nothing ever looks
#     it up again — one entry in a 256-entry LRU. Keying that cache on the rule
#     id instead, which is the obvious "fix", would make an edit invisible until
#     a restart: the opposite of what the cache is for.
#   * THERE IS NO REORDER CONTROL, and there must not be one. `_pick_rule` breaks
#     a `priority` tie on `created_at` "so a rule added later can't silently
#     outrank an equal", and a screen where an author types a priority produces
#     ties constantly. A drag-to-reorder that rewrote `created_at` would rewrite
#     which rule fired yesterday, under a control that looks cosmetic. Priority
#     is typed; equal priorities are settled by age; age is not editable.
#   * EVERY WRITE IS AUDITED, the delete included, with the rule's whole
#     before-state on the row. The evidence of a bad rule is an account that
#     exists and an application nobody ever reviewed, so "who wrote this rule and
#     what did it say" has to survive the rule being deleted.
#
# SCOPED BY THE RULE'S BATCH (decision 3), through
# `scope_views.registration_rule_scope_clause` — the READ as well as the writes.
# A college admin who could list a rule they cannot edit, with nothing on screen
# saying which is which, is worse than either alternative. The write gate is
# `_assert_rule_reachable`, which re-SELECTs through the list's own predicate
# exactly as `_assert_reachable` does for an application, and a rule naming NO
# batch hangs under nothing and belongs to the Main Admin alone.


class RuleOut(BaseModel):
    id: str
    name: str
    enabled: bool
    email_domain: str | None
    usn_pattern: str | None
    degree_level: str | None
    cohort_id: str | None
    auto_approve: bool
    priority: int
    #: THE TIEBREAK, ON THE WIRE. Two rules at priority 100 are decided by which
    #: was created first (`_pick_rule`), and a screen that ordered by priority
    #: alone would draw the pair in whichever order the database felt like.
    created_at: datetime


class RuleIn(BaseModel):
    """A new seating rule. Every condition is optional and an omitted one is a
    WILDCARD — which is why `_assert_rule_has_a_condition` exists below."""

    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    email_domain: str | None = Field(default=None, max_length=200)
    # A COARSE bound only. The real limit is MAX_USN_PATTERN_LENGTH and it is
    # enforced by `validate_usn_pattern`, whose refusal is one sentence written
    # for the author; a Field(max_length=200) here would answer the same case
    # with FastAPI's list-shaped schema error instead, which is the message that
    # renders as "[object Object]" in a client that forgets `detailOf`.
    usn_pattern: str | None = Field(default=None, max_length=2000)
    degree_level: DegreeLevel | None = None
    cohort_id: str | None = None
    auto_approve: bool = False
    # 0 is "before everything". The ceiling is arbitrary and generous: priority
    # is an ordering, not a score, and nothing reads its magnitude.
    priority: int = Field(default=100, ge=0, le=10_000)


class RulePatch(BaseModel):
    """A partial edit. NULL AND ABSENT ARE DIFFERENT HERE: an absent field is
    left alone, and an explicit `null` CLEARS a condition (it is how a rule stops
    requiring a USN pattern). The four fields the column cannot hold NULL for are
    refused rather than silently ignored — see `_rule_patch`."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    email_domain: str | None = Field(default=None, max_length=200)
    usn_pattern: str | None = Field(default=None, max_length=2000)
    degree_level: DegreeLevel | None = None
    cohort_id: str | None = None
    auto_approve: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)


#: The fields a rule row cannot hold NULL for. An explicit `null` on one of these
#: is a 422 rather than a no-op: a client that sends `{"name": null}` believes it
#: is doing something, and ignoring it is how a screen reports a save that never
#: happened.
_RULE_NOT_NULLABLE: tuple[str, ...] = ("name", "enabled", "auto_approve", "priority")


def _rule_out(rule: RegistrationRule) -> RuleOut:
    """ONE writer for the rule's shape, so the list and the three write routes
    cannot answer different objects for the same row."""
    return RuleOut(
        id=rule.id,
        name=rule.name,
        enabled=rule.enabled,
        email_domain=rule.email_domain,
        usn_pattern=rule.usn_pattern,
        degree_level=rule.degree_level.value if rule.degree_level is not None else None,
        cohort_id=rule.cohort_id,
        auto_approve=rule.auto_approve,
        priority=rule.priority,
        created_at=rule.created_at,
    )


def _rule_snapshot(rule: RegistrationRule) -> dict:
    """What the audit trail keeps. The whole rule, because a rule is small and
    the question asked of the trail later is "what did it say", not "what
    changed"."""
    return {
        "name": rule.name,
        "enabled": rule.enabled,
        "email_domain": rule.email_domain,
        "usn_pattern": rule.usn_pattern,
        "degree_level": rule.degree_level.value if rule.degree_level is not None else None,
        "cohort_id": rule.cohort_id,
        "auto_approve": rule.auto_approve,
        "priority": rule.priority,
    }


def _validated_rule_pattern(pattern: str | None) -> str | None:
    """Rule-write defence #1, as a 422 in the author's own language.

    The ValueError text is handed back UNCHANGED. It names the actual problem —
    too long, a quantified group, a syntax error and where — and the person
    reading it is the person editing the pattern.
    """
    if pattern is None:
        return None
    pattern = pattern.strip()
    if not pattern:
        return None
    try:
        validate_usn_pattern(pattern)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return pattern


def _validated_rule_domain(domain: str | None) -> str | None:
    """`@BGSCET.ac.in ` -> `bgscet.ac.in`, or a 422 saying what was expected.

    NORMALISED AT WRITE TIME because `_rule_matches` compares the stored value
    against `_email_domain(email)`, which is already lowercased and stripped: a
    rule stored as "BGSCET.ac.in" would simply never match, and the symptom is
    an application quietly falling through to manual review with no rule to
    blame. The same normalisation `institution_domains` uses, not a second copy.
    """
    if domain is None:
        return None
    normalised = normalise_domain(domain)
    if not normalised:
        return None
    if "@" in normalised or any(ch.isspace() for ch in normalised) or "." not in normalised:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "email_domain is the part after the @ — 'bgscet.ac.in', not a whole "
                "address and not a single word."
            ),
        )
    return normalised


def _validated_rule_cohort(db: Session, cohort_id: str | None) -> str | None:
    """The batch a rule seats into must exist. Checked here rather than left to
    the foreign key, because an IntegrityError on commit is a 500 with the audit
    row rolled back inside it — the same shape as the USN hole GUARD 3 closed."""
    if not cohort_id:
        return None
    if db.get(Cohort, cohort_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That batch does not exist. Pick one from the list, or leave the rule unseated.",
        )
    return cohort_id


def _assert_rule_has_a_condition(rule: RegistrationRule) -> None:
    """Refuse an auto-approving rule that matches EVERYTHING.

    A rule with no populated condition is a wildcard — `_rule_matches` returns
    True for every applicant — and `auto_approve` on top of that is "provision an
    account for whoever submits the form", which is the one outcome this queue
    exists to prevent. It is not hypothetical on a CRUD screen where all three
    conditions are optional inputs and the auto-approve switch is one click.

    The deployment that genuinely wants "everyone on our domain is waved
    through" says so by naming the domain, which costs one field and makes the
    intent readable on the rules table afterwards. A rule that does NOT
    auto-approve is left alone: a catch-all that routes everything to review with
    a label is a useful rule and refuses nobody.

    This is not a substitute for GUARD 1 — the college's provisionable domains
    still fence an auto-approval, and `_apply_rule` catches that refusal and
    sends the application to review. It is a second lock on the same door,
    because the first one is a per-college list an operator can widen.
    """
    if not rule.auto_approve:
        return
    if rule.email_domain or rule.usn_pattern or rule.degree_level is not None:
        return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            "A rule that auto-approves with no conditions would provision an account for "
            "every application ever submitted. Name at least one condition — the email "
            "domain is the usual one — or leave auto-approve off."
        ),
    )


def _assert_rule_reachable(db: Session, session: dict, rule: RegistrationRule) -> None:
    """Refuse a rule this caller's grant does not reach (decision 3).

    THE SAME CONSTRUCTION AS `_assert_reachable`, and for the same reason: a
    re-SELECT through `registration_rule_scope_clause` rather than a second
    reading of the reach in Python, so the drawer's list and the buttons under it
    obey one predicate. A rule naming no batch hangs under nothing and is reached
    only by a reach of everything, which is the Main Admin — the same answer
    `registration_scope_clause` gives an application that named nothing.

    CALLED AFTER THE FLUSH ON A CREATE OR A RE-TARGET, not before. There is no
    row to re-select until there is a row, and checking a cohort id in Python
    first would be precisely the second reading this avoids. Nothing is committed
    until every check has passed, and `get_db` closes the session — rolling the
    insert back — on the way out of the exception.

    403, not 404: the caller is a known reviewer, so "this one is not yours" is
    true, safe and actionable. See `_assert_reachable` for why that argument does
    not transfer to students.
    """
    reach = scope_filter(db, session, "admin.registrations")
    if reach.everything:
        return
    covered = db.scalar(
        select(RegistrationRule.id).where(
            RegistrationRule.id == rule.id, registration_rule_scope_clause(reach)
        )
    )
    if covered is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your Registrations capability does not reach that batch, so it cannot "
                "seat a rule there. A rule that names no batch at all is the Main "
                "Admin's."
            ),
        )


@router.get("/rules", response_model=list[RuleOut])
def rules(
    response: Response,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> list[RuleOut]:
    """The rule set, in the order the engine evaluates it.

    NARROWED THE SAME WAY THE WRITES ARE (decision 3). This list was
    unscoped — every rule to any holder of `admin.registrations` — which was the
    third of the three options the map laid out and the worst of them: a college
    admin reading rules they cannot edit, with nothing saying which is which.
    `X-Reep-Scope` states the narrowing, so "no rules" and "no rules you can see"
    are different words on the screen rather than the same empty table.
    """
    require_capability(db, session, "admin.registrations")
    reach = scope_filter(db, session, "admin.registrations")
    scope_header(response, reach)
    if reach.nothing:
        return []
    rows = db.scalars(
        select(RegistrationRule)
        .where(registration_rule_scope_clause(reach))
        .order_by(RegistrationRule.priority, RegistrationRule.created_at)
    ).all()
    return [_rule_out(r) for r in rows]


@router.post("/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(
    body: RuleIn,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RuleOut:
    """Write a new seating rule (B11.3).

    THE ORDER OF THE CHECKS IS THE POINT. The pattern is validated before
    anything is written, the batch is proven to exist before the foreign key can
    500 on it, the wildcard-auto-approve door is shut, and the row is flushed and
    then re-SELECTed through the LIST'S OWN predicate — so a scoped author cannot
    seat a rule in somebody else's batch, and cannot write a batchless
    programme-wide rule at all. Only then is anything committed.

    Audited. `auto_approve` plus a batch is the one control in the product that
    mints an account with no human in the loop, and the trail is where "who
    turned that on" is answered.
    """
    require_capability(db, session, "admin.registrations")
    rule = RegistrationRule(
        name=body.name.strip(),
        enabled=body.enabled,
        email_domain=_validated_rule_domain(body.email_domain),
        usn_pattern=_validated_rule_pattern(body.usn_pattern),
        degree_level=body.degree_level,
        cohort_id=_validated_rule_cohort(db, body.cohort_id),
        auto_approve=body.auto_approve,
        priority=body.priority,
    )
    if not rule.name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A rule needs a name — it is what the queue shows beside every application it routes.",
        )
    _assert_rule_has_a_condition(rule)
    db.add(rule)
    db.flush()
    _assert_rule_reachable(db, session, rule)
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="registration_rule", entity_id=rule.id, action="CREATED",
        before=None, after=_rule_snapshot(rule),
        event_type="registration.rule_created",
        payload={"name": rule.name, "auto_approve": rule.auto_approve},
    )
    db.commit()
    db.refresh(rule)
    return _rule_out(rule)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: str,
    body: RulePatch,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> RuleOut:
    """Edit a seating rule (B11.3).

    ABSENT AND NULL ARE DIFFERENT. An omitted field is left alone; an explicit
    `null` clears a condition, which is the only way a rule stops requiring a USN
    pattern. `model_fields_set` is what tells the two apart — `exclude_unset`
    would flatten the enum back to a string on the way — and the four columns
    that cannot hold NULL refuse it rather than ignoring it.

    REACHABLE BEFORE AND AFTER. A scoped author must already reach the rule, and
    must still reach it once the edit lands: without the second check, moving a
    rule's batch would be the way to hand it to another college — or to take it
    programme-wide, out of everybody's reach including the author's.
    """
    require_capability(db, session, "admin.registrations")
    rule = db.scalar(
        select(RegistrationRule).where(RegistrationRule.id == rule_id).with_for_update()
    )
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found.")
    _assert_rule_reachable(db, session, rule)
    provided = body.model_fields_set
    for field in _RULE_NOT_NULLABLE:
        if field in provided and getattr(body, field) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{field} cannot be cleared — leave it out to keep the value it has.",
            )
    before = _rule_snapshot(rule)
    if "name" in provided:
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A rule needs a name — it is what the queue shows beside every application it routes.",
            )
        rule.name = name
    if "enabled" in provided:
        rule.enabled = bool(body.enabled)
    if "email_domain" in provided:
        rule.email_domain = _validated_rule_domain(body.email_domain)
    if "usn_pattern" in provided:
        rule.usn_pattern = _validated_rule_pattern(body.usn_pattern)
    if "degree_level" in provided:
        rule.degree_level = body.degree_level
    if "cohort_id" in provided:
        rule.cohort_id = _validated_rule_cohort(db, body.cohort_id)
    if "auto_approve" in provided:
        rule.auto_approve = bool(body.auto_approve)
    if "priority" in provided:
        rule.priority = int(body.priority)
    _assert_rule_has_a_condition(rule)
    db.flush()
    _assert_rule_reachable(db, session, rule)
    after = _rule_snapshot(rule)
    if after == before:
        # Nothing changed — no audit row. A trail that records every no-op save
        # is a trail in which the one real edit is on page nine.
        db.commit()
        db.refresh(rule)
        return _rule_out(rule)
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="registration_rule", entity_id=rule.id, action="UPDATED",
        before=before, after=after,
        event_type="registration.rule_updated",
        payload={"name": rule.name, "auto_approve": rule.auto_approve},
    )
    db.commit()
    db.refresh(rule)
    return _rule_out(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: str,
    request: Request,
    session: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> None:
    """Delete a seating rule (B11.3).

    IT COSTS PROVENANCE, AND THE TRAIL IS WHAT PAYS FOR IT.
    `registrations.matched_rule_id` is `ON DELETE SET NULL`, so every application
    this rule routed loses the pointer that said which rule routed it — the
    queue's "Rule" column goes to "No rule matched" on rows a rule certainly did
    match. The audit row carries the whole rule and the number of applications
    that referenced it, which is the only place that fact survives.

    DISABLING IS THE REVERSIBLE ONE. `PATCH {"enabled": false}` stops a rule
    firing, keeps it on the screen where somebody can see why the intake behaved
    as it did, and keeps every `matched_rule_id` pointing at it. The client says
    so beside the button; this endpoint does not refuse the delete, because an
    office that has typed a rule by mistake should be able to remove it.
    """
    require_capability(db, session, "admin.registrations")
    rule = db.scalar(
        select(RegistrationRule).where(RegistrationRule.id == rule_id).with_for_update()
    )
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found.")
    _assert_rule_reachable(db, session, rule)
    routed = (
        db.scalar(
            select(func.count())
            .select_from(Registration)
            .where(Registration.matched_rule_id == rule.id)
        )
        or 0
    )
    before = _rule_snapshot(rule)
    record_change(
        db, session=session, request=request, tenant_id=None,
        entity_type="registration_rule", entity_id=rule.id, action="DELETED",
        before=before, after=None,
        event_type="registration.rule_deleted",
        payload={"name": rule.name, "applications_unlinked": routed},
    )
    db.delete(rule)
    db.commit()
    return None
