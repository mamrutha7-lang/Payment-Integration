# REEP as a phone app (Android + iOS)

The phone app is **the same Angular website**, packed into an Android/iOS app
by **Capacitor** and drawn with **Ionic** components so it looks like a phone
app. Nothing was rewritten: every screen, service, API call and route is the
website's own code.

```
apps/web/
  src/                    the website (unchanged screens)
  src/app/native/         code that runs ONLY inside the app
  src/app/layout/mobile-shell.*   the app's frame: menu, header, bottom tabs
  src/environments/native.ts      which server the app talks to   <- you edit this
  src/ionic.scss          Ionic's styles (loaded only inside the app)
  capacitor.config.ts     app name, app ID, splash screen, status bar
  resources/              the big source images for the icon and splash
  android/                the Android Studio project (generated, committed)
  ios/                    the Xcode project (generated, committed; needs a Mac)
  scripts/build-apk.mjs   `npm run mobile:apk`
```

---

## 1. One-time setup on the server

The app is a different "site" from the website as far as the server is
concerned (the app runs at `https://localhost` on the phone). Tell the API to
accept it — one line in `apps/api-py/.env` (or, in production, the API's
environment variables):

```
NATIVE_APP_ORIGINS="https://localhost,capacitor://localhost"
```

Restart the API. Without this line the app opens, but every screen says it
could not load.

## 2. One-time setup on your computer

1. **Node.js 24** — https://nodejs.org (the "LTS" button). Angular needs
   22.22.3 or newer.
2. **Android Studio** — https://developer.android.com/studio. Install it with
   the default options. The first time it opens it shows a "Setup Wizard":
   click **Next** on every page, accept the licences, and let it download (it
   installs the Android SDK and Java for you — about 3 GB).
3. In a terminal:
   ```
   cd apps/web
   npm ci
   ```

## 3. Point the app at your server

Open `apps/web/src/environments/native.ts` and fill in your REEP address, the
same one you open the website on, **no `/api` and no `/` at the end**:

```ts
export const native = {
  apiOrigin: 'https://reep.example.edu',
};
```

It must be `https://`. Android blocks the app from calling a plain `http://`
address (see "Why something works on the web but not in the app" below).

## 4. Run it on your phone

**Prepare the phone (once):**

1. On the phone: **Settings → About phone** → tap **Build number** seven times
   until it says "You are now a developer".
2. **Settings → System → Developer options** → turn on **USB debugging**.
3. Plug the phone into the computer with a USB cable. On the phone, a box asks
   "Allow USB debugging?" → tick **Always allow** → **Allow**.

**Every time:**

```
cd apps/web
npm run mobile:android
```

That builds the website, copies it into the Android project, and opens it in
Android Studio. In Android Studio:

1. Wait until the bar at the bottom stops saying "Gradle sync…" (first time:
   a few minutes).
2. At the top, in the device drop-down next to the green ▶ button, pick your
   phone's name.
3. Click the green **▶ Run** button. The app installs and opens on the phone.

Shortcut once that works: `npm run mobile:run` builds and installs straight
onto the plugged-in phone without opening Android Studio (it asks you to pick
the device in the terminal).

**After you change code:** run `npm run mobile:android` again (or
`mobile:run`) — the phone only gets what was built.

## 5. Build an APK (a file you can send to any Android phone)

**Option A — on your computer:**

```
cd apps/web
npm run mobile:apk
```

The file is `apps/web/android/app/build/outputs/apk/debug/app-debug.apk`.
Copy it to a phone (WhatsApp, Google Drive, USB) and open it there. Android
asks to allow "Install unknown apps" for the app you opened it from — allow
it once.

**Option B — Android Studio's menus:** **Build → Generate App Bundles or APKs
→ Generate APKs**. When it finishes, a box in the corner says "APK(s)
generated" → click **locate**.

**Option C — on GitHub, no Android Studio at all:** **Actions → "Android APK
(debug)" → Run workflow**, type your server address, press **Run workflow**.
After ~10 minutes, open the finished run and download **reep-debug-apk**.
(GitHub only shows this button once the workflow file is on the `main`
branch.)

These are **debug** APKs: right for testing, not for the Play Store. The Play
Store gets a signed *release* bundle, built by GitHub — see
[8. Publish to Google Play](#8-publish-to-google-play).

## 6. iOS

Building for iPhone needs a **Mac with Xcode** (Apple's rule). On the Mac:

```
cd apps/web
npm ci
npm run mobile:ios
```

Xcode opens. Click **App** in the left list → **Signing & Capabilities** →
pick your Apple ID under **Team** → choose your iPhone at the top → ▶.

## 7. Changing the icon or the splash screen

The source images are in `apps/web/resources/` (`icon-only.png`,
`icon-foreground.png`, `icon-background.png`, `splash.png`). Replace them with
your own (same sizes), or redraw REEP's with
`python tools/icons/make-native-icons.py`, then:

```
cd apps/web
npm run mobile:icons
```

## 8. Publish to Google Play

The app is published **from the `feature/mobile-app` branch only**. `main` never
builds or ships it. Every push to `feature/mobile-app` that changes the app
runs **Actions → "Android app to Google Play"**
(`.github/workflows/android-play.yml`). It builds a release bundle (`.aab`),
signs it with your upload key, attaches it to the run as **reep-play-aab**,
and, once you switch it on, uploads it to Google Play's **internal testing**
track and rolls it out straight away.

**Internal testing has no restrictions to wait on.** Google does not hold an
internal test for its standard review, a new version reaches your testers
within minutes, and the 12-tester, 14-day rule for new personal accounts
applies only before **production** (see "Before the app can be public"). It
takes up to **100 testers**, each added by their Google account email, who
install the app from Google Play through an opt-in link.

### One-time setup, in this order

1. **A Google Play developer account**: https://play.google.com/console/signup
   (a one-time US$25 fee; Google checks your identity, which takes a few days).

2. **Create the app** in Play Console → **Create app**: name *REEP*, *App*,
   *Free*. Its package name is decided by the first upload and can never change
   afterwards: `in.ac.bgscet.reep` (`capacitor.config.ts` and
   `android/app/build.gradle`). Change both before step 5 if the college wants
   a different one.

3. **Make your upload key** on your own computer (Java comes with Android
   Studio):
   ```
   keytool -genkeypair -v -keystore reep-upload.jks -alias reep-upload -keyalg RSA -keysize 2048 -validity 10000
   ```
   It asks for a password and a name. Keep `reep-upload.jks` and its password
   in a password manager, **never in the repository** (`android/.gitignore`
   refuses `*.jks` for that reason). Google keeps the key that signs what phones
   install (Play App Signing); this one only proves an upload came from you.
   Google can replace a lost upload key, but only after you ask them.

   Turn it into text for GitHub:
   - macOS: `base64 -i reep-upload.jks -o reep-upload.b64`
   - Linux: `base64 -w0 reep-upload.jks > reep-upload.b64`
   - Windows PowerShell:
     `[Convert]::ToBase64String([IO.File]::ReadAllBytes("reep-upload.jks")) | Out-File reep-upload.b64 -Encoding ascii`

4. **The `play-store` environment** on GitHub: **Settings → Environments → New
   environment** → `play-store`.
   - **Deployment branches and tags** → *Selected branches and tags* → add
     `feature/mobile-app`. This is what keeps every other branch away from the
     secrets below.
   - **Required reviewers**, if your GitHub plan shows it: tick yourself, and
     every release waits for your **Approve**.
   - **Environment secrets**:

     | Name | Value |
     |---|---|
     | `ANDROID_UPLOAD_KEYSTORE_BASE64` | everything in `reep-upload.b64` |
     | `ANDROID_UPLOAD_KEYSTORE_PASSWORD` | the keystore password |
     | `ANDROID_UPLOAD_KEY_ALIAS` | `reep-upload` |
     | `ANDROID_UPLOAD_KEY_PASSWORD` | the key password (the same as the keystore's unless you typed a different one) |

   - **Environment variables**: `REEP_API_ORIGIN` = your REEP server,
     `https://…`, no `/` at the end (the same address as section 3, "Point
     the app at your server").

   Then delete `reep-upload.b64` (keep the `.jks`).

5. **The first bundle goes up by hand**, because Google does not let its API
   upload an app's very first one. Push to `feature/mobile-app` (or re-run the
   latest "Android app to Google Play" run). When it is green, open it,
   download **reep-play-aab** and unzip it. In Play Console: **Test and release
   → Testing → Internal testing → Create new release** → accept **Play App
   Signing** → upload `app-release.aab` → **Next** → **Save and publish**.
   Then **Testers** → create an email list with your testers' Google account
   addresses (up to 100) → **Save**, and send them the **Copy link** opt-in
   link. Each tester opens it once on their phone, accepts, and installs REEP
   from Google Play.

6. **Let GitHub upload every later one.**
   - https://console.cloud.google.com → pick or create a project → **APIs &
     Services → Library** → enable **Google Play Android Developer API**.
   - **IAM & Admin → Service accounts → Create service account** (name it
     `reep-play-upload`, give it no roles) → open it → **Keys → Add key → JSON**.
     A file downloads.
   - Play Console → **Users and permissions → Invite new users** → paste the
     service account's email (`…@….iam.gserviceaccount.com`) → **App
     permissions** → add REEP → tick **Release apps to testing tracks** (tick
     the production release permission only if GitHub should be able to upload
     to production too) → **Invite user**.
   - GitHub, `play-store` environment: secret `PLAY_SERVICE_ACCOUNT_JSON` = the
     whole JSON file; variable `PLAY_UPLOAD` = `true`. Delete the JSON file from
     your computer.

From then on every push to `feature/mobile-app` that touches `apps/web` rolls a
new version out to your internal testers, and their phones update it from
Google Play like any other app. Nothing to press. (Only if Google still treats
the app as never released does it accept a **draft** instead; the run's summary
says so, and pressing **Start rollout** once in Play Console ends that.)

### The environment's variables

| Variable | When not set | What it does |
|---|---|---|
| `REEP_API_ORIGIN` | the run stops | the server the app talks to |
| `PLAY_UPLOAD` | build and sign only | `true` uploads to Google Play; anything else attaches the bundle to the run and uploads nothing |
| `PLAY_TRACK` | `internal` | `internal`, `alpha` (closed testing), `beta` (open testing) or `production` |
| `PLAY_RELEASE_STATUS` | `completed` on `internal`, `draft` on every other track | `completed` sends it to that track at once; `draft` waits for **Start rollout** |

Each run numbers its bundle `1.0.<run number>`, with version code
`run number × 100 + attempt`, so the numbers only grow and a re-run never
collides. Google refuses a version code it has seen before.

### Before the app can be public

This is the list for **production**, where anybody can find REEP on Google
Play; internal testing does not wait on the closed test below. If Play Console
asks for one of these declarations before your first internal release, fill it
in there. Its **Dashboard** lists what Google needs before a production
release: a **privacy policy** web page (the app shows students' marks and
attendance, so it needs a real one), the **Data safety** form, a **content
rating**, the **target audience**, and the store listing's screenshots and
description. A **personal** developer account made after November 2023 must
also run a **closed test with at least 12 testers for 14 days** before Google
allows production. An organisation account (the college's) does not have that
rule. The server also needs `NATIVE_APP_ORIGINS` (section 1), or the published
app cannot load anything.

### Who can publish

The workflow's first job refuses a push by anyone but the repository owner,
and refuses every branch but `feature/mobile-app`. That job is part of the
workflow file, though, and a collaborator with write access could push an
edited copy of that file to `feature/mobile-app`. What actually closes that
door is one of: adding `feature/mobile-app` to the **Lead review** ruleset
(**Settings → Rules → Rulesets**), so only you can update the branch; or the
**Required reviewers** box in step 4.

---

## What changed, in plain words

| You asked for | Where it is |
|---|---|
| Ionic + Capacitor added, nothing rewritten | `package.json`, `capacitor.config.ts`, `android/`, `ios/` |
| Ionic layout (`ion-menu`, `ion-header`, `ion-content`, `ion-list`, tab bar) | `src/app/layout/mobile-shell.component.*` — used **only inside the app**; the website keeps its own layout |
| Phone-friendly | the Ionic frame above, plus the website's existing phone layout for every screen |
| Services, API calls, routing keep working | `src/app/native/native-fetch.ts` sends every `/api/...` call to the server with the sign-in token; routes are untouched |
| Back button | `native-bootstrap.ts` → `wireBackButton`: closes the menu, goes back a screen, or puts the app in the background on a home screen |
| Safe areas (notch, status bar, gesture bar) | Ionic's header and tab bar pad themselves; `viewport-fit=cover` is set inside the app; `SystemBars` in `capacitor.config.ts` |
| Splash screen | `capacitor.config.ts` (`SplashScreen`), hidden when the first screen is ready |
| App icon | `resources/` → `npm run mobile:icons` |
| Status bar | dark icons on REEP's light header (`StatusBar`) |
| Token in Capacitor Preferences | `src/app/native/token-store.ts` |

### How the sign-in works in the app

On the website the server keeps you signed in with a **cookie** the browser
holds and JavaScript can never read. The app cannot use that cookie: to the
server the app is a different site, and browsers do not send one site's
cookie to another.

So inside the app:

1. You type email + password as usual.
2. The server sees the request comes from the app (its `Origin` is
   `https://localhost`, listed in `NATIVE_APP_ORIGINS`) and also returns the
   **same sign-in token** in the reply. A normal browser never gets it.
3. The app saves it with **Capacitor Preferences** (the phone's own app
   storage — not `localStorage`, which Android and iOS may wipe when the phone
   is low on space).
4. Every request then carries `Authorization: Bearer <token>`. The server
   checks it exactly like the cookie, so "one device at a time", sign-out and
   disabled accounts all work the same.

## Why something works on the web but not in the app

| What you see in the app | Why | What to do |
|---|---|---|
| Every screen: "could not load" | `apiOrigin` is blank in `native.ts`, or the server does not list the app in `NATIVE_APP_ORIGINS` | Fill both in (steps 1 and 3). |
| Same, and `apiOrigin` starts with `http://` | The app runs at `https://localhost`; Android blocks `https` pages from calling `http` ("mixed content") | Use the `https://` address. |
| No "Continue with Google" button | Google refuses sign-in from inside apps' built-in browsers (`disallowed_useragent`), and its reply would come back to the server, not the phone | Sign in with email + password in the app. |
| Mock interview will not connect | The interview uses a live connection (WebSocket) that only carries the cookie, and checks it comes from the website's address | Use the website for mock interviews for now. Making it work needs the server to accept the app's token on that connection — a follow-up. |
| A download button (CSV, PDF) does nothing | Plain download links are opened by the phone's web view directly, without the sign-in token | Use the website for downloads for now. |
| Signed out after signing in on a laptop | REEP allows one device at a time — on purpose | Sign in again. |
| Android: Back closes the app on the home screen | That is normal Android behaviour on the first screen (it goes to the background) | — |

## Commands at a glance

```
cd apps/web
npm ci                     # first time, and after package.json changes
npm run mobile:android     # build + open in Android Studio
npm run mobile:run         # build + install on the plugged-in phone
npm run mobile:apk         # build the APK file
npm run mobile:ios         # build + open in Xcode (Mac only)
npm run mobile:icons       # regenerate icons/splash from resources/
```
