# SparkymateAU Mobile Launch Checklist

## PWA / Home Screen
- Ensure the web app is served over HTTPS
- Confirm `/public/manifest.webmanifest` is linked in the HTML head
- Ensure icon files are present in `public/`
- Confirm `beforeinstallprompt` install logic works in supported browsers
- Test Add to Home Screen on Chrome/Edge for Android

## Android Wrapper
- Confirm `android/app/src/main/res/values/strings.xml` points to your live app URL
- Build the signed release bundle with Gradle
- Confirm app launches and loads the live web app
- Check network permissions and cleartext policy
- Validate launcher icon and shortcuts on a real device

## Release Files
- `android/app/build.gradle`
- `android/app/src/main/AndroidManifest.xml`
- `android/app/src/main/res/xml/shortcuts.xml`
- `public/manifest.webmanifest`
- `public/install-pwa.js`
- `public/icon.svg`
- `public/shortcut-ai.svg`
- `public/shortcut-quote.svg`

## Production URL
Replace the placeholder production URL before release:
- `android/app/src/main/res/values/strings.xml`

## Final Publishing Note
The web app should be deployed to a production URL before the Android wrapper or PWA is submitted for app-store or home-screen use.
