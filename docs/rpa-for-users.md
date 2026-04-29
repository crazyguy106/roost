# Setting up an RPA Flow — Non-Technical Guide

You don't need to know HTML, write YAML, or open DevTools. You describe
the workflow in plain English; the bot inspects the portal, drafts the
automation, and walks you through testing it.

## What the bot needs from you

1. **The URL of the portal** (e.g. `https://www.aia.com.sg/portal/login`).
2. **What the workflow should do** in your own words. e.g. *"Log in,
   open the policies tab, download every policy as a PDF, save them to
   Google Drive."*
3. **Your credentials**, when the bot asks. They're stored encrypted —
   not in the YAML file, never logged, never sent anywhere except the
   target portal.
4. **OTP codes**, if the portal sends any. The bot pauses mid-run and
   pings you on Telegram with a prompt; you reply with the code; it
   resumes.

That's it. Everything else is the bot's job.

## The conversation

In Telegram:

```
You:   /rpa new
Bot:   Let's build a new RPA flow. Tell me — in plain English — what
       you'd like the bot to do, and the URL of the portal.
You:   I want to log into my AIA broker portal at
       https://www.aia.com.sg/portal/login, deal with the OTP they
       email me, and download every policy statement.
Bot:   [calls rpa_inspect_page on the URL]
       I see a username field, a password field, a Sign In button,
       and after that an OTP step. Could you give me your AIA login
       and password? I'll store them encrypted.
You:   user: ethan@example.com   password: hunter2
Bot:   [calls rpa_set_credential x2]
       Stored. Drafting the flow…
       [drafts YAML, calls rpa_set_flow_config]
       [calls rpa_test_step on step 0 to confirm the goto works]
       Looks good. Ready to do a full run? It'll pause to ask you for
       the OTP when AIA mails it to you.
You:   yes
Bot:   [calls rpa_run]
       Started run #7. I'll ping you when the OTP arrives.

       (a minute later)

       AIA wants an OTP — what is it?
You:   382911
Bot:   [calls rpa_runs.submit_input — flow resumes]
       Got it — fed into RPA run #7.

       (later)

       Run #7 done. Downloaded 4 files; uploaded to Drive at
       Roost/Insurance/AIA/2026-04-26/.
```

## What you can ask the bot to do

- **Build a new flow:** *"Set up automation for `<portal URL>` that
  does `<thing>`."*
- **Test a flow:** *"Run a dry-run of the AIA flow."*
- **Schedule it:** *"Run the AIA flow every Monday at 8am."*
- **Edit a flow:** *"For AIA, change the OTP source to email and
  search for messages from `noreply@aia.com.sg`."*
- **Share a flow:** *"Export the AIA flow as a YAML file so I can
  send it to my colleague."*
- **List what's set up:** *"What RPA flows do I have configured?"*

## What can go wrong

- **The portal changes its layout.** Selectors break. Tell the bot
  *"The AIA flow is failing — please look at the page and fix it."*
  It re-inspects, finds the new selectors, updates the flow.
- **A login is blocked or there's a CAPTCHA.** The bot can't solve
  CAPTCHAs. It pauses and asks you to complete the challenge in a
  Roost-controlled browser session.
- **An OTP doesn't arrive.** The bot times out after 5 minutes.
  Re-trigger the run; it'll prompt again.

## Privacy & safety

- Credentials are encrypted at rest with a key derived from
  `SESSION_SECRET`. They never appear in YAML files or logs.
- The bot only visits URLs you tell it to.
- You can delete a flow any time: *"Forget the AIA flow."*
- Cancel any in-flight run: `/rpa cancel <id>`.

## When you outgrow the chat

If you want fine-grained control — exact selectors, custom step
ordering, or commit your flow to git for other Roost users to share —
see [rpa-authoring.md](rpa-authoring.md) for the YAML reference.

The chat path and the file path produce the same kind of flow —
they're two interfaces to the same underlying engine.
