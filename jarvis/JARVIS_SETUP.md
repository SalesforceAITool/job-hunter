# Jarvis setup: Samsung Galaxy Z Fold 7

Jarvis is built on the open-source app [Mika](https://github.com/wangzan101/Mika) (Apache-2.0).
What's different from Mika: **before sending an email, WhatsApp message or form, it asks you first** (recipient + exact text), and sends only after you say yes.

## 1. Download the APK
1. On the phone, open: github.com/SalesforceAITool/job-hunter/actions → **Jarvis APK** → latest green run.
2. At the bottom, under **Artifacts**, download `Jarvis-...` (requires GitHub login), then unzip it.
3. Before installing: Settings → Security and privacy → **Auto Blocker → Off** (otherwise Samsung blocks the APK).
4. Install the `.apk` (allow "Install unknown apps" when asked).

> Updates: until the `JARVIS_DEBUG_KEYSTORE_B64` secret is added in GitHub, each new build has a new key,
> so to update you have to uninstall the old app and install the new one (settings are lost). Once the secret is added, updates install directly.

## 2. Phone settings (one time)
- Settings → About phone → Software information → tap **Build number** 7 times → Developer options turn on.
- Developer options:
  - **USB debugging** ON
  - **Wireless debugging** ON
  - **Disable adb authorization timeout** ON
- Settings → Apps → Jarvis/Mika → Battery → **Unrestricted**.
- Settings → Battery → Background usage limits → **Never sleeping apps** → add Jarvis.
- Settings → Device care → **Auto restart / Auto optimization** → Off (a reboot stops the helper).
- After installing: Settings → Apps → Jarvis → ⋮ → **Allow restricted settings** (if notification access is blocked).

## 3. Inside the app
1. **Set up phone control** wizard → pair Wireless debugging with the 6-digit code (connect to home Wi-Fi first, tick "Always allow on this network").
2. Run the **Compatibility self-check**. This is day-1 test #1: it shows whether the Fold 7 is OK.
3. Settings → Model → **Gemini** → paste your API key from aistudio.google.com (free).
   - Main: a Gemini Flash-Lite model (higher free quota). For hard tasks: a Gemini Flash model.
   - Backup: Groq (console.groq.com, free) via Custom.
4. Permissions: notification access, microphone, phone, contacts.
5. Language: "System". Set the phone to English (India) or Hindi so speech recognition understands Hinglish.

## 4. Voice
- Talk: hold the chat input, or long-press the floating capsule, or use the voice-call mode.
- Quick launch: Settings → Advanced features → **Side button** → Double press → open app → Jarvis.

## 5. First tests
1. "Aaj kaunse emails aaye, bata do" (read only).
2. "Rahul ko WhatsApp pe 'kal milte hain' bhej do" → **an approval card will appear** with the recipient + text → approve → it gets sent.
3. "Indore mein turf search karo".
4. Tell me what worked and what didn't.

## Honest limits
- Banking/password screens show black: you do those steps yourself. Logins, OTPs and payments are always done by you.
- Gemini free tier: Google may use the data. To avoid that, enable billing on the Gemini account or use a local model.
- WhatsApp automation is against WhatsApp's terms, so there is a small risk of an account ban.
- After a reboot, it needs Wi-Fi to restart the helper.
