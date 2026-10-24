"""Request/response models for auth. Field names mirror the Next.js session
payload (camelCase) so the Angular client is unchanged across the cutover."""

from datetime import datetime

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginChallenge(BaseModel):
    """What /login answers instead of a session when OTP_REQUIRED is on: the
    password was right, a code has been emailed, and NO cookie was set. The
    client branches on `otp_required` and posts the code to /login/code."""

    otp_required: bool = True
    email: str
    expires_in_minutes: int


class LoginCodeRequest(BaseModel):
    email: str
    code: str


class FeatureFlagOut(BaseModel):
    """One student feature switch as the client renders it.

    The message travels WITH the boolean rather than in a parallel map, for the
    reason `governance.FeatureState` gives: the two come from the same winning
    override, and two maps can disagree.
    """

    enabled: bool
    #: The office's own words, and only when `enabled` is false. Null means they
    #: switched it off and chose not to explain, which is a real choice: the
    #: screen then simply is not there, and the client must not invent a reason.
    message: str | None = None


class SignInOut(BaseModel):
    """One row of "Recent sign-ins" on My account (B15).

    SUCCESSES ONLY. A failed attempt is the brute-force limiter's business, and
    putting failures on a screen the account holder reads turns "somebody
    mistyped their password" into an alarm. What this list answers is "was that
    me".
    """

    at: datetime
    #: `password`, `code`, `google` or `activation` - see auth.py's DOOR_*.
    door: str
    #: Behind a load balancer this is the balancer, for every row. Sent anyway,
    #: and the screen says where it came from rather than implying it is the
    #: person's own address.
    ip: str | None = None
    user_agent: str | None = None


class NotificationPrefOut(BaseModel):
    """One switch from auth.py's NOTIFICATION_PREFS catalogue."""

    enabled: bool
    label: str
    #: False means nothing reads this preference yet. The client shows it as
    #: "not wired yet" and PUT /api/auth/notification-prefs refuses to switch
    #: it off - the same rule B2.2 applies to an unwired feature switch, for
    #: the same reason: a preference that is stored and never read looks like
    #: one that works.
    enforced: bool


class SessionUser(BaseModel):
    userId: str
    email: str
    name: str
    role: str
    studentId: str | None = None
    mentorId: str | None = None
    # Resolved LIVE on every /login and /me from the role baseline plus any
    # grants — never carried in the JWT, so a revocation takes effect on the
    # next request rather than the next sign-in. The client renders its nav
    # and its route guards from this list; the API re-decides every call.
    capabilities: list[str] = []
    # The session token, IN THE BODY, for the phone app only (Ionic +
    # Capacitor). Set by /auth/login and /auth/login/code when the request's
    # Origin is listed in NATIVE_APP_ORIGINS, and None for every browser: a
    # browser keeps the httpOnly cookie, which JavaScript cannot read, and
    # putting the token here for it would undo that. The app stores it with
    # Capacitor Preferences and sends it back as `Authorization: Bearer`.
    session_token: str | None = None
    # B2.2. The student's feature switches, resolved on GET /api/auth/me and
    # EMPTY EVERYWHERE ELSE — including on /login, deliberately.
    #
    # The client reads its session from /me (AuthService.refresh()); /login
    # returns the same model because it mints the same session, not because the
    # two answers have to carry the same payload. Resolving eleven overrides on
    # the sign-in path would put a second multi-row read in front of the one
    # request a student makes when they are already waiting, to populate a field
    # the very next call replaces.
    #
    # `{}` therefore means "not asked", not "no features", and a client must
    # never read an absent key as a switched-off feature: absent is the default
    # state of this field on three of the four endpoints that return the model.
    # Every feature is ON unless a rule says otherwise, which is why the map is
    # dense — all eleven keys, with `enabled` — rather than a list of the ones
    # that are off.
    features: dict[str, FeatureFlagOut] = {}
    # ----------------------------------------------------------------- B15 --
    #
    # THREE FIELDS THAT ARE ONLY ANSWERED BY GET /api/auth/me, exactly like
    # `features` above and for the same reason: /login and the other endpoints
    # that return this model mint a session, they do not render My account, and
    # resolving three more reads on the sign-in path would populate fields the
    # very next call replaces.
    #
    # `None` is therefore "not asked" and NOT "no Google account is linked" -- a
    # client that reads the absent value as `false` will tell somebody their
    # Google sign-in is unlinked on the screen immediately after they used it.
    # That is why this is `bool | None` and not a `bool` defaulting to False.
    google_linked: bool | None = None
    last_sign_ins: list[SignInOut] = []
    notification_prefs: dict[str, NotificationPrefOut] = {}
