<h1><img src="docs/icon.png" width="40" align="top"> Maple Classic Exp Overlay</h1>

**A free EXP tracker for MapleStory Classic World.** A small window sits next to the game and shows how fast you're leveling and making mesos, and the [best maps for your class](https://mcwo.app).

**[Download it here](../../releases/latest)** · [How to install](#how-to-install)

> **This is a beta.** It works, but you might run into rough edges. I'm actively improving it, so check back for updates. If something doesn't work, please [tell me](../../issues)!

![The overlay next to MapleStory, while farming](docs/screenshot-game.jpg)

## What it shows

![The overlay up close, with its log open](docs/screenshot-main.png)

- **EXP per hour**, for your whole session and for the last 5 minutes
- **How long until your next level**, right under your EXP/hr (for example "Lv 31 in 36 min")
- **Mesos per hour**, read from your inventory
- **Net mesos per hour**, which takes off what your potions cost (see [Net mesos and potions](#net-mesos-and-potions))
- **A log of every map you farm**: your EXP/hr and mesos/hr there, at which level and class, solo or in a party

You don't type anything in. It reads all of this from your game screen by itself.

## How to install

You need Windows 10 or 11. It takes about 2 minutes, and you don't need to install anything else.

1. Go to the **[download page](../../releases/latest)**.
2. Under **Assets**, click the **Maple-Classic-Exp-Overlay-v….zip** file (for example `Maple-Classic-Exp-Overlay-v0.9.3-beta.zip`) to download it. Don't click "Source code": that's for programmers.
3. Find the zip in your Downloads folder. **Right-click it**, choose **Extract All...**, then click **Extract**. (Just double-clicking the zip isn't enough. It has to be extracted.)
4. Open the new folder, then the **Maple Classic Exp Overlay** folder inside it.
5. Double-click **Maple Classic Exp Overlay.exe**. It may show as just **Maple Classic Exp Overlay**, with a gold chart icon.

**Windows says "Windows protected your PC"?** Click **More info**, then **Run anyway**. Windows shows this for any new program that isn't from a big company.

**Keep the whole folder together.** The program needs the files next to it, so don't drag it out of its folder. To put it on your desktop, right-click **Maple Classic Exp Overlay.exe** and choose **Send to**, then **Desktop (create shortcut)**. On Windows 11, **Send to** is under **Show more options**.

## How to use it

1. **Start MapleStory.** Windowed, borderless or fullscreen all work, at any size.
2. **Open Maple Classic Exp Overlay.** It finds the game window by itself.
3. **Move the overlay so it doesn't cover these parts of the game:**
   - the minimap (top left)
   - the EXP bar (bottom)
   - the mesos at the bottom of your inventory
   - the party list

   The overlay reads them from your screen, so it can't see through itself. If it's in the way, it tells you at the bottom of its window. It remembers where you put it.
4. **Press Start** and play normally. You only need to press it once:
   - It **pauses by itself** when you get no EXP for 15 seconds or leave the map, and carries on at your next EXP gain. Paused time doesn't count, so EXP/hr only covers your farming.
   - On a different map, the numbers start from zero for that map.
5. **Want mesos tracked?** Open your inventory (any tab) when you start, and now and then while you play. It doesn't have to stay open. If the overlay asks you to open it, do. Mesos you spend or move at a shop, storage or trade are left out, so only mesos you pick up count.
6. **Want net mesos?** Keep the potions you use on the quick-slot bar. See [Net mesos and potions](#net-mesos-and-potions).

Within a few seconds it shows your level and how much EXP you have. The per-hour numbers fill in once you've gained a little EXP.

| Control | What it does |
|---|---|
| **Status pill** (top left) | Says what it's doing: tracking, paused, or what's wrong (for example it can't find the EXP bar) |
| **Start / Pause / Resume** | Starts tracking. **Pause** stops it until you press **Resume**. |
| **Reset** (the round arrow) | Starts a new session from zero, and looks for the minimap, inventory and party list again (handy after moving them around) |
| **Log this run** (switch) | Turn it off to keep the current run out of the log, for example while you're testing a map or waiting for your party. Everything is still shown on screen. It turns itself back on when you leave the map or stop getting EXP, so your next run is logged as usual. |
| **Details** | Shows or hides the extra numbers (gained, current, mesos, elapsed) |
| **...** (More) | **Select area** lets you drag a box around the EXP numbers, if it ever can't find them (it then turns into **Follow game**, which goes back to finding them by itself). **View capture** shows exactly what it read from your screen. |
| **Best maps** | Opens the [best-maps website](#best-maps-for-your-class), already showing maps for your level, party size and class |
| **Log icon** | Opens and closes the log |
| **Compact icon** | A much smaller window with the main numbers and Start/Pause, in the [shape](#compact-shapes) you chose. Click it again to bring everything back. Shift+4 or double-clicking the big number does the same. |
| **Gear icon** | Settings |

## Compact shapes

Compact mode comes in three shapes. Your first launch asks which you want, and you can change it any time in Settings, under **Compact shape**. The full view and the log always look the same.

![The three compact shapes: Square, Wide and Bar](docs/screenshot-compact.png)

- **Square:** the narrow card, with the big EXP/hr number, mesos/hr, the level bar and Start/Pause.
- **Wide:** longer sideways, with the level bar on top and four numbers in a row: EXP/hr, last 5 minutes, mesos/hr and the time to your next level.
- **Bar:** one thin strip with EXP/hr, mesos/hr, the time to your next level and a slim level bar. It shows a status line only when something needs your attention.

## Hotkeys

They work while MapleStory (or the overlay) is the window in front, so you don't have to click away from the game:

| Keys | What they do |
|---|---|
| **Shift+1** | Start / Pause |
| **Shift+2** | Reset |
| **Shift+3** | Log this run on / off |
| **Shift+4** | Compact / Full |

The status line says what happened, for example "Shift+1: paused." While the hotkeys are on, those keys go to the overlay only, so Shift+1 won't type **!** in the game's chat. If that gets in your way, change them (or turn them off) in ⚙: click one, then press the keys you want (click it again, or press Esc, to cancel). Backspace turns one off.

## The log

Click the **log icon** (top left, next to the status pill) to open the log. It's the table on the right in the close-up picture above.

- **After 5 minutes of non-stop farming**, it adds a line for that map, your level and your party size.
- **Every 5 more minutes** it updates that line, so the numbers get more accurate the longer you farm.
- **Time** shows how long a line is based on. It grows by 5 minutes with each update.
- **Class** is read from the status bar (never your character's name). Different classes get separate lines.
- **15-minute runs:** under the log it also counts toward 15 minutes of non-stop farming on one map with the same party size ("15-min run: 7 of 15 min"). A level-up doesn't stop it; a break, a new map or a new party size starts it over. Finished runs are saved separately and, if you said yes, shared with the [best-maps site](#best-maps-for-your-class) (only clean ones, see there).
- **Greyed-out lines** were changed outside the overlay. Every line the overlay writes is sealed, so a line edited or added by hand is easy to spot, and it won't count toward the best-maps site. Deleting a line, or renaming a map in the overlay, is fine.
- **Taking a break restarts the 5-minute timer.** A break means no EXP for 15 seconds (you can change this in ⚙). Changing maps or party size restarts it too.
- **Don't want a run logged?** Turn off **Log this run**. It switches back on its own when that run ends.
- **Solo** means just you. **4ppl** means a party of 4.
- **?** under Mesos/hr means your inventory wasn't looked at.
- **Net/hr** is mesos/hr minus your potion cost, when potions are counted.
- **Right-click a line** to:
  - **Copy line**, to paste it into Discord or anywhere else
  - **Rename map**, if it read the name wrong (it remembers the fix for next time)
  - **Delete entry**
- **Playing in a party?** A map's party sizes on one day share one line (e.g. **4–5ppl ▸**), with the numbers over all that time. Click it to see a line for each party size.
- **Notes:** click a line and write whatever you like about it in the box under the log: the party, the pots, the loot, why you left. A ✎ in the **Note** column marks the lines that have one. Notes save as you type, and **they only ever stay on your PC**, in your log file: they are never shared with the best-maps site (only the finished 15-minute runs are, and they have no notes). Renaming a map, updating the overlay, or farming more on a line keeps its note. Deleting a line deletes its note too, and asks you first.
- **Can't see a whole column?** Drag the lines between the column titles to make columns narrower or wider. While the log is open, you can also make the window bigger by dragging its edge. It remembers both.

## Settings (⚙)

- **Pause tracking after no EXP for...** How long you can go without EXP before it pauses by itself. This also restarts the log's 5-minute timer. The default is 15 seconds.
- **Window transparency.** Makes the overlay see-through, up to 40%. (More and it could fade out of sight, with Settings in it.)
- **Size.** Makes the text and the whole window smaller or bigger, from 60% to 200%. The smallest text stops at a readable size, so it never gets too small to read. It's sized for each screen, so a window moved to a screen with different Windows scaling (like a 5K and a 1440p screen) looks the same size on both.
- **Compact shape.** Square, Wide or Bar: how compact mode looks. See [Compact shapes](#compact-shapes).
- **Potions.** Turn net mesos and potion counting on or off, and type your own prices under **Prices**. See [Net mesos and potions](#net-mesos-and-potions).
- **Level fallback.** Only for an EXP bar that shows just a percentage, when your level can't be read from the badge: type your level here. See [How accurate is it?](#how-accurate-is-it).
- **Hotkeys.** Turn them on or off, and change them. See [Hotkeys](#hotkeys).
- **Always on top of other windows.** Keeps the overlay above the game. It's on by default.
- **Check for updates when it starts.** On by default. See [Updates](#updates).
- **Share my 15-minute runs.** See [Best maps for your class](#best-maps-for-your-class).
- **Send daily health reports.** Once a day, a short anonymous summary of how well the overlay reads your game. See [Privacy](#privacy).
- **Open data folder.** Opens the folder where your log and settings are saved.
- **Check for updates.** Checks right now.
- **Report a bug.** See below.

## Net mesos and potions

Mesos/hr is what you pick up. **Net mesos/hr** takes off what the potions you drink cost, so you can see which map really pays. Example: 300,000 mesos/hr, minus 16,600 in potions (200 Red Potions x 50 + 30 Blue Potions x 220) = **283,400 net mesos/hr**.

- **Put the potions you use on the quick-slot bar** (the hotkey slots at the bottom right of the game) so the overlay can see their counts. When a count goes down, that's one used. Potions only in your inventory window aren't seen.
- **It counts only while tracking.** Paused or in town, nothing counts. Picking up or buying more potions is never counted as using them.
- **Prices are the NPC shop prices.** Potions no NPC sells count as free until you type what you pay, in ⚙ under **Settings > Potions > Prices**. You can change shop prices there too.
- **Don't want it?** Switch it off under **Settings > Potions**. Nothing is counted and no net is shown.
- **Your potion data never leaves your PC.** It isn't sent to the best-maps website.
- **Mesos/hr leaves out shop, storage and trade changes,** and your inventory doesn't have to stay open: open it at the start, now and then, and when the overlay asks (for example after a trip to town).

## Best maps for your class

**[mcwo.app](https://mcwo.app)** shows which maps give the most EXP/hr and mesos/hr for your class and level, solo or in a party, from real hours farmed by players. Click **Best maps** at the top of the overlay to open it, already filled in for you. Other apps can use the same numbers via the public [Partner API](https://mcwo.app/partners.html) (`/api/v1/…`, CORS open, credit mcwo.app).

- **Enter your level and whether you're solo or in a party, then pick your class and path.** Paths are named after their 4th job, like Night Lord or Bishop, and include the jobs before it (Shadower is Bandits, Chief Bandits and Shadowers). Under Lv 30 you're still 1st job, so Path is greyed out and you see what every player of your class gets.
- **Around your level** starts at 3 levels either way. If a map doesn't have enough players that close, it widens to 5 levels, and the row says so ("wider range"). Parties of any size count together.
- **The top 5 maps show first.** Click **Show more maps** under them to see the rest.
- **Each map shows** its EXP/hr and mesos/hr, and how many players and hours of farming it's based on. The dots on each line are players' typical runs, coloured by how their level compares to yours.
- **Include new players:** a new player's runs only count after 24 hours and 4 runs, so the site starts strict. Tick **Include new players** to see them too. Those maps are labelled, the new players' dots are faded, and maps with a name the game doesn't list are never shown from new players.
- **Sort and filter** the list, open **Details** on a map (EXP/hr by party size and by level), or switch to the **Leveling ladder** to see which maps to use as you level. **Copy link** shares exactly what you're looking at.

![The best-maps website, with a map's details open](docs/screenshot-site.png)

- **It's built from clean 15-minute runs:** 15 minutes of non-stop farming on one map with the same party size. The log shows your progress ("15-min run: 7 of 15 min"). For the site, 15 seconds without EXP ends a run (whatever your log's setting), and a run where a shop, storage or trade window was open is never shared.
- **Mesos only when they can be trusted:** a run is sent with mesos only if you looked at your inventory at the start and within 30 seconds after the end. Otherwise it is sent with EXP only. If you pause right after a run, the overlay may ask you to open the inventory.
- **Sharing is your choice.** The first time you start the overlay, it asks whether to share your runs. You can change it any time in ⚙.
- **What's shared:** the map, your level, class and party size, the EXP and mesos you got (EXP only if the mesos couldn't be trusted), and a random ID for your copy of the overlay. Never your character's name, and never anything about potions.
- **Keeping it honest:** the site uses the typical (median) run, counts each player once, leaves out runs far from what other players get there, a new player's runs only count after 24 hours and 4 runs, and a map is only ranked once 3 different players have farmed it (before that, it's early data: hidden unless you untick **Hide early data**, and greyed out). Every player's typical run is a dot on the chart, so anything odd is easy to spot.

## How accurate is it?

It double-checks everything it reads before counting it:

- **EXP:** the number and the % have to agree with each other, and with the EXP your level needs.
- **Mesos:** a new amount has to show up in several readings in a row.
- **Sudden jumps:** an EXP jump or a level-up has to be confirmed before it counts. A mesos amount that looks like an extra or missing digit is ignored.
- **Dying:** the EXP you lose isn't taken off your EXP/hr. It says how much you lost, then carries on from there.
- **When in doubt, it waits.** If it can't read something clearly, it skips it instead of guessing.
- **A bar with only a percentage:** past about Lv 57 the game stops showing the EXP number. The overlay then reads your level from the orange **LV** badge and works EXP out from it, so those numbers are estimates and show a ≈. If the badge can't be read it uses your level from before (only for the same character), or you can type it in Settings. These estimates are never shared with the best-maps website.

Every version is tested against thousands of misread numbers, and against real screenshots of the game pasted over many different backgrounds at different window sizes.

## Something not working?

**Click View capture first.** It shows exactly what the overlay read, with a box around every digit:

![View capture](docs/screenshot-capture.png)

| Problem | Try this |
|---|---|
| An error pops up when you open Maple Classic Exp Overlay.exe | Extract the zip first (step 3 above). It can't run from inside the zip or away from its folder. |
| "MapleStory window not found" | Start the game first. If it's minimized, bring it back up. |
| "Can't find the EXP bar" | Make sure nothing covers the EXP bar at the bottom of the game, including the overlay. If the game window is very small, make it bigger. |
| View capture shows the wrong part of the screen | If the button says **Follow game**, click it. |
| Mesos says "open inventory" | Open your inventory, and make sure the mesos at its bottom aren't hidden. In small game windows the chat bar can cover them, so drag the inventory up a bit. If it still doesn't show, stand still for a moment so the inventory stops being see-through. |
| The log says "Map not read yet" | Make sure the minimap is open at the top left of the game, and the overlay isn't covering it. |
| "...a file is missing from the overlay's folder (tessdata\eng.traineddata)" | Download the zip again and extract all of it. If the file is still missing, your antivirus may have removed it: allow the overlay's folder and extract again. EXP and mesos work without it. |
| "...its name has unusual characters" | Move the overlay's folder to a simple place like C:\Games\ (no accents or non-English letters in the path). EXP and mesos work without it. |
| The map name is wrong | Right-click the log line and choose **Rename map**. |
| "This window is covering..." | Drag the overlay to another spot. |
| Numbers stopped updating, or anything else | Please report it (see below). |

**To report a bug,** click **Report a bug** in ⚙ (or **Something's wrong? Report a bug** in View capture) and pick a way:
- **Send to the developer** (no account needed): write what went wrong, open *What will be sent* to see exactly what goes (text only, no pictures), and click **Send**. You can send up to 3 a day.
- **Report on GitHub** (you need a free GitHub account, and you can add pictures):
1. Your browser opens a bug report on GitHub, already filled in with your version and what the overlay was reading. Nothing personal is included, and nothing is sent until you click Submit.
2. A folder opens with **bug-report.png** (what the overlay read: the EXP bar, mesos, class and map name) and **bug-report.txt** (how it's doing: your screen and game window size, what it found, what it last said, and any errors). Drag both into the report. Neither has your character name or Windows user name. It also asks if you want to save a small picture of the whole game window: that one can show chat and names, so it's saved only if you say yes, and you choose whether to attach it.
3. Write a sentence about what went wrong, and click **Submit**. (You need a free GitHub account.)

## Is it safe? Is it allowed?

**Only download this overlay from this repository's [Releases](https://github.com/beanguri/Maple-Classic-Exp-Overlay/releases).** Other people have copied the code (that's allowed, it's open source), but their copies aren't mine and I haven't checked them. Mine are built by GitHub, and the steps below show how to check that.

- **It only looks at your screen, like a screen recorder.** It never touches the game's files or memory, and never presses keys or clicks for you.
- **It only goes online for these things:** asking GitHub whether there's a newer version when it starts; sharing your finished 15-minute runs, only if you said yes (see above); the daily health report, only if you said yes; and a bug report, only when you click **Send to the developer** yourself. Nothing about who you are is sent. Each of the first three can be turned off in ⚙. See [Privacy](#privacy).
- **Your antivirus might warn about it.** Antivirus programs sometimes flag small new programs by mistake. All of the code is public right here, so anyone can check what it does.
- **It isn't made by or affiliated with Nexon.** Like any third-party tool, you use it at your own risk.

## Privacy

- **Your log, your notes and your settings stay on your PC**, in `%APPDATA%\MapleEXP` (`~/Library/Application Support/MapleEXP` on a Mac). Notes are part of your log file: they are never sent anywhere, and the overlay's tests check that.
- **A copy of your log is kept for each day** (the last 7 days), in the `backups` folder next to it, so if anything ever goes wrong there's a recent one. To go back to one, close the overlay and copy it over `exp_log.json`.
- **Potions stay here too.** Your potion counts, prices and costs are never sent anywhere.
- **The update check** (on by default, can be turned off in ⚙) asks GitHub for the newest version and what's new in it. GitHub sees your internet address and the overlay's version, like any website you visit; nothing else about you is sent.
- **Buttons that open your browser** (Best maps, Report on GitHub, the update's download page) only do it when you click them. Best maps puts your level, party size and class in the website's address, so the website sees them.
- **15-minute runs are only shared if you said yes.** Each one is the map, when you farmed it, your level, class and party size, the EXP and mesos you got (EXP only if the mesos couldn't be trusted), the overlay's version, and a random ID made by your copy of the overlay. Never your character's name, your game account or your Windows user name.
- **Daily health reports are only sent if you said yes** (you're asked once; you can change it any time in Settings). Once a day the overlay sends numbers about the day before: the overlay's version, your Windows or macOS version, your screens' sizes and scaling, the game window's size (and the height of the EXP digits on screen), how the overlay captures the screen (whole screen, game window or selected area), whether its text reader (Tesseract) works, how many times it looked at the game and how each look went (for example how often it found the EXP bar), how many times the level, class, map name, mesos and potion readers worked, how long a look takes, how long you tracked, how often the new test reader agreed with the current one and how long it takes, and the names of any errors (just the kind, like `TesseractError`). Never your character's name, chat, maps, class, amounts, pictures, file paths or your log. It carries a random ID made only for these reports (kept in your settings, and not the same as the ID on shared runs, so the two can't be joined). The website keeps them for 90 days, never shows them on the site, and uses them only to find and fix reading problems. The numbers about potions are only how often the overlay could read the potion bar, not how many you used.
- **The website keeps** those runs. To limit spam it also keeps a scrambled code made from your internet address with a secret key, which changes every day and can't be turned back into the address. Your address itself isn't kept.
- **Bug reports** only go to GitHub if you click Submit there yourself, or to the website if you click **Send to the developer** (you see exactly what is sent first). Send to the developer is text only: what you typed, plus the same details text as the GitHub report (your version, screen and game window sizes, what the overlay found, the map name and class it read, its settings, what it last said, and the end of its error list), with file paths cut down to the file name and your Windows or Mac user name and computer name taken out. No pictures, no chat. It carries the random ID used only for reports (the same one as the daily health report, not the one on shared runs), up to 3 a day. The website keeps it for 90 days, never shows it on the site, and reads it only to fix problems.

## Where the download comes from

Downloads on the [Releases page](../../releases) are built by GitHub from the code in this repository ([Build workflow](.github/workflows/build.yml)), not on anyone's own PC. Each build:

- installs exact package files, each checked against its SHA-256 ([requirements-build.txt](requirements-build.txt)),
- runs all the tests,
- records a signed **attestation**: which workflow built which file, from which commit,
- puts the zip's **SHA-256** on the release page (`SHA256SUMS.txt`).

**Check your download** with the free [GitHub CLI](https://cli.github.com):

```
gh attestation verify "the zip you downloaded.zip" --repo beanguri/Maple-Classic-Exp-Overlay
```

It tells you whether that exact file was built here, and from which commit. Or compare the file's SHA-256 with `SHA256SUMS.txt` on its release page (in PowerShell: `Get-FileHash "the zip you downloaded.zip"`).

The same zip is also kept on the page of the build that made it (Actions, the run, the file at the bottom; you need a GitHub account to download it). Build runs since October 17 keep it exactly as it is, so the SHA-256 GitHub shows there is the zip's own, the same as on the release page.

**About v0.9.6:** it was built by GitHub from its tag, before the package checks and attestations were added, so it has no attestation. Its SHA-256 is on its release page. [Its build's page](https://github.com/beanguri/Maple-Classic-Exp-Overlay/actions/runs/38631744258) keeps the zip inside an extra zip (GitHub does that unless told not to), so the SHA-256 shown *there* is for that outer zip and won't match: extract it once, and the zip inside has the release's SHA-256. That page keeps it until January 2027. Versions before v0.9.6 were built on my PC.

### Code signing policy

Free code signing from [SignPath Foundation](https://signpath.org) is planned, so Windows stops warning about new downloads. Only builds made by the Build workflow from this repository are signed.

- Committers and reviewers: [beanguri](https://github.com/beanguri)
- Approvers: [beanguri](https://github.com/beanguri)
- Privacy policy: see [Privacy](#privacy) above. In short: your log and notes stay on your PC, and the overlay only sends anything to another system if you turn on sharing your finished 15-minute runs or the daily health report (both off until you say yes, and you can turn them off any time in Settings), if you click **Send to the developer** on a bug report, or to check GitHub for a newer version (can be turned off too).

**Credits:** the list of the game's map names (`assets/maps.txt`) comes from [meowdb.com](https://meowdb.com/msclassic/maps/all), a fan-made MapleStory Classic database (thank you). The typeface is [Manrope](https://github.com/googlefonts/manrope) (SIL Open Font License, included as `assets/fonts/OFL.txt`).

## Updates

This is a beta, and I'm still adding features and fixing things.

**New name:** since v0.9.3 it's called Maple Classic Exp Overlay. It used to be Maple EXP/hr. Your log and settings carry over.

**When a new version is out,** the overlay tells you when you start it, and shows what's new. Click **Get the update** to open the download page, or **Not now** / **Skip this version** if you'd rather wait.

**To update:** download the newest zip from the [download page](../../releases/latest) and use it instead of the old folder. Your log, notes and settings stay, because they're saved separately (⚙ → **Open data folder** shows where).

**To remove it:** delete its folder. To also delete your log and settings, delete the folder that **Open data folder** opens.

See [CHANGELOG.md](CHANGELOG.md) for what changed in each version.

---

<details>
<summary><b>For developers</b></summary>

**Run from source.** You need Python 3.10 or newer, and [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) for map names.

```
py -3 -m pip install -r requirements.txt
run.bat
```

**On macOS** (from source only, with the game running through CrossOver, Whisky, Parallels or a game stream): macOS support was written by thanhphamvan, who tried it on a real Mac with the game. I don't own a Mac, so I can't test it myself: GitHub's automatic tests run on a real Mac, but not with the game. If you try it, please tell me how it goes.

**How to run it on a Mac:** `brew install tesseract`, then `python3 -m pip install -r requirements.txt` and `python3 maple_exp.py`. The first time, allow your Terminal (or Python) in **System Settings > Privacy & Security > Screen Recording**, then start it again. The window is found by "MapleStory" in its title; if it isn't, use **Select area**. Hotkeys aren't available on macOS yet.

Everything that differs between Windows and macOS (finding the game window, screen scaling, hotkeys, the one-copy-at-a-time lock) is in `platforms/`: one `Platform` interface in `base.py`, one implementation per OS, and `create_platform()` picks the one for the OS it's on.

**Build the download** (puts `Maple-Classic-Exp-Overlay-v<version>.zip` in `release/`, and a copy to use right away in the project folder: `Maple Classic Exp Overlay.exe` with its `_internal` folder):

```
py -3 -m pip install -r requirements.txt pyinstaller
py -3 build.py
```

**Testing never shares anything:** with `MAPLE_EXP_DATA` set (it moves the data folder, which is how the tests keep away from your real log), the overlay refuses to send runs to the real site, so made-up test runs can't end up there. (Set `MAPLE_EXP_ALLOW_SHARE=1` only to test against a local copy of the server.)

**Run the tests:**

```
py -3 tests/test_logic.py
py -3 tests/test_screen.py
py -3 tests/test_ui.py     # opens the real window for a few seconds (the notes box)
```

To build with exactly the packages the releases use: `py -3 -m pip install --require-hashes -r requirements-build.txt`.

GitHub runs the logic tests on every push ([Tests workflow](.github/workflows/tests.yml)). The [Build workflow](.github/workflows/build.yml) builds the download on GitHub (Actions tab, **Build**, **Run workflow**), runs all the tests first, and signs the .exe once code signing is set up. Pushing a version tag also attests the zip and drafts the release with it; releases are only ever made that way.

**The stats site** is in `server/`: a Cloudflare Pages site with a D1 database (free tier). The page is `server/public/`, the API is `server/src/index.js`. `npm --prefix server install`, then `npm --prefix server run dev` runs it locally, `py -3 server/test_server.py http://localhost:8787` tests it, and `npm --prefix server run deploy` puts it online.

**How it reads the screen:**
- It finds the `MapleStory.exe` window, then the EXP numbers (like `12,345 [12.34%]`) in the status bar.
- Each digit is resized to a standard size and compared against saved examples of the game's font in `assets/`.
- A reading only counts if the EXP and % agree with how much EXP that level needs, so misreads are ignored.
- Mesos are read the same way. The mesos box is found by its outline, which also works while the inventory is see-through.
- The party size is the number of HP bars in the party list.
- Map names are read with Tesseract.
- Settings, the log and `errors.log` are saved in `%APPDATA%\MapleEXP` (`~/Library/Application Support/MapleEXP` on macOS; from its old name, Maple EXP/hr, so updates keep them).

</details>

## Credits

- The Classic World EXP table is from [meowdb](https://meowdb.com/msclassic/guides/exp-table-level-1-to-100). It's used to work out your level.
- Map names are read with [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (Apache 2.0 license), which is included in the download.

## License

[MIT](LICENSE): free to use, change and share.
