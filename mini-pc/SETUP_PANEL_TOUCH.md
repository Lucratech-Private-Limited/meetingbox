# Panel rotation + touchscreen — step by step

Do these **on the mini PC**, in order. Stop when touch works.

---

## Step 1 — Open the panel config

```bash
sudo mkdir -p /etc/meetingbox
sudo nano /etc/meetingbox/panel-xrandr.env
```

The path must be **`panel-xrandr.env`** (with **xrandr**). A typo like **`panel-xrander.env`** will not be read by **`meetingbox-apply-kiosk-display-orientation`**.

---

## Step 2 — Paste this block, then edit the two **bold** parts

**A.** If your screen is **DSI** and you use **rotate right** (common for portrait panels mounted landscape), use:

```bash
MEETINGBOX_PANEL_OUTPUT=DSI-1
MEETINGBOX_PANEL_MODE=800x1280
MEETINGBOX_PANEL_ROTATE=right
MEETINGBOX_MAP_TOUCH_TO_OUTPUT=1
```

**A2.** If the panel must use **rotate left** (90° CCW), use **`MEETINGBOX_TOUCH_MATRIX_PRESET=left`** (default MeetingBox DSI stack):

```bash
MEETINGBOX_PANEL_OUTPUT=DSI-1
MEETINGBOX_PANEL_MODE=800x1280
MEETINGBOX_PANEL_ROTATE=left
MEETINGBOX_MAP_TOUCH_TO_OUTPUT=1
MEETINGBOX_TOUCH_MATRIX_PRESET=left
```

If taps are **~90° wrong** (e.g. control at **bottom-center** only works when you tap **right-center** or **left-center**), try **swap X/Y** instead — remove the preset line and use:

```bash
MEETINGBOX_TOUCH_COORD_MATRIX="0 1 0 1 0 0 0 0 1"
```

(or **`MEETINGBOX_TOUCH_MATRIX_PRESET=swap_xy`**). Equivalent 90° CCW as nine numbers: **`MEETINGBOX_TOUCH_COORD_MATRIX="0 -1 1 1 0 0 0 0 1"`**

**A3.** If the **whole UI is upside down** (180°, top and bottom swapped), use **`inverted`** for both the panel and touch:

```bash
MEETINGBOX_PANEL_ROTATE=inverted
MEETINGBOX_TOUCH_MATRIX_PRESET=inverted
```

(Or only the matrix: `MEETINGBOX_TOUCH_COORD_MATRIX="-1 0 1 0 -1 1 0 0 1"`.)  
If it was only “sideways”, try **`left`** or **`right`** instead — not **`inverted`**.

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
For **`MEETINGBOX_PANEL_ROTATE=right`**, use preset **`right`** (or the **Right** matrix below). For **`rotate=left`**, start with **`MEETINGBOX_TOUCH_MATRIX_PRESET=left`**; if taps hit the wrong edge (~90° off), try **swap X/Y** below.

```bash
MEETINGBOX_TOUCH_MATRIX_PRESET=right
```

**Rotate left — 90° CCW** (default):

```bash
MEETINGBOX_TOUCH_MATRIX_PRESET=left
```

**Rotate left — swap X/Y** if preset `left` gives wrong-edge taps (same as `MEETINGBOX_TOUCH_MATRIX_PRESET=swap_xy`):

```bash
MEETINGBOX_TOUCH_COORD_MATRIX="0 1 0 1 0 0 0 0 1"
```

Or nine explicit numbers (quotes are **required**). **Right** (same as preset `right`):

```bash
MEETINGBOX_TOUCH_COORD_MATRIX="0 1 0 -1 0 1 0 0 1"
```

**Left** (same as preset `left`, 90° CCW — not the same as swap X/Y above):

```bash
MEETINGBOX_TOUCH_COORD_MATRIX="0 -1 1 1 0 0 0 0 1"
```

Save and reboot. If it gets **worse**, try the other **left** option (swap X/Y vs preset `left`), swap `right` ↔ `left` for right-rotated panels, or **delete** the matrix line and reboot again.

**If the matrix “does not apply”**, common causes are:

1. **`xrandr` failed** (wrong `MEETINGBOX_PANEL_OUTPUT` / mode) — older scripts **skipped all touch steps** when `xrandr` failed. Update `scripts/apply-kiosk-display-orientation.sh` from the repo and reinstall it to `/usr/local/bin/meetingbox-apply-kiosk-display-orientation` (Step 5). Touch mapping now runs even when `xrandr` fails, and the matrix is applied even when `map-to-output` fails.
2. **`map-to-output` failed** — the matrix is still applied to your device after an attempted map (same script update).
3. **Unquoted matrix in `panel-xrandr.env`** — without quotes, only the first number is used. It must be exactly **nine** numbers:
   `MEETINGBOX_TOUCH_COORD_MATRIX="0 1 0 -1 0 1 0 0 1"`
4. **No pointer device** — set **`MEETINGBOX_TOUCH_XINPUT_ID`** to the **slave pointer** id from `xinput list` (Step 2).
5. **Confirm the script ran** — after login: `journalctl -t meetingbox-kiosk -b --no-pager | tail -30`  
   You should see either `Coordinate Transformation Matrix on '15': ...` or a clear `set ... failed` line.  
   On the panel: `xinput list-props 15 | grep -i Coordinate` (use your id).

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

**D.** Remove **matrix** if you added it and things got worse — edit `panel-xrandr.env`, delete `MEETINGBOX_TOUCH_MATRIX_PRESET` / `MEETINGBOX_TOUCH_COORD_MATRIX`, reboot.

**E.** In **mini-pc `.env`**, keep **`MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1`** so Kivy’s window size matches the real screen (wrong size = wrong button positions).

---

## Taps ~90° wrong (e.g. control at **bottom-center** only works if you tap **left-center** or **right-center**)

That usually means the **touch preset does not match** the panel/firmware, or **X/Y are swapped**.

Do this **on the mini PC** in order (edit `/etc/meetingbox/panel-xrandr.env`, reboot or re-login after each try):

1. **Match rotation** — If `MEETINGBOX_PANEL_ROTATE=right`, start with `MEETINGBOX_TOUCH_MATRIX_PRESET=right`. If `rotate=left`, start with **`MEETINGBOX_TOUCH_MATRIX_PRESET=left`**; if you get wrong-edge taps, switch to **swap X/Y**: **`MEETINGBOX_TOUCH_COORD_MATRIX="0 1 0 1 0 0 0 0 1"`** (remove preset `left` when using the matrix line).

2. **Flip 90°** — If the symptom is “tap on the wrong edge” (bottom vs left or **bottom vs right**), for **rotate=right** switch preset `right` ↔ `left`. For **rotate=left**, switch **swap X/Y** ↔ preset **`left`**.

3. **Swap axes** — **`MEETINGBOX_TOUCH_MATRIX_PRESET=swap_xy`** or matrix **`"0 1 0 1 0 0 0 0 1"`** when preset **`left`** misaligns (~90° off).

4. **Reset to identity on touch** — Try **`MEETINGBOX_TOUCH_MATRIX_PRESET=normal`** (sometimes `map-to-output` alone is enough; remove custom `MEETINGBOX_TOUCH_COORD_MATRIX` if set).

5. **Kivy size must match X11** — In **`mini-pc/.env`** use **`MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1`**, or set **`DISPLAY_WIDTH` / `DISPLAY_HEIGHT`** to the same **rotated** size **`xrandr`** reports. If the UI thinks the window is a different shape than X11, **buttons draw in the wrong place** relative to touch even when the matrix is correct.

6. **Confirm one matrix** — Use **either** `MEETINGBOX_TOUCH_MATRIX_PRESET` **or** `MEETINGBOX_TOUCH_COORD_MATRIX`, not both. If both are set, **`MEETINGBOX_TOUCH_COORD_MATRIX` wins** and **`MEETINGBOX_TOUCH_MATRIX_PRESET` is ignored**. A common mistake: `MEETINGBOX_PANEL_ROTATE=right` and `MEETINGBOX_TOUCH_MATRIX_PRESET=right`, but **`MEETINGBOX_TOUCH_COORD_MATRIX="0 -1 1 1 0 0 0 0 1"`** — that nine-number form is **preset `left`** (90° CCW), not `right`, and will skew taps ~90°. For **`rotate right`**, either delete the coord line and keep **`PRESET=right`**, or set **`MEETINGBOX_TOUCH_COORD_MATRIX="0 1 0 -1 0 1 0 0 1"`** and remove the preset line.

Then reinstall the orientation helper if you updated the repo:

`sudo install -m 0755 …/scripts/apply-kiosk-display-orientation.sh /usr/local/bin/meetingbox-apply-kiosk-display-orientation`

---

## One-page checklist

| Done | Task |
|------|------|
| ☐ | `/etc/meetingbox/panel-xrandr.env` created with DSI + rotate + `MEETINGBOX_TOUCH_XINPUT_DEVICE` |
| ☐ | `sudo usermod -aG input $USER` + reboot |
| ☐ | `MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1` in `mini-pc/.env` |
| ☐ | `/usr/local/bin/meetingbox-apply-kiosk-display-orientation` installed from repo |
| ☐ | `device-ui` restarted or full reboot |
| ☐ | With rotate left: `MEETINGBOX_TOUCH_MATRIX_PRESET=left` (see **`kiosk-desktop/panel-xrandr.env.example`**); use swap X/Y matrix if taps are ~90° wrong |

More detail: **`INFOTAINMENT.md`**, template: **`kiosk-desktop/panel-xrandr.env.example`**.

---

## Rotation not applying (picture stays wrong / `xrandr` never sticks)

1. **Confirm the helper runs and log the error** (on the mini PC, after login):

   ```bash
   journalctl -t meetingbox-kiosk -b --no-pager | tail -50
   ```

   You should see either `xrandr ok: ...` or lines like `xrandr failed ...` plus a dump of `xrandr` output.

2. **Wrong output name** — On the **built-in screen** terminal (`DISPLAY=:0`):

   ```bash
   xrandr
   ```

   Use the **exact** connected output name in `panel-xrandr.env` (e.g. `HDMI-1`, `eDP-1`, `DSI-1`). Or set:

   ```bash
   MEETINGBOX_PANEL_OUTPUT=auto
   ```

   so the script picks the first **connected** line from `xrandr`.

3. **Wrong mode** — If `--mode 800x1280` fails, the script falls back to **rotate-only** and **`--auto --rotate`**. If all fail, set **`MEETINGBOX_PANEL_MODE`** to a mode that appears in `xrandr` for that output (or leave mode wrong and rely on `--auto` after script update).

4. **Disabled in config** — Ensure you do **not** have:

   ```bash
   MEETINGBOX_SKIP_PANEL_XRANDR=1
   ```

5. **Early X / GDM** — The script **retries** `xrandr` (default 30 attempts, 1 s apart). If the panel is very slow, add to `panel-xrandr.env`:

   ```bash
   MEETINGBOX_XRANDR_ATTEMPTS=60
   MEETINGBOX_XRANDR_RETRY_DELAY=1
   ```

6. **Refresh the installed script** after `git pull`:

   ```bash
   sudo install -m 0755 ~/meetingbox-mini-pc-release/scripts/apply-kiosk-display-orientation.sh /usr/local/bin/meetingbox-apply-kiosk-display-orientation
   ```

   Then reboot.

---

## It used to work — something changed (regression)

Touch/rotation rarely “drifts” by itself. After an update or config edit, check these **in order**:

1. **Touch `xinput` id shifted** (very common after kernel/driver or USB reorder).  
   Run `xinput list` on the panel and compare **`slave  pointer`** id to **`MEETINGBOX_TOUCH_XINPUT_ID`** in `/etc/meetingbox/panel-xrandr.env`. If the id changed, **update the number** and reboot. Mapping the wrong id applies matrix to the wrong device.

2. **System packages** — `apt upgrade` can change **libinput**, **Xorg**, or **kernel** touch behavior. Note date of last upgrade: `grep " install \| upgrade " /var/log/dpkg.log | tail -20`. If it lines up with the break, search for touch-related packages or try booting an older kernel from the GRUB **Advanced options** menu once to confirm.

3. **Kivy / Docker `.env`** — If **`DISPLAY_WIDTH`**, **`DISPLAY_HEIGHT`**, or **`MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR`** changed, the **UI size** may no longer match X11. Restore previous values or set **`MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1`** and restart **device-ui**.

4. **`panel-xrandr.env` changed** — Compare to a backup or git:  
   `sudo diff -u /etc/meetingbox/panel-xrandr.env ~/meetingbox-mini-pc-release/kiosk-desktop/panel-xrandr.env.example`  
   Revert accidental edits to **`MEETINGBOX_PANEL_ROTATE`**, **`MEETINGBOX_TOUCH_*`**, or **`MEETINGBOX_PANEL_OUTPUT`**.

5. **Repo script updated** — If you **`git pull`**’d MeetingBox, reinstall **`meetingbox-apply-kiosk-display-orientation`** (see Step 5 in the main guide) so the box matches the repo; then reboot.

6. **Collect a snapshot** (attach to a ticket or compare after fixes):

   ```bash
   export DISPLAY=:0
   bash ~/meetingbox-mini-pc-release/scripts/diagnose-touch-panel.sh | tee /tmp/meetingbox-touch.txt
   xinput list-props "$(grep MEETINGBOX_TOUCH_XINPUT_ID /etc/meetingbox/panel-xrandr.env | cut -d= -f2)" 2>/dev/null | grep -i Coordinate
   ```

If you find the **pointer id** was wrong, fixing only that often restores “like before” without chasing new matrix values.
