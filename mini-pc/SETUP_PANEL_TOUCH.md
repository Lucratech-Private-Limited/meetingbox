# Panel rotation + touchscreen — step by step

Do these **on the mini PC**, in order. Stop when touch works.

---

## Step 1 — Open the panel config

```bash
sudo mkdir -p /etc/meetingbox
sudo nano /etc/meetingbox/panel-xrandr.env
```

---

## Step 2 — Paste this block, then edit the two **bold** parts

**A.** If your screen is **DSI** and you use **rotate right** (common for portrait panels mounted landscape), use:

```bash
MEETINGBOX_PANEL_OUTPUT=DSI-1
MEETINGBOX_PANEL_MODE=800x1280
MEETINGBOX_PANEL_ROTATE=right
MEETINGBOX_MAP_TOUCH_TO_OUTPUT=1
```

**B.** Touch: use **numeric id** if you have Goodix twice (pointer vs keyboard). Run:

```bash
export DISPLAY=:0
xinput list
```

Find **Goodix** with **`slave  pointer`** — note **id=** (often **15**). **Ignore** the Goodix line under **`slave  keyboard`** (often **18**).

Add **this** to the same file (use your pointer id):

```bash
MEETINGBOX_TOUCH_XINPUT_ID=15
```

Or by **name** (only if one Goodix line):

```bash
MEETINGBOX_TOUCH_XINPUT_DEVICE='Goodix Capacitive TouchScreen'
```

Save: **Ctrl+O**, Enter, **Ctrl+X**.

---

## Step 3 — User must be in group `input` (once)

```bash
sudo usermod -aG input $USER
sudo reboot
```

(Or log out and log in completely after this.)

---

## Step 4 — Match app size to the real screen

```bash
nano ~/meetingbox-mini-pc-release/.env
```

Add or set:

```bash
MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1
```

Save and exit.

---

## Step 5 — Install / refresh the orientation helper (if you use git)

```bash
cd ~/meetingbox-mini-pc-release
git pull
sudo install -m 0755 scripts/apply-kiosk-display-orientation.sh /usr/local/bin/meetingbox-apply-kiosk-display-orientation
```

---

## Step 6 — Restart the UI

```bash
cd ~/meetingbox-mini-pc-release
docker compose --profile mini-pc --profile docker-audio up -d device-ui
```

Or reboot:

```bash
sudo reboot
```

---

## Step 7 — If taps still hit the wrong place

Edit the panel file again:

```bash
sudo nano /etc/meetingbox/panel-xrandr.env
```

Add **one** of these (try after Step 6). **Always quote values with spaces.**

```bash
MEETINGBOX_TOUCH_MATRIX_PRESET=right
```

Or nine explicit numbers (quotes are **required**):

```bash
MEETINGBOX_TOUCH_COORD_MATRIX="0 1 0 -1 0 1 0 0 1"
```

Save and reboot. If it gets **worse**, change `right` to `left`, or **delete** the line and reboot again.

---

## Step 8 — Still broken?

**A.** Collect a full report and share it:

```bash
cd ~/meetingbox-mini-pc-release
export DISPLAY=:0
bash scripts/diagnose-touch-panel.sh
```

**B.** Raw touch test (should print lines when you drag a finger on the glass):

```bash
export DISPLAY=:0
xinput test 15
```

(Use **15** or your pointer id from `xinput list`.)

**C.** If `xinput test` shows nothing, the kernel may not see touch — try `sudo evtest` and pick the Goodix event node.

**D.** Remove **matrix** if you added it and things got worse — edit `panel-xrandr.env`, delete `MEETINGBOX_TOUCH_MATRIX_PRESET`, reboot.

**E.** In **mini-pc `.env`**, keep **`MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1`** so Kivy’s window size matches the real screen (wrong size = wrong button positions).

---

## One-page checklist

| Done | Task |
|------|------|
| ☐ | `/etc/meetingbox/panel-xrandr.env` created with DSI + rotate + `MEETINGBOX_TOUCH_XINPUT_DEVICE` |
| ☐ | `sudo usermod -aG input $USER` + reboot |
| ☐ | `MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1` in `mini-pc/.env` |
| ☐ | `/usr/local/bin/meetingbox-apply-kiosk-display-orientation` installed from repo |
| ☐ | `device-ui` restarted or full reboot |
| ☐ | Optional: `MEETINGBOX_TOUCH_MATRIX_PRESET=right` |

More detail: **`INFOTAINMENT.md`**, template: **`kiosk-desktop/panel-xrandr.env.example`**.
