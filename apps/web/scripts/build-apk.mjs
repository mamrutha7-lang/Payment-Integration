// Build an installable Android APK (debug build) in one command:
//
//     npm run mobile:apk
//
// Works the same on Windows, macOS and Linux. Needs Java 21 and the Android
// SDK, which Android Studio installs for you (docs/mobile-app.md, step 2).
// The APK lands in android/app/build/outputs/apk/debug/app-debug.apk.

import { execSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const web = join(import.meta.dirname, '..');
const run = (cmd, cwd = web) => execSync(cmd, { cwd, stdio: 'inherit' });

const nativeTs = readFileSync(join(web, 'src/environments/native.ts'), 'utf8');
if (/apiOrigin:\s*''/.test(nativeTs)) {
  console.error(
    "\nStop: src/environments/native.ts still has apiOrigin: ''.\n" +
      'Put your REEP server address there first (for example https://reep.example.edu),\n' +
      'or the app will open but every screen will fail to load.\n',
  );
  process.exit(1);
}

run('npx ng build');
run('npx cap sync android');

const android = join(web, 'android');
const gradlew = process.platform === 'win32' ? 'gradlew.bat assembleDebug' : './gradlew assembleDebug';
run(gradlew, android);

const apk = join(android, 'app/build/outputs/apk/debug/app-debug.apk');
console.log(existsSync(apk) ? `\nDone. Your APK is here:\n  ${apk}\n` : '\nGradle finished but no APK was found.');
