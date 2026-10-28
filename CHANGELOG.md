# Changelog

## Unreleased

**Partner API (best-maps site)**
- Public JSON for third-party apps: `GET /api/v1/maps`, `/api/v1/ladder`, `/api/v1/options`, `/api/v1/recent` (CORS open, no key). Slim payloads without chart dots; every answer includes attribution for mcwo.app.
- Docs page: [mcwo.app/partners.html](https://mcwo.app/partners.html). Existing `/api/stats` etc. unchanged for the site itself.

## 0.9.13-beta

**Fewer reading problems**
- One part that can't be read (map name, class, mesos, potions, level badge) never stops the others: EXP keeps reading, and the status line says which part is having trouble.
- A stuck or missing Tesseract (the text reader) no longer freezes the EXP display, and the message says what's wrong and how to fix it (a missing `eng.traineddata`, or a folder name with unusual characters).
- The overlay keeps starting even if a folder on your PC can't be looked into, and the reading tries again if Windows briefly refuses the screen capture.

**High levels (a bar with only a percentage, about Lv 57+)**
- Leveling up from the last level with an EXP number to the first one with only a % no longer gets stuck on "level changed".
- Runs at these levels count in the log and on the overlay (they showed 0). They're still never shared.
- A window over the LV badge now says so and offers Settings, Level fallback; Reset and switching to Select area forget the old level.
- A partly covered name row no longer starts the session over by itself.

**Health reports (your choice, off until you say yes)**
- Once a day the overlay can send numbers about how well it reads your screen: how many looks read fine, screen and game size, how long a look takes, which error types happened. Never pictures, names, chat, map names or your log. It uses its own random ID, not linked to shared runs. It asks once; change it any time in Settings. See Privacy in the README.

**Send a report without GitHub**
- Report a bug, then **Send to the developer**: type what went wrong and send it. It shows exactly what is sent first. Text only, at most 3 a day.
- Bug reports (both ways) now remove your Windows user name and folders from file paths in error messages too.

**A new EXP reader, tested quietly**
- A new way to find and read the EXP bar (by shape, not colour) runs in the background and only notes whether it agrees with the current one. It changes nothing you see; if it costs too much CPU on your PC it slows down or switches itself off.

## 0.9.12-beta

**Hotfix: an EXP bar that shows only a percentage**
- Players at high levels (about Lv 57 and up, where a level needs a million EXP or more) reported that the overlay found no EXP bar. From there the game shows only the percentage, like `EXP. [58.47%]`, with no EXP number, and the overlay needed the number. It reads that bar now.
- Your level comes from the orange **LV** badge on the status bar. EXP is then worked out from your level's EXP and the percentage, so EXP/hr, last 5 minutes, gained and current are estimates, and show a ≈ in front (in your log too).
- If the badge can't be read, it keeps your level from before the number went away, but only for the same character: it compares a small picture of the name row, which only lives in memory. It never reads, saves or sends your character's name.
- If that fails too, **Settings, Level fallback** lets you type your level. It lasts for this session only.
- Estimates stay on your PC: a run made from a percentage alone is never shared with the best-maps website.
- Fixed an error in the status line before the first EXP reading.

## 0.9.11-beta

**Mesos/hr you can trust**
- Mesos you spend or move at an NPC shop, storage or a trade are left out of mesos/hr (the status line says what was left out). Only mesos you pick up count.
- The inventory doesn't have to stay open: the first look sets the start, a later look adds what you earned since, and mesos/hr holds still while it is closed.
- After a trip to town, open the inventory when it asks, so what you earned before the trip is kept.
- Your log shows a stretch's mesos from the first to the last look at the inventory, however far apart.

**Potion cost and net mesos/hr: what you really make**
- Mesos/hr says what you pick up. **Net mesos/hr** takes off what the potions and food you drink cost, so you can see which map really pays. Example: 300,000 mesos/hr, and in that hour 200 Red Potions (50 each) and 30 Blue Potions (220 each) = 16,600 in potions, so net is **283,400 mesos/hr**.
- **Keep the potions you use on the quick-slot bar**, the hotkey slots at the bottom right of the game, so their counts show. The overlay reads that bar every 3 seconds: when a count goes down (326 to 325), that's one used. Potions that are only in your inventory window aren't seen. Skills, empty slots and other items (stars, scrolls, pet food) are ignored.
- **It counts only while tracking is on** and EXP is coming in. Paused or in town, nothing counts. Buying or looting more of a potion (its count going up) is never a use. Reset starts the count again from zero.
- **Prices** are the NPC shop prices (Red Potion 50, Orange 150, White 350, Blue 220, Elixir 3,000...). Potions no NPC sells (Mana Elixir, Power Elixir, Sap of Ancient Tree, Drake's Blood...) count as free until you type what you pay for them: **Settings, Potions, Prices**. If you set a shop potion you used to 0, net shows a dash and the status line asks for a price.
- **Where you see it:** a Net / hr tile in the full view (next to Last 5 min, Mesos / hr and Elapsed), Potion cost and Net mesos / hr in Details, a Net/hr column in your log, and a small "net" in the Square, Wide and Bar shapes. It shows the cost in mesos, never a count of potions.
- Don't want it? Switch it off in **Settings, Potions**: potions aren't counted and no net is shown.
- Potion data stays on your computer: nothing about it is sent to the best-maps website.

**Clean runs for the best-maps website**
- A run counts for the website only after 15 minutes on one map without a break: 15 seconds without EXP ends it, whatever your log's gap setting (your log still uses your setting).
- A run where a shop, storage or trade window was open is kept in your own records and never shared.
- A run is sent with mesos only when the inventory was looked at when it started and within 30 seconds after it ended. Otherwise it is sent with EXP only. Mesos that look impossible (below zero, over 20M an hour, or a reading that later snapped back) are never sent.
- If you pause right after a run while it waits for that look, it asks: "Open the inventory now so your run is logged for the website." It waits 7 seconds.
- The website now ignores mesos it can't trust (from older overlay versions, a week after this release), and its map figures use the last 60 days.

## 0.9.10-beta

**When the minimap's name is covered**
- After 3 seconds without the minimap's name, the status line says so ("Can't see the minimap's name. Still tracking...") and says when it is back. A short cover changes nothing in your run; it still pauses after 45 seconds.
- While the name is hidden, nothing new is saved to the log. If the name comes back on the same map, the hidden time counts. If it comes back on a different map, the stretch that was in progress is dropped (stretches already saved stay as they were), so the new map's EXP is never saved under the old map.
- It looks for a missing name every second (was every 3), and retries an unreadable one every 2 seconds (was 5).

**Compact shapes and what's new**
- **Compact mode has three shapes:** **Square** (as before), **Wide** (longer sideways, with a row of four numbers like a streamer's tracker) and **Bar** (one thin strip). Pick one in Settings, under Compact shape. The full view and the log are unchanged.
- **A new install picks its shape on the first launch**, from three small pictures.
- **A short "What's new" window** shows once after you update to a new version (never on a first install).

**New address**
- The best-maps website is now at **mcwo.app** (opening the old maple-classic-exp-overlay.pages.dev page now sends you there; older overlays keep working on the old address for now). The overlay's Best maps button and sharing use the new one.

**Cleaner map names**
- A map's name is now put right against the game's own list of maps (from meowdb.com, thank you): OCR slips like "ist Accompaniment" for "1st Accompaniment" or "Boar Il" for "Boar II" become one map, in your log and on the best-maps site. A map that isn't on the list (an event map, a new map) is used as it was read.
- **The minimap's name can't be seen** (hidden, covered): after 45 seconds the overlay pauses instead of carrying on under the map you were on before, and carries on when it can read the name again. (This is likely why a town like Perion could end up in people's logs.)
- **Towns** aren't logged ("In a town: nothing is logged here"), and the site turns town runs down.

**The best-maps website: harder to fake**
- A new player's runs start counting a day after their first, and after 4 runs, so made-up players can't change the numbers at once. Runs from a banned player are refused and hidden.
- Runs are checked before anything is stored: the job must be a real job, the map name plain English text (adverts, links and look-alike letters are turned down), and EXP and mesos must be believable for the level.
- One home's IPv6 addresses count as one connection, a day's new players and runs have a limit (so a flood can't use up the database), and nobody farms more than 12 different maps a day.
- A map name the game doesn't list shows only once 3 players have shared it on 2 different days.

**The best-maps website**
- **Include new players.** A new box in Best maps and the ladder shows the players who joined lately and don't count yet (they count for everyone after a day and 4 runs). Maps that use them say so, their dots are faded, and the header shows how many are new. Off by default; map names the game doesn't list never come from new players.
- **A closer level range.** A map's numbers start from players within 3 levels of yours, and go to 5 or 8 only if the map needs more players to be ranked (the row says "wider range").
- **% of a level per hour and time per level** on every map ("about 14 h per level"), which compares players at different levels.
- **Sort** by EXP/hr, %/hr or mesos/hr, and **hide early data**.
- **Details** under every map: solo vs party, EXP/hr at each level, and the last 7 days against before.
- **Leveling ladder:** the best map for each 5 levels of your class and path.
- **Copy link**: the address always holds your level, class, path and sort, so a view can be shared.
- It tells you when the newest run was shared.
- **Maps at your level come first**: maps that had to use players from further away (up to 8 levels) are listed under their own "Wider range" heading.
- **Dots are coloured by the player's level** against yours (4+ lower, 1–3 lower, your level, 1–3 higher, 4+ higher), so you can see which level the high-EXP players are at, and hovering a dot or a level bar in Details says the level and number of players.

## 0.9.9-beta

**New**
- **Smaller sizes:** Settings > Size now goes down to 70% and 60% (it was 80%). The smallest text stops at a readable size.
- **Transparency stops at 40%.** More and the window could fade so far that you couldn't find Settings again. A higher saved value becomes 40%.

**Fixed**
- **Hotkeys in Settings:** clicking one to change it could leave several looking selected (and the first click could fail with an error). Now only one waits for keys at a time: clicking another moves it, clicking the same one again (or Esc) cancels, and hotkeys come back right away if you turn them off or click away from Settings.
- A long number in the three small panels (like 99,999,999) now shrinks to fit instead of running out of its box.
- **Select area** crashed on Windows ("expected integer but got bold") since 0.9.7. It opens again.

**Better bug reports**
- The report now also saves **bug-report.txt**: your screens and Windows scaling, the game window, where each part (EXP bar, mesos, minimap, party list) was found, how the reading thread is doing, what the overlay last said (with times), and the end of errors.log. At the top it lists what looks wrong, like "the overlay is covering the class name".
- The picture now also shows where the class and map name are read, with a line on how each went. View capture shows the same, and says why the class wasn't read.
- Optionally (it asks) a small picture of the whole game window, saved only if you say yes.

## 0.9.8-beta

**Lighter and more reliable**
- **Far less CPU:** the overlay used a thread per core for tiny pictures, and the threads mostly waited on each other. It now uses one. In a test on a 1080p game window that took a normal look from about 100 ms of CPU to 12 ms, and the look at the whole window (every 4 seconds) from about 350 ms to 60 ms. How much you gain depends on your core count; the more cores, the more.
- Reading the map name and your class asks Tesseract once instead of six times (about 4.5 times faster), with the same answers.
- **Storage and trade windows:** they show a second mesos box next to yours, and the overlay could read that one instead. It now sees there are two, reads neither, and picks yours up again once the window closes (thanks to Xoere, whose recording showed it).
- Map names and classes now also work when your Windows user name has letters your PC's text code page can't hold (some non-Latin names): Tesseract couldn't open its pictures there, so they were never read.
- `pytesseract` isn't used any more: one less package in the download.
- Thanks to thanhphamvan, whose fork's measurements pointed at the extra threads and the six Tesseract runs.

**A new look**
- The whole overlay is redrawn: a big glowing EXP/hr, a live trend line (is your pace rising or falling vs the last 5 minutes?), a progress bar to your next level, a status pill (looking for the game / ready / tracking / paused), and one clear Start button. The title bar matches the window (Windows 10 and 11).
- Less clutter: three key numbers up front (last 5 min, mesos/hr, elapsed); everything else is one click away under **Details**, and it remembers if you opened it.
- "Don't log" is now a switch called **Log this run** (no more double negatives). Select area and View capture are under the **...** menu.
- Log, notes, Settings and the update window match, with a bundled typeface (Manrope, SIL Open Font License), real switches, a gold slider and thin scrollbars.
- **Compact mode shows mesos/hr** too, under the EXP/hr number, in the same window size.
- Just as light as before: the numbers move pre-drawn pictures instead of redrawing, and nothing is redrawn while nothing changes.

## 0.9.7-beta

**New**
- **Notes on your log lines:** click a line and write a note about it in the box under the log. A ✎ in the new **Note** column marks lines that have one. Notes save as you type and **stay on your PC only**: they live in your log file, are never shared with the best-maps site, and the overlay's tests check that nothing the overlay sends can include them. Renaming a map (even when it merges two lines), farming more on a line, and updating keep your notes. Deleting a line asks first, and says its note goes too. Typing in the box doesn't trigger the hotkeys.
- **A backup of your log each day** (the last 7, in a `backups` folder next to it), so a recent copy of your log and notes is always there.
- **One log line per party**: playing a map in a party, people join and leave, and each party size used to get its own line. Now a map's party sizes on a day share one line (e.g. "4–5ppl ▸"), with EXP/hr and mesos/hr over all of that time. Click it to see a line for each party size, to compare them. Solo play keeps its own line. Your log itself isn't changed, only how it's shown.
- **macOS** (running from source; thanks to thanhphamvan): works on a Mac with the game in CrossOver, Whisky, Parallels or a game stream. It finds the game window by itself, as on Windows. See "For developers" in the README. Hotkeys aren't available on macOS yet. It has only been tried with the game by its author; the maintainer has no Mac, so reports are welcome.

**Fixed**
- Mesos: a small change across a digit boundary, like 9,980 to 10,020, is now counted instead of dropped (thanks to TaylorAdamCA).
- Typing "!" in a note or a map name no longer triggers the Shift+1 hotkey.

**Safer**
- Downloads are now built by GitHub from this code, never on a PC, from exact package files checked against their SHA-256. Each one gets an attestation anyone can check (`gh attestation verify`), and its SHA-256 is on the release page and on the build's page (the same one in all three places). See "Where the download comes from" in the README.
- Built with Pillow 12.3, which fixes the problems published since 11.3. (The overlay never opens image files from elsewhere, so it wasn't affected, but now nothing is flagged.)
- It never runs a `tesseract.exe` from the folder it happens to be started from, only its own or an installed one.
- Sharing is off whenever the overlay runs in test mode (`MAPLE_EXP_DATA` set), so made-up test runs can never reach the real site.

**The best-maps website**
- Security headers: browsers only run the site's own scripts, and other sites can't show it inside theirs.
- Map names that are web addresses are turned down, and early data only shows map names at least 2 players have shared, so one person can't put a made-up name on the site.
- Much lighter on its database (small summary tables, and answers shared between Cloudflare's locations), so it keeps working as more people use it.
- Every player is a dot on a chart: each map's row shows what every player gets there, the typical EXP/hr, what the middle half of players get, and the runs left out as unusual. Point at (or tap) anything for the numbers.
- Maps with fewer than 3 players are shown greyed out as early data, instead of "No maps here yet".
- A bar at the top shows the latest runs players shared, one at a time; click one to see the best maps for that level, class and party. One run per player, only maps at least 2 players have shared, and nothing unusual, so made-up runs stay off the front page.

## 0.9.6-beta

**New**
- **Compact mode** (the Compact button at the top, or Shift+4): a much smaller window with just EXP/hr, when you'll level, the last 5 minutes, mesos/hr and Start/Pause. The button then says Full, to bring everything back.
- **Hotkeys**, while MapleStory or the overlay is in front: Shift+1 Start / Pause, Shift+2 Reset, Shift+3 Don't log, Shift+4 Compact. You can change them, or turn them off, in Settings.
- **Size** in Settings: makes the text and the window smaller or bigger, from 80% to 200%, and changes right away. It stops at 80%, so it can't get too small to read or use.

**Fixed**
- On a second screen with different Windows scaling (e.g. a 1440p screen at 125% next to a 5K one at 200%), the window was far too big. It's now sized for the screen it's on, and resizes when moved.
- The Settings window could stay at its old size after the size changed, cutting its contents off. It now always fits, and opens over the overlay (on the same screen).

## 0.9.5-beta

**Fixed**
- Dying (losing a few % of your EXP) made EXP gained and EXP/hr go down, even below zero. Now the loss isn't counted: it says how much you lost and carries on.
- A run the best-maps site turned down was sent again every 30 minutes, and enough of them could stop newer runs from being shared.
- A log file edited by hand (or damaged) could stop the overlay from starting. Lines it can't use are now left out (a copy of the file is kept), and it starts as usual.

**The best-maps website** (already live)
- Checks every run harder: no more EXP than the levels it covers hold, and start times in a sensible range.
- Only 6 players can share from one internet connection a day, so it's much harder to make up players.
- Other websites can't make their visitors' browsers send runs.
- The code kept for spam limits can no longer be turned back into an internet address.

## 0.9.4-beta

**New**
- **Best maps** link at the top of the overlay: opens the best-maps website already showing the maps for your level, party size and class.
- **Runs for the best-maps site are 15 minutes** instead of an hour, so more of your farming counts (a break or a new map no longer throws away up to an hour).
- **Smaller download:** 82 MB instead of 98 MB. Parts of the libraries it never used are left out.
- **The best-maps website** fills in sooner: it now looks 5 levels either way, and counts parties of any size together (solo or party).
- Downloads can now be built on GitHub straight from the code, the first step toward code signing (so Windows stops warning about new downloads).

## 0.9.3-beta

**New**
- **New name: Maple Classic Exp Overlay** (it was Maple EXP/hr). The program is now `Maple Classic Exp Overlay.exe`. Your log and settings carry over.
- **Class**: read from the status bar (never your character's name), shown in the log, and different classes get separate lines.
- **1-hour runs**: the overlay counts toward a full hour of non-stop farming on one map with the same party size, and saves each finished hour (these are what the best-maps site uses). A level-up doesn't stop a run; a break, a new map or a new party size starts it over.
- **Best maps for your class**: if you agree (it asks once), finished 1-hour runs are shared anonymously with the new stats site, maple-classic-exp-overlay.pages.dev, which shows the best maps for each class and path (named after its 4th job, like Night Lord or Bishop), level and party size. You can change this in Settings.
- **Sealed log**: every line the overlay writes is sealed, so lines edited or added by hand are greyed out, and an edited 1-hour run is never shared. Deleting a line, or renaming a map in the overlay, keeps everything else trusted.

**Fixed**
- The update window showed names with underscores wrong (e.g. explog.txt).

## 0.9.2-beta

**New**
- **Time** in the log: how long each line is based on, growing by 5 minutes with every update. Also in copied lines and `exp_log.txt`.
- **Report a bug** button (in Settings and in View capture): opens a bug report on GitHub, already filled in with what the overlay was reading, plus a picture to drag in. Nothing is sent until you click Submit.
- **Reset** now also looks for the minimap, inventory and party list again, so it picks them up after you move them.

**Fixed**
- The map name wasn't read when the overlay's window was right next to the minimap (but not covering it). The same is fixed for the party list.

## 0.9.1-beta

**New**
- **Pauses by itself** when you get no EXP for 15 seconds (you can change this in Settings) or leave the map, and carries on at your next EXP gain. Paused time doesn't count toward EXP/hr. On a new map, the numbers start from zero.
- **Don't log** button: keeps the current run out of the log while still showing everything. It turns itself off when the run ends.
- **Update check:** tells you when a new version is out and what's new, so you can decide whether to update. You can turn it off, or check by hand, in Settings.
- Your level shows within seconds of opening, and the time to your next level sits right under EXP/hr, like "Lv 31 in 36 min".
- The log's columns can be resized, and the window can be made bigger while the log is open. Both are remembered.

**Fixed**
- In a 1920x1080 window, the EXP number could be read with its first digits cut off.
- Mesos weren't read when the overlay's window was close to (but not covering) the inventory, or when a see-through inventory was in front of a busy background.

**More reliable**
- Much stronger protection against misread numbers, tested with thousands of simulated misreads and screenshots over many backgrounds: a misread can't pass for a level-up, a lost level, or extra mesos.
- Clearer status messages. "Select area" and "Follow game" are now one button.
- Safer saving: a damaged log file is backed up before anything is saved over it, and damaged settings fall back to the defaults.

## 0.9.0-beta

First public beta.

- Shows your EXP per hour, % per hour, EXP gained, and time to your next level.
- Finds the MapleStory window by itself, at any window size, windowed or fullscreen.
- Reads your mesos from the inventory, even while it's see-through during fights, and shows mesos per hour and per 5 minutes.
- Notices whether you're solo or in a party, and how many people are in it.
- Reads the map name from the minimap.
- Keeps a log of your EXP/hr and mesos/hr for each day, map, level and party size, updated every 5 minutes of non-stop farming.
- Settings for the break length, window transparency and always-on-top.
- Saves your settings and log in `%APPDATA%\MapleEXP`, and writes problems to `errors.log` there.
