# Setting Up Your Telegram Bot

## Step 1: Create the Bot

1. Open Telegram on your phone
2. Search for **@BotFather** and open the chat
3. Send: `/newbot`
4. When asked for a **display name**, type something like: `Roost`
5. When asked for a **username**, pick one ending in `bot`, e.g.: `yourname_roost_bot`
6. BotFather will reply with a message containing your **bot token**
   - It looks like: `7123456789:AAF-some-long-random-string`
   - Copy this token — you'll need it in Step 3

## Step 2: Get Your Telegram User ID

1. In Telegram, search for **@userinfobot** and open the chat
2. Send any message (e.g. `/start`)
3. It replies with your **user ID** — a number like `123456789`
4. Copy this number — you'll need it in Step 3

## Step 3: Configure the Bot on Your VPS

Run this command, replacing the placeholder values with your actual token and user ID:

```bash
nano /home/dev/projects/roost/.env
```

Edit these two lines:

```
TELEGRAM_BOT_TOKEN=7123456789:AAF-your-actual-token-here
TELEGRAM_ALLOWED_USERS=123456789
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

## Step 4: Start the Bot

```bash
systemctl --user start roost-bot
```

Check it's running:

```bash
systemctl --user status roost-bot
```

## Step 5: Test It

1. Open Telegram on your phone
2. Search for your bot by the username you chose (e.g. `@yourname_roost_bot`)
3. Send `/help`
4. You should see the list of available commands

## Troubleshooting

View bot logs:

```bash
journalctl --user -u roost-bot -n 50
```

Restart after changing `.env`:

```bash
systemctl --user restart roost-bot
```

## Optional: Set Command Menu in Telegram

Send these to @BotFather to add a nice command menu:

```
/setcommands
```

Then select your bot and paste:

```
tasks - List active tasks
add - Quick-add a task
done - Mark task complete
show - Show task details
note - Jot a quick note
notes - List recent notes
delnote - Delete a note
doc - AI-assisted documentation
git - Git status, log, diff
commit - Stage and commit changes
gdrive - Google Drive commands
log - Command audit log
help - Show all commands
```
