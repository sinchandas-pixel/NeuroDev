# NeuroDev chat interface

The static chat interface is served by the FastAPI application at `/ui/`. Start
the backend with `uvicorn app.main:app --reload`, then open
`http://localhost:8000/ui/`.

The interface sends messages to the existing `/chat` and `/projects` endpoints.
NeuroDev now requires an account to access workspace APIs. New accounts must
verify their email before signing in. Conversations remain in browser storage
and are separated by account on the same browser; they are not synchronized
between devices.
Each browser tab has its own sign-in session. Opening NeuroDev in a new tab
requires signing in again; signing out or closing one tab does not sign out
other tabs.

Email verification uses a six-digit code sent through SMTP. Enter the code in
the app on the device where you signed up; no email link or localhost address
is used. Codes expire after 15 minutes and allow five attempts. Use **Resend
verification code** to get a replacement if needed.
On the sign-in screen, choose **Forgot your password?** to receive a separate
six-digit password reset code. Codes expire after 15 minutes, allow five
attempts, and a successful password reset signs out all active tabs.
Signup also flags common email-provider domain typos (for example, `gmail.co`)
and offers a one-click correction before creating the account.
For Gmail, enable 2-Step Verification on the sender account, create a Google
App Password, and use that App Password (not the Gmail sign-in password):
New account passwords need at least eight characters; no symbols or other
character combinations are required.

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=sender@gmail.com
SMTP_PASSWORD=your-16-character-app-password
SMTP_FROM_EMAIL=sender@gmail.com
SMTP_USE_SSL=false
SMTP_STARTTLS=true
```

Keep these values private in the backend `.env`; never put them in frontend
code or commit them. Serve the app over HTTPS when it is deployed publicly.
Account records are stored in the local SQLite database under `~/.neurodev` (or
`NEURODEV_DATA_DIR`); deployments with multiple app instances need a shared,
durable account database and storage volume.
To allow faculty to view the account roster, set `ADMIN_EMAILS` to a
comma-separated list of verified administrator email addresses in `.env`.
Administrators sign in with that email through their verified NeuroDev account,
then choose **Users & CSV** in the top bar or **View users & export CSV** in
their profile; no public admin endpoint is provided. The roster shows names,
emails, verification status, signup time, and last successful login. It never exposes passwords or
password hashes. Conversation history remains in each browser and is not
available in the roster or a central server database.
The roster summary counts all accounts, verified accounts, and accounts with a
successful login. Administrators can export those same non-sensitive account
fields as CSV; passwords and password hashes are never included.
If signup says that email could not be sent, the account is still saved but
cannot sign in yet. The app owner must configure SMTP in the server's `.env`,
restart NeuroDev, and the user can then select **Resend verification code**.

You can paste long text into the message box and attach up to three PDFs
(10 MB each); PDF text is extracted locally by the backend and included with
your question. The extracted text is then sent to the configured Groq model
with your message. Scanned PDFs without selectable text are not supported.
Conversation history, the current workspace ID, and the optional display name
are stored in the current browser's local storage under the signed-in account.
They are not synchronized between devices.
Voice input records audio in the browser and sends it to the backend for
transcription with Groq's Whisper model. The browser prefers the built-in
microphone and enables echo cancellation, noise suppression, and automatic gain
control. The voice panel indicates when it detects speech; silent recordings
are rejected instead of being shown as hallucinated transcripts. English
recordings use Whisper Large V3 for recognition accuracy. Allow microphone
access when prompted; the recording is sent to Groq only after you stop
recording.
Use **Photos** to attach up to three JPEG, PNG, or WebP images to a message.
Photos are resized in the browser and analyzed with the configured Groq vision
model (`GROQ_VISION_MODEL`, default `qwen/qwen3.8-27b`). Image data is sent to
Groq for analysis; only small previews are retained with local conversation
history.

If PostgreSQL is unreachable during local development, the backend automatically
uses SQLite at `~/.neurodev/neurodev-local.sqlite3` (override the directory with
`NEURODEV_DATA_DIR`). PostgreSQL remains the recommended store for deployment.

Run `npm run check` from this directory to check the frontend JavaScript syntax.
