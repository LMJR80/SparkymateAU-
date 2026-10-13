# SparkymateAU Android Wrapper

This directory contains a simple Android WebView wrapper that packages the SparkymateAU web app for installation on Android devices and the Google Play Store.

## What it does

- Launches the web app in a secure WebView
- Enables JavaScript and local storage support
- Blocks cleartext network access
- Uses app-level navigation and back button handling

## Quick start

1. Open Android Studio.
2. Choose Open and select the `android` folder.
3. Let Gradle sync complete.
4. Update the web app URL in `android/app/src/main/res/values/strings.xml` to your production deployment URL.
5. Build a release APK/AAB from Build > Generate Signed Bundle / APK.

## Required before publishing

- Replace the placeholder URL in `strings.xml` with your production website or deployed app.
- Create app icons and review the Play Store graphics.
- Add a privacy policy and update your Play Store listing.
- Generate a valid signing key and upload the AAB.

## Default package name

`com.sparkymateau.app`

## Privacy policy

Use the file `PRIVACY.md` as a starting point, then publish the final version on a public URL.
