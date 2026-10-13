# SparkymateAU App Store Release Checklist

## 1. Production URL
Update the live app URL in:
- `android/app/src/main/res/values/strings.xml`

Set it to your production deployment, for example:
- `https://your-domain.com`

## 2. Privacy Policy
Publish your privacy policy to a public URL and update it in the Play Console listing.

Starter file:
- `PRIVACY.md`

## 3. Create a release keystore
Run:
- keytool -genkeypair -v -keystore sparkymateau-release.jks -keyalg RSA -keysize 2048 -validity 10000 -alias sparkymateau

Store this file in the `android/` folder.

## 4. Set signing environment variables
Run:
- export KEYSTORE_PASSWORD="your-keystore-password"
- export KEYSTORE_KEY_PASSWORD="your-keystore-password"

## 5. Build the Android App Bundle
From the `android` folder:
- ./gradlew bundleRelease

Output:
- `android/app/build/outputs/bundle/release/app-release.aab`

## 6. Upload to Google Play Console
- Create the app in Play Console
- Upload the AAB
- Add screenshots, description, and privacy link
- Complete content rating and store listing metadata
- Submit for review

## 7. Suggested Play metadata
- App name: SparkymateAU
- Category: Productivity or Business
- Short description: AI tools for Australian electricians
- Content rating: review for appropriate category

## 8. Final preflight
- Confirm the app loads the live site correctly
- Test on a real Android device
- Validate JavaScript and storage functions
- Confirm app icon and splash/theme look correct

## 9. Important note
The WebView wrapper is intended to host the web application. The actual functionality is in the deployed web app, not the native wrapper itself.
