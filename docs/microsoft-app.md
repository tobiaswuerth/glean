# Registering glean with Microsoft

Outlook.com and Hotmail accept only OAuth2 for IMAP: Microsoft turned off passwords and app passwords for IMAP on
personal accounts in September 2024. Any program that signs in with OAuth2 needs a client ID, which an app registered in
Microsoft Entra gives it. The registration is free, takes about five minutes, and gives no one but you access: glean
still signs in as you, in your browser, and only you can consent.

## 1. Register the app

1. Open the [Microsoft Entra admin center](https://entra.microsoft.com) and sign in with your Microsoft account.
   If it says your account has no directory, create a free Azure account first, which makes one, and come back.
2. Go to **Applications > App registrations > New registration**.
3. **Name:** `glean` (anything you recognize).
4. **Supported account types:** *Personal Microsoft accounts only*.
5. **Redirect URI:** platform *Public client/native (mobile & desktop)*, URI `http://localhost`.
6. **Register.**

## 2. Allow IMAP

1. In the app, open **API permissions > Add a permission > Microsoft Graph > Delegated permissions**.
2. Add `IMAP.AccessAsUser.All` and `offline_access`, then **Add permissions**.

glean asks for exactly these when you sign in, and Microsoft shows you the consent screen once. (Without them listed
here, the sign-in still works, since a personal account consents for itself; listing them documents what the app
does.)

## 3. Copy the client ID

On the app's **Overview** page, copy the **Application (client) ID**, a GUID such as
`3f2a9c1b-7d4e-4b8a-9c61-0e5f2a7d9b13`. `glean setup` asks for it; it goes into `glean.toml` as `[mail] client_id`.
It is not a secret: a public client has none, and the sign-in is protected by PKCE and your own login.

## 4. Turn on IMAP in Outlook

In [Outlook on the web](https://outlook.live.com): **Settings > Mail > Forwarding and IMAP > Let devices and apps use
IMAP**: on.

## 5. Sign in

```bash
glean mail login               # opens the browser
glean mail login --device-code # or: a code to enter on another device
```

The tokens are kept in `tokens-mail.bin` in glean's home, encrypted with your Windows login (DPAPI); on macOS and
Linux in a file only you may read. glean refreshes them by itself; `glean mail logout` forgets them. You can also
revoke glean's access at any time at [account.live.com/consent/Manage](https://account.live.com/consent/Manage).

## Troubleshooting

| Message | What to do |
|---|---|
| `AADSTS700016: Application … was not found` | The client ID is wrong, or the app isn't registered for personal accounts. |
| `AADSTS50011: The redirect URI … does not match` | Add `http://localhost` as a *Public client/native* redirect URI (step 1.5). |
| `the IMAP server refused the sign-in` | IMAP is off in Outlook's settings (step 4), or the sign-in has expired: `glean mail login`. |
| `signed in as …, but glean.toml names …` | You chose another account in the browser: sign in again with the one in `glean.toml`. |
