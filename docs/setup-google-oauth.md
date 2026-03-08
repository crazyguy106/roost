# Setting Up Google OAuth for the Web UI

This replaces the basic username/password login with Google Sign-In.
Only your Google account will be allowed access.

## Step 1: Create a Google Cloud Project (if you don't have one)

1. Go to: https://console.cloud.google.com/
2. Click the project dropdown at the top → **New Project**
3. Name it something like `Roost`
4. Click **Create**
5. Make sure the new project is selected in the dropdown

## Step 2: Enable the OAuth API

1. In the left sidebar, go to **APIs & Services** → **Library**
2. Search for **Google Drive API** and click **Enable** (needed for rclone/gdrive later)
3. No other APIs are needed for login — OAuth consent is built-in

## Step 3: Configure the OAuth Consent Screen

1. Go to **APIs & Services** → **OAuth consent screen**
2. Select **External** and click **Create**
3. Fill in:
   - **App name**: `Roost`
   - **User support email**: your email
   - **Developer contact email**: your email
4. Click **Save and Continue**
5. On the **Scopes** page, click **Add or Remove Scopes**
   - Add: `openid`, `email`, `profile`
   - Click **Update**, then **Save and Continue**
6. On the **Test users** page, click **Add Users**
   - Add your Gmail address
   - Click **Save and Continue**
7. Click **Back to Dashboard**

> **Note**: While in "Testing" mode, only the emails you added as test users
> can log in. This is fine — it's an extra layer of security.

## Step 4: Create OAuth Credentials

1. Go to **APIs & Services** → **Credentials**
2. Click **+ Create Credentials** → **OAuth 2.0 Client ID**
3. Application type: **Web application**
4. Name: `Roost Web`
5. Under **Authorized redirect URIs**, click **+ Add URI**
   - Enter: `http://YOUR_VPS_IP:8080/auth/callback`
   - Replace `YOUR_VPS_IP` with your actual VPS IP address
   - If you use a domain name, use that instead
6. Click **Create**
7. A dialog shows your **Client ID** and **Client Secret**
   - Copy both — you need them in Step 5

## Step 5: Configure on Your VPS

Run:

```bash
nano /home/dev/projects/roost/.env
```

Add these lines (replace with your actual values):

```
GOOGLE_CLIENT_ID=123456789-xxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxx
GOOGLE_ALLOWED_EMAIL=your.email@gmail.com
SESSION_SECRET=run-this-to-generate-one-see-below
```

Generate a random session secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output and paste it as the `SESSION_SECRET` value.

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

## Step 6: Tell Me You're Ready

Once you've added those four values to `.env`, let me know and I'll
implement the OAuth flow in the code. The changes are:

- `web/app.py` — swap Basic Auth for session middleware
- `web/auth.py` — new file with `/login`, `/callback`, `/logout` routes
- `config.py` — load the new env vars
- `requirements.txt` — add `authlib` and `itsdangerous`

Nothing else changes — CLI, bot, templates, and API all stay the same.

## How It Will Work

1. You visit `http://YOUR_VPS_IP:8080`
2. Redirected to Google Sign-In
3. You log in with your Google account
4. Google redirects back to your VPS
5. The app checks your email matches `GOOGLE_ALLOWED_EMAIL`
6. A session cookie keeps you logged in
7. No passwords stored anywhere

## Security Notes

- Only the email in `GOOGLE_ALLOWED_EMAIL` can access the web UI
- The session cookie is signed with `SESSION_SECRET` (can't be forged)
- The OAuth consent screen in "Testing" mode adds a second gate
- Basic Auth is removed entirely — no passwords in `.env`
