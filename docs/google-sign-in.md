# Google-only sign-in

Every role signs in with Google. There is no password field on the production
login screen, and **the roster is the access control**: after Google has proved
who the visitor is, the API looks that verified email up in the `users` table
and refuses anyone who is not already there. Nothing self-provisions.

```
browser  --->  accounts.google.com  --->  GET /api/auth/sso/google/callback
                                              |
                                     verify ID token (JWKS, aud, iss, exp,
                                     email_verified) + nonce + state
                                              |
                                     select User where email = <verified email>
                                              |
                          found -> the SAME reep_session cookie the app already uses
                          absent -> 302 /login?error=sso_not_enrolled
```

The session that comes out the far end is **byte-identical to the one password
login has always minted** — same HS256 JWT, same `AUTH_SECRET`, same claim names
(`userId, email, name, role, studentId?, mentorId?`), same cookie flags. That is
the whole reason this change is small: `get_current_session`, `require_mentor`,
`require_admin`, `_assert_can_access_student` and the WebSocket auth in
`routers/interview.py` were not touched and cannot tell the two paths apart.

| file | role |
|---|---|
| `apps/api-py/app/google_auth.py` | the OIDC layer: authorisation URL, code exchange, ID-token verification against Google's JWKS, state/nonce sealing |
| `apps/api-py/app/routers/auth.py` | the endpoints: `/auth/sso/*`, the roster lookup, the cookie, the streak write, and the password endpoint's production refusal |
| `apps/api-py/app/config.py` | `google_client_id`, `google_client_secret`, `google_redirect_uri`, `college_email_domain` + derived `@property` helpers |
| `apps/api-py/app/seed_roster.py` | the roster itself — production-safe, idempotent, no passwords |
| `apps/web/src/app/features/login/login.component.*` | the Google button, the capability probe, and the refusal messages |

---

## The flow, end to end

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant A as FastAPI — routers/auth.py
    participant G as accounts.google.com
    participant J as Google JWKS
    participant D as Postgres

    B->>A: GET /api/auth/sso/status
    A-->>B: {google_available, domain, reason} — button enabled only if google_available
    B->>A: GET /api/auth/sso/google?next=/student
    Note over A: state = token_urlsafe(32), nonce = token_urlsafe(32)
    A-->>B: 302 to Google + Set-Cookie: reep_oauth_state=(signed state/nonce/next)<br/>HttpOnly; Max-Age=600; Path=/; SameSite=lax
    B->>G: /o/oauth2/v2/auth?client_id&redirect_uri&scope=openid email profile<br/>&state&nonce&response_type=code
    G-->>B: the student picks their institutional account
    B->>A: GET /api/auth/sso/google/callback?code=…&state=…
    Note over A: the state cookie must exist AND match the query param,<br/>then the cookie is deleted — single use
    A->>G: POST oauth2.googleapis.com/token (code, client_id, client_secret, redirect_uri)
    G-->>A: {id_token, access_token}
    A->>J: GET /oauth2/v3/certs (cached signing keys)
    J-->>A: RSA public keys
    Note over A: jwt.decode(id_token, RS256, audience=client_id,<br/>issuer=accounts.google.com) + exp + nonce + email_verified
    A->>D: select(User).where(User.email == claims["email"].lower())
    alt on the roster
        D-->>A: User row
        A->>D: last_login_at = now; LoginDay upsert; commit
        A-->>B: 302 to next + Set-Cookie: reep_session=(HS256 JWT)<br/>HttpOnly; Max-Age=43200; Path=/; SameSite=lax
        B->>A: GET /api/auth/me (the SPA cold-boots, authGuard calls refresh())
        A-->>B: 200 SessionUser
    else not on the roster
        D-->>A: None
        A-->>B: 302 /login?error=sso_not_enrolled
    end
```

Two details in that picture carry more weight than they look:

**The callback ends in a 302, not JSON.** The browser arrives by top-level
navigation, so the response has to be something a browser can render. Landing on
an app path makes the SPA cold-boot; `authGuard` finds an empty in-memory session,
calls `AuthService.refresh()` → `GET /api/auth/me`, and the cookie set one
redirect earlier answers it. That is the existing hard-refresh path, which is why
the guard needed no change.

**The streak write is not optional.** `GET /api/student/dashboard` computes the
login streak from `LoginDay` rows, so a Google login that skipped the
`last_login_at` + `LoginDay` upsert would produce a perfectly good session and a
streak frozen at whatever it was — silent, and only noticed weeks later.

---

## Endpoints

| endpoint | purpose | answers |
|---|---|---|
| `GET /api/auth/sso/status` | capability probe for the login screen | `200 {google_available, password_login_available, domain, reason}` — never a 4xx, so the client can show *why* |
| `GET /api/auth/sso/google?next=/path` | start the flow | `302` to Google, plus the state cookie |
| `GET /api/auth/sso/google/callback?code&state` | finish it | `302` to `next` with `reep_session` set, or `302 /login?error=…` |
| `POST /api/auth/login` | password — dev/CI only | `200` in dev, **`403` when `ENV=prod`** |
| `GET /api/auth/me` | unchanged | `200 SessionUser` / `401` |
| `POST /api/auth/logout` | unchanged | `200 {ok:true}` |

`/api/auth/sso/status` exists for the same reason `/api/interview/status` and
`/api/interview/status` do: a Google button rendered live with no
`GOOGLE_CLIENT_ID` configured reproduces the "why is voice broken" report this
codebase already has a runbook for. Blank credentials ⇒ `google_available:false` ⇒ the
button renders disabled with the reason underneath.

### Every redirect-back error code

| `?error=` | meaning | who caused it |
|---|---|---|
| `sso_config` | `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` blank | deployment |
| `sso_denied` | the student cancelled at Google, or Google returned `error=access_denied` | student |
| `sso_state` | no state cookie, or it did not match the `state` query param | an expired tab, or an attack (see below) |
| `sso_token` | the code exchange failed — config, network, or a spent code | network / config — check the API log |
| `sso_identity` | the ID token did not verify (signature, `aud`, `iss`, `exp`, `nonce`) | network / config — check the API log |
| `sso_unverified_email` | the ID token's `email_verified` was not `true` | the Google account |
| `sso_not_enrolled` | verified identity, no matching `users` row | roster |
| `sso_disabled` | verified identity, but the Main Admin has disabled the account (a removed account still lands on `sso_not_enrolled`) | the placement office |
| `sso_failed` | the backstop — anything not enumerated above | check the API log |

These eight-plus-one strings are a **three-way contract**: the callback in
`app/routers/auth.py` emits them, `messageFor` in
`apps/web/src/app/features/login/login.component.ts` turns each into a sentence,
and this table documents them. They were once three independent vocabularies
with **no code in common**, so every refusal rendered as "the reason given is not
one this page knows" and every written message was unreachable. Nothing in
`pytest`, `tsc` or `ng build` can see that, which is why
`apps/api-py/tests/test_sso_contract.py` compares the lists directly — add a code
in one place and it fails.

---

## What is verified, and what each check stops

The ID token is verified **as a JWT against Google's published keys**. A
`userinfo` response is not used as proof of identity anywhere in this flow: it is
an HTTP body, and an HTTP body is only as trustworthy as the channel that carried
it, whereas a signature is checkable on its own.

| check | what it stops |
|---|---|
| RS256 signature against `https://www.googleapis.com/oauth2/v3/certs` | a forged or edited token; the algorithm is pinned, so nothing can downgrade it to `none` or to HS256-with-a-public-key |
| `aud == GOOGLE_CLIENT_ID` | **token replay from another app.** An ID token minted for someone else's Google client is a valid, correctly signed Google token — it just is not for us. Without the `aud` check, any site the student ever signed into could hand us its token and log in as them |
| `iss` ∈ `{accounts.google.com, https://accounts.google.com}` | a token from a different issuer entirely. Google publishes both spellings; both are accepted, nothing else is |
| `exp` in the future (enforced by PyJWT, with 60s of clock skew allowed) | a stale token pulled out of a log or a proxy cache |
| `email_verified is True` | **an unverified `email` claim is not an identity.** It is a string the account holder typed. Since the roster is keyed on email, accepting an unverified one would let anyone who can create a Google account claim a student's row |
| `nonce` matches the one sealed at the start | an ID token obtained elsewhere and injected into this login |
| `state` matches the state cookie, then the cookie is deleted | **login CSRF** — see below |

### Why `state`, specifically

Without it, an attacker completes a Google login *as themselves*, keeps their own
`code`, and then gets the victim's browser to follow
`/api/auth/sso/google/callback?code=<attacker's code>`. The victim's browser
quietly receives a `reep_session` for the **attacker's** account, and everything
the victim then does — uploads a resume, writes profile notes — lands in the
attacker's account, where the attacker reads it at leisure. It is CSRF pointed
the unusual way round: the session is planted, not stolen.

`state` stops it because the callback only proceeds when the `state` in the URL
matches a value this server generated *for this browser* and stored in a cookie
the attacker cannot write. The cookie is deleted before the code is exchanged, so
a replayed callback URL fails on the second use.

The state cookie is `SameSite=Lax`, deliberately, and **must not be `Strict`**:
the callback navigation originates at `accounts.google.com`, and a `Strict`
cookie is not sent on a cross-site navigation at all. It would fail 100% of the
time, in a way that reads as "Google is broken". `Lax` sends cookies on top-level
cross-site GETs, which is exactly this hop and no other. Its other flags copy the
session cookie's: `httponly=True`, `secure=settings.is_prod`, `path="/"`, and a
10-minute `max_age`, because a login that takes longer than that has been
abandoned.

### Dependency note

RS256 verification needs `cryptography` — PyJWT alone raises *"Algorithm 'RS256'
could not be found. Do you have cryptography installed?"*. It is pinned in
`apps/api-py/requirements.txt` alongside `pyjwt`. If a rebuilt image starts
answering `sso_failed` on every login with that message in the log, that pin was
dropped.

---

## Environment variables

All in `apps/api-py/.env` — the one file every process in this repo reads. Blank
means the feature is off, the same convention every other optional integration
in `config.py` follows.

| variable | default | blank means | where it comes from |
|---|---|---|---|
| `GOOGLE_CLIENT_ID` | `""` | SSO unavailable | Google Cloud console → **APIs & Services → Credentials** → your OAuth 2.0 Client ID. Ends `.apps.googleusercontent.com` |
| `GOOGLE_CLIENT_SECRET` | `""` | SSO unavailable | the same credential. Shown in full only at creation; afterwards use **Add secret** or download the JSON. Starts `GOCSPX-` |
| `GOOGLE_REDIRECT_URI` | `""` | derived: `WEB_ORIGIN` + `/api/auth/sso/google/callback` | you compose it — and it must byte-match what is registered in the console |
| `ROSTER_EMAIL_DOMAIN` | first `GOOGLE_ALLOWED_DOMAIN` entry, else `bgscet.ac.in` | the default is used | your institution's Google Workspace domain — the part after `@` in student mail. `COLLEGE_EMAIL_DOMAIN` is accepted as an alias |
| `WEB_ORIGIN` | `http://localhost:4200` | — | existing setting. Also the CORS origin. In production this is the public site origin |
| `AUTH_SECRET` | dev placeholder | — | existing setting. Signs `reep_session` **and** the state/nonce cookie. Rotating it signs everyone out |
| `ENV` | `dev` | — | existing setting. `prod` turns on `Secure` cookies **and** makes password login refuse |

`GOOGLE_CLIENT_SECRET` is a credential. It never leaves the API process, is never
logged, and is not needed by the Angular app — the authorisation-code flow keeps
it server-side, which is why this is a redirect flow and not a browser-side
Google Identity Services widget.

### Getting the credential — Google Cloud console

1. <https://console.cloud.google.com> → pick or create a project (one project per
   deployment is fine; credentials are per-project).
2. **APIs & Services → OAuth consent screen.** Choose **Internal** if the project
   lives in the institution's Google Workspace — Internal restricts sign-in to
   `@bgscet.ac.in` accounts at Google's end, which is a useful second fence in
   front of the roster. Choose **External** only if it must accept accounts
   outside the Workspace; an External app stays in *Testing* until published, and
   in Testing **only listed test users can sign in** (a very common cause of a
   student seeing "access blocked").
3. Scopes: `openid`, `email`, `profile`. Nothing else. These are non-sensitive
   and need no Google verification review. Do not add Drive, Calendar or
   directory scopes — they trigger a review and they buy nothing here.
4. **Credentials → Create credentials → OAuth client ID → Application type: Web
   application.** Name it after the deployment (`REEP dashboard — production`).
5. **Authorised redirect URIs → Add URI** — the exact string from the next
   section. **Authorised JavaScript origins** can stay empty; this flow performs
   no browser-side token request.
6. **Create.** Copy the Client ID and Client secret into `apps/api-py/.env`,
   restart uvicorn (settings are read at import time, and `--reload` is
   unreliable on Windows here — kill port 3300 and restart), then confirm
   `GET /api/auth/sso/status` reports `google_available: true`.

---

## The authorised redirect URI — exact strings

Register **the URI the browser's address bar will show at the callback**, not the
one the API sees internally. Google compares it character for character: scheme,
host, port, path, trailing slash.

```
dev         http://localhost:4200/api/auth/sso/google/callback
production  https://<your-host>/api/auth/sso/google/callback
```

Add both to the same client if one project serves both, or keep a separate client
per environment — cleaner, because rotating a dev secret then cannot lock out
production.

**The dev-proxy trap.** In dev the browser is on `http://localhost:4200`, and
`apps/web/proxy.conf.json` forwards `/api` to `http://localhost:3300` with
`"changeOrigin": true`. FastAPI therefore sees `Host: localhost:3300`, so a
redirect URI derived from the inbound request (`request.url_for(...)`) would come
out as `http://localhost:3300/api/auth/sso/google/callback` — an origin the
browser is not on and the console does not have. That is why the redirect URI is
**configuration, not inference**: it is `GOOGLE_REDIRECT_URI` when set, otherwise
`WEB_ORIGIN` + the callback path, and the inbound request never contributes.

Google permits plain `http` for `localhost`, and only for localhost. Production
must be `https` — and has to be, since `ENV=prod` marks the session cookie
`Secure`, and a `Secure` cookie set over plain http is dropped on the floor.

---

## The roster allowlist

The allowlist is not a separate table or a list in a config file — **it is the
`users` table**. The callback does one lookup:

```python
user = db.scalar(select(User).where(User.email == email))   # email already lowercased
```

`None` ⇒ refused. This is what makes the roster load-bearing rather than
decorative: a Google account that is not already a REEP user cannot log in,
cannot create itself, and gets no session. It also means role assignment is never
guessed — a user's `role` comes from the row that was seeded for them, which is
what AGENTS.md rule 2 requires (guessing a role at provisioning time is a
data-exposure bug, not a UX one).

The 33 MBA students are created by:

```
cd apps/api-py
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m app.seed_roster
```

`app.seed_roster` is **production-safe**, following the `app.seed_kb` precedent:
idempotent (keyed on the derived email, which is `UNIQUE`, and cross-checked on
`Student.usn`, also `UNIQUE`), safe to re-run, and it creates **no passwords**.
It is a separate module from `app.seed` precisely because `app.seed` creates demo
accounts whose passwords are published in `AGENTS.md` and refuses outright when
`ENV=prod`.

Per roster row it writes three rows — `User` (role `STUDENT`), its `Student`
(carrying the USN), and an empty `StudentProfile`. The `Student` row is not
garnish: `_payload_for()` only puts `studentId` in the session JWT when
`user.student` exists, and without it every `/api/student/*` route answers 403 to
a user who logged in perfectly.

The `password_hash` column is `NOT NULL`, so the seed writes a deliberately
**unusable** sentinel — a value that can never satisfy `verify_password`, which
requires exactly `scrypt:<salt>:<digest>`. The effect is that these accounts have
no password at all: `POST /api/auth/login` returns the same generic 401 it
returns for an unknown email, and they are reachable only through Google.

### What a refused student sees

The callback logs the cause and the status code, then redirects:

```
WARNING sso refused: someone@gmail.com is not on the roster -> 302 /login?error=sso_not_enrolled
```

and the login screen renders, in its existing `.alert--error` block:

> **That Google account is not on the programme roster.** Sign-in is limited to
> students and staff the placement office has enrolled. If you used a personal
> Gmail account, try again with your college account. If you used your college
> account, ask the placement office to add your USN.

The message deliberately does not say whether that email exists in REEP — it says
the *account* is not on the roster either way — so the endpoint is not an
enumeration oracle for who is enrolled.

### Adding a student

1. Add `USN,Name` to the roster in `app/seed_roster.py` (or to the CSV it reads,
   if you are running it with a file argument).
2. Re-run `.venv/Scripts/python -m app.seed_roster`. It is idempotent: existing
   students are left untouched, and it prints how many were added.
3. Tell the student to sign in with their institutional Google account. Their USN
   is already on their profile — the Identity card on `/student/profile` is
   read-only and populated from the seeded `Student.usn`; they never type it.

### Removing a student

Removal means "no Google identity maps to a row any more". Two options, and the
first is usually right:

```sql
-- disable: keeps all history, blocks sign-in immediately
update users set email = email || '.disabled' where email = '1mp25mdm07@bgscet.ac.in';
```

The derived email no longer matches anything Google can present, so the next
attempt lands on `sso_not_enrolled`. Existing sessions survive up to 12 hours — the
JWT is stateless and is not re-checked against the database on each request (see
`get_current_session`); if that matters, rotate `AUTH_SECRET`, which signs
everybody out at once.

```sql
-- delete: destroys the student's uploads, conversations, marks and profile
delete from users where email = '1mp25mdm07@bgscet.ac.in';
```

Only do this for a row created in error. It is not reversible, and several child
tables cascade.

---

## The email convention, and how to change it

The seed derives each student's email from their USN:

```
1MP25MDM01  ->  1mp25mdm01@bgscet.ac.in
```

USN lowercased as the local part, `@`, then `ROSTER_EMAIL_DOMAIN` (alias
`COLLEGE_EMAIL_DOMAIN`; blank falls back to the first `GOOGLE_ALLOWED_DOMAIN`
entry). The USN
itself is stored **uppercase** on `Student.usn` (the registration rules' patterns
are uppercase-anchored, e.g. `^1BG2[0-9]MBA[0-9]{3}$`), and the lowercasing
happens only when building the address. The precedent is already in the repo:
`app/seed.py` seeds `Registration(email="1bg24mba045@bgscet.ac.in",
usn="1BG24MBA045")`.

**This convention is a guess about someone else's mail system, and if it is wrong
every student is locked out at once** — their real Google address would find no
row, and they would all see `sso_not_enrolled`. So the fix must never require a code
change:

- **Different domain** (say the college uses `bgscet.edu.in`, or a `students.`
  sub-domain): set `ROSTER_EMAIL_DOMAIN=bgscet.edu.in` in `apps/api-py/.env`,
  then re-run the seed **naming the old domain** so it moves the existing rows
  instead of reporting 33 USN conflicts and writing nothing:

  ```
  python -m app.seed_roster --rekey-domain bgscet.ac.in --dry-run   # look first
  python -m app.seed_roster --rekey-domain bgscet.ac.in
  ```

  `--rekey-domain` moves **only** rows this seed created and nothing has happened
  to since — the unusable-password sentinel, role `STUDENT`, and an address that
  is exactly what the OLD domain derives for that USN. An account with a real
  password, a promoted role, or an address a human chose is somebody's identity,
  not a misconfiguration, so it stays a conflict for a human to look at.

- **A completely different local part** (`firstname.lastname@`, or a
  staff-issued address unrelated to the USN): the derivation is one function,
  `email_for(usn, domain)` in `app/seed_roster.py`, and it is the only place an
  address is composed. Change it there, or bypass it entirely by feeding the seed
  a CSV that carries the real address per row. The rest of the flow does not care
  where the address came from — the callback matches on whatever is in `users`.

Before rolling this out to a batch, verify the convention with **one** real
account: seed that student, sign in as them, and only then run the rest. One
successful login is worth more than any amount of reasoning about the domain.

---

## Password login: kept, and refused in production

`POST /api/auth/login` still exists. It is guarded, not deleted:

```python
# Dev/CI affordance. 13 of 18 test files — and the shared `login` fixture in
# tests/conftest.py — authenticate through this endpoint; deleting it would take
# the DB-backed suite and CI with it. Production has exactly one way in, and it
# is Google. Same guard shape, and the same reasoning, as app/seed.py.
if settings.is_prod:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Password sign-in is disabled. Use Sign in with Google.",
    )
```

Consequences worth knowing:

- **In dev and CI nothing changed.** `tests/conftest.py`'s `login` fixture, the
  eight test files that take it, and `test_conversations.py`'s inline login all
  keep passing unmodified. The seeded `student@ / mentor@ / admin@` logins
  still work locally.
- **In production it is a hard 403**, with a message that names the alternative.
  The production login screen does not render the password form at all; the 403
  is the backstop for anyone posting directly.
- **It is not an account oracle.** For roster students the endpoint would have
  returned the generic 401 anyway (their password hash is an unusable sentinel),
  so the 403 leaks nothing about which accounts exist.

There is no override flag, for the same reason `app/seed.py` has none: an escape
hatch would be found and used, and every path through it ends with password
authentication live in production against accounts nobody ever set a password on.

---

## The session is unchanged — and must stay that way

The callback mints the session through the **same two calls** password login
uses, `_payload_for(user)` and `create_session_token(payload)`, and sets the
cookie with the **same six keyword arguments**:

```python
response.set_cookie(
    SESSION_COOKIE, token,
    httponly=True, samesite="lax", secure=settings.is_prod,
    path="/", max_age=SESSION_TTL_SECONDS,
)
```

Copying those six is what keeps `Secure` tied to `ENV` in one place. No claim was
added: `userId`, `email`, `name`, `role`, plus `studentId`/`mentorId` when the
user has those rows, plus PyJWT's `iat`/`exp`. There is no `sub`, `iss` or `aud`
on **our** session — those are checks we apply to *Google's* token, not claims we
issue. The Angular `SessionPayload` interface, `HOME_FOR_ROLE`, the assistant's
`role === 'STUDENT'` gate and the resume builder's locked email field all read
this shape; a renamed claim breaks every one of them silently.

The `next` parameter is re-validated **server-side** in the callback — it must
start with `/` and must not start with `//` — because the client-side check that
normally does this is bypassed entirely in a redirect flow. That check is the one
thing standing between this endpoint and an open redirect.

---

## Troubleshooting

### `Error 400: redirect_uri_mismatch`

Google's own error page, shown before the callback is ever reached. It means the
`redirect_uri` the API sent is not, character for character, one of the
Authorised redirect URIs on that client.

1. Read the URI off Google's error page (expand **Error details**) — it prints
   exactly what was sent.
2. Compare it with **APIs & Services → Credentials → your client → Authorised
   redirect URIs**. The usual culprits: `http` vs `https`, port `4200` vs `3300`,
   a trailing slash, `127.0.0.1` vs `localhost` (Google treats those as different
   strings), or `www.` on one side only.
3. If the sent URI names port **3300** in dev, `GOOGLE_REDIRECT_URI` is unset
   *and* something is deriving it from the inbound request — see the dev-proxy
   trap above. Set it explicitly:
   `GOOGLE_REDIRECT_URI="http://localhost:4200/api/auth/sso/google/callback"`.
4. Console changes can take a few minutes to propagate. Fix it, wait, then retry
   in a fresh tab.

### The student's email is not verified

Symptom: the student completes Google's screen and lands on
`/login?error=sso_unverified_email`; the API log has
`WARNING sso refused: email_verified is false for <email> -> 302 /login?error=sso_unverified_email`.

The ID token arrived with `email_verified: false`. We reject it because the roster
is keyed on email, and an unverified email claim is just a string the account
holder typed — accepting it would let anyone who can create a Google account
claim a student's row.

Almost always this is a **personal Gmail** account that has not completed
verification, or a non-Google address attached to a Google account. The fix is on
the student's side: sign in with the institutional Workspace account, whose email
is verified by the domain administrator rather than by the user. If a genuinely
institutional account reports `email_verified: false`, that is a Workspace
configuration problem for the domain admin — do not work around it here.

### "I am on the roster but it says I am not"

Symptom: `/login?error=sso_not_enrolled`, and the log names the address Google
actually presented. **That logged address is the answer** — compare it with what
is in the database:

```sql
select u.email, u.role, s.usn from users u
  left join students s on s.user_id = u.id
 where u.email ilike '%mdm07%';
```

Three causes, in order of likelihood:

1. **They used a personal Gmail.** The log shows `something@gmail.com`. Tell them
   to use the college account. Nothing to fix server-side.
2. **The derivation is wrong.** The log shows a real institutional address that
   is not what the seed built — a different domain, or a local part that is not
   the USN. See *The email convention, and how to change it*; if the log shows a
   pattern the whole batch shares, fix it once for everyone rather than
   hand-editing rows.
3. **The seed never ran, or ran against another database.** Check the count:
   `select count(*) from students where usn like '1MP25MDM%';` should be 33.
   Confirm `DATABASE_URL` points where you think it does, then re-run
   `python -m app.seed_roster` — it is idempotent.

Note that USNs **11, 23 and 30 do not exist** in this batch; the sequence skips
them. A student is not missing because their number is absent.

### Other things that go wrong

| symptom | cause | fix |
|---|---|---|
| The Google button is disabled with "sign-in is not configured" | `GOOGLE_CLIENT_ID`/`SECRET` blank, or uvicorn was not restarted after editing `.env` (settings are read at import) | set them, kill port 3300, restart, re-check `GET /api/auth/sso/status` |
| `/login?error=sso_state` every single time | the state cookie is not coming back — usually `SameSite=Strict` instead of `Lax`, or a `Secure` state cookie on a plain-http dev origin | it must be `Lax`, and `secure` must track `settings.is_prod` and nothing else |
| `/login?error=sso_state` occasionally | a login tab left open past the cookie's 10 minutes, or the back button replaying a used callback (the cookie is deleted after one use — by design) | start again from `/login` |
| `/login?error=sso_failed`, log says *"Algorithm 'RS256' could not be found"* | `cryptography` missing from the built image | it belongs pinned next to `pyjwt` in `requirements.txt`; rebuild |
| Signed in fine, then every `/api/student/*` call is 403 | the `User` has no `Student` row, so the session carries no `studentId` | re-run `python -m app.seed_roster`, which creates `User` + `Student` + `StudentProfile` together |
| Google says "Access blocked: … has not completed the Google verification process" | an **External** consent screen still in *Testing*, and this account is not a listed test user | switch the consent screen to **Internal** (correct for a Workspace institution), or add the tester |
| Signed in, but the login streak stopped counting | the `LoginDay` write was skipped on the SSO path | the callback must do the same `last_login_at` + `LoginDay` upsert as password login; check `select max(day) from login_days where user_id = …` |
| Everyone signed out at once, with no deploy | `AUTH_SECRET` changed, or differs between replicas | it signs both `reep_session` and the state cookie; it must be identical everywhere and stable |

---

## First-deploy checklist

1. `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` set in
   `apps/api-py/.env`; `WEB_ORIGIN` is the real public origin; `ENV=prod`;
   `AUTH_SECRET` is a real random value, not the dev placeholder.
2. The production redirect URI is registered in the console, on **https**.
3. `python -m alembic upgrade head`, then `python -m app.seed_kb`, then
   `python -m app.seed_roster`. **Not** `python -m app.seed` — it refuses under
   `ENV=prod`, and that refusal is the point.
4. `GET /api/auth/sso/status` reports `google_available: true`.
5. Sign in as one real student, end to end. Confirm their USN shows on
   `/student/profile` without them typing it, and that
   `select count(*) from login_days where day = current_date;` moved.
6. `POST /api/auth/login` answers **403** in production.
