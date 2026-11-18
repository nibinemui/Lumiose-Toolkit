# Patch notes

## 1.6.2 - 2026-11-17

### New
- **Search all tables…** (Game data tab): one search across every data table the toolkit can read - over 2,200 tables and 80,000 rows: moves, items, shops, clothing, hair, trainers, Pokémon stats, encounters, spawners, wild zones, model and AI parameters and more. Search by name or number: "Pikachu", "Tackle", "Poke Ball", "a0403_w01", "25 alpha". Pokémon, move and item numbers are searchable by their names, every word must match, a number matches whole numbers only, and accents don't matter. Double-click a result to open that row in the Game data tab; for tables that keep their rows in groups (encounters, spawners), the matching entry is selected too. Tables your current mod changes are searched in the mod's version (marked [mod]). The first search builds an index in about a minute; after that only changed tables are read again.

### Improved
- Mods tab: **My mods** and the mod's **file list** have scrollbars; the file list also scrolls sideways, so long paths are readable.
- Mods added, removed or changed outside the program show up without restarting it: **Refresh** (Mods tab, next to My mods) re-reads the mods folder, and the **Current mod** list re-reads it every time it opens.

## 1.6.1 - 2026-11-17

### Fixed
- **Numbers 90-99** (Preferences → Mods → Your number) made 5-digit model folder numbers and creating a new item or hairstyle failed. They now use folders 6000-6900. Numbers 60-89 are unchanged.
- **New hairstyle from this…** on a hairstyle that only exists in a mod gave a file error; it now asks you to start from one of the game's hairstyles.
- The **Test checklist** window no longer takes over mouse-wheel scrolling in the rest of the program.
- **Add original to mod** is no longer offered for files that only a mod has (there is no original).

## 1.6.0 - 2026-11-16

### New
- **New hairstyles** (Game data → **Hairstyles, hair colours & make-up** → pick a hairstyle → **New hairstyle from this…**): a new hairstyle made from an existing one - its own copy of the model, its hair animations, a new id, its name in every language, and the salons that offer it. Nothing in the game is replaced. The new table lists every hairstyle, hair colour and make-up option and can be edited and merged like the other tables.
- **Test checklist…** (Mods tab): a list of what to check in the game, made from what the mod changes - new clothing items and hairstyles (with the shop or salon), replaced songs and sounds by name, Pokémon whose data changed, changed moves, items, shops and other table rows, models, textures and text, plus the save / reload / online checks every test needs. Tick what works, then **Copy** or **Save as text…** to share it.
- **Check new items** (Mods tab): checks the new clothing items and hairstyles of a mod before you share it - ids not used by the game or your other mods, model folder, `.ccdata`, category / hairstyle list, names in every language, and which shop or salon offers each one.
- **Your own id series** (Preferences → Mods → **Your number**): when several people make new items, each picks a different number (60-99) and gets their own range of item ids, model folders and group numbers, so items made on different computers never clash.

### Improved
- **New clothing items work everywhere in the program**: Search finds them by name (with all their colour variants), **Try on** and **Show in 3D** render them in colour, **Edit colours…** opens, and the mod's own files (new models and textures) show up in search marked with the mod's name.

### Note
- New clothing items and hairstyles are tested in the program but not confirmed in-game yet - the test checklist lists what to try.

## 1.5.1 - 2026-11-16

### Fixed
- **New clothing items in different mods got the same item ids.** Each new item picked the first free number by looking only at the game and its own mod, so two mods could clash when installed together (merging then kept only one of the items). New items now skip the ids, model folder numbers and group numbers every mod in your mods folder already uses.

## 1.5.0 - 2026-11-16

### New
- **Mod merging**: when several mods change the same game data table (shops, items, clothing, moves…) or text file, **Build package(s)** and **Install to Recomp** merge them row by row instead of letting one mod overwrite the other. Rows each mod adds are all kept; only a field two mods change differently goes to the higher mod in the load order (listed in the build log). Identical copies are never a conflict. **Check conflicts** marks such files "Different - mergeable". Mods that each add items to the same shop now all keep their items.
- **New clothing items** (Game data → Clothing items → **New item from this…**): a new, buyable item made from an existing one - its own copy of the model, new item ids, its name in every language, a price and a shop. Nothing in the game is replaced. **Edit a texture…** live-edits its textures. Back up your save before trying new items: buying one stores its id in the save.
- **Textures say what they are**: every texture in Browse gets a plain-English description in the new **About** column and the preview header - which Pokémon, clothing item, character, building, map object or icon it belongs to, which part, and what it does (colour, bumps, colour regions…). **Search matches it**, so "pikachu body", "turtleneck", "poke ball icon", "billboard", "poster", "neon", "marble" or "statue" find the right textures. Map names in Japanese get their English meaning (kanban → signboard, ishidatami → paving stones…), and search ignores accents.
- **DDS import**: **Import PNG/DDS into mod…** also takes DDS files. A DDS in the texture's own format and size goes in exactly as it is - no recompression, so nothing is lost; any other DDS is decoded and compressed like a PNG. Works for textures in UI archives and for every layer of array textures.
- The toolkit can now open the player customisation files (`.ccdata`, `.ccdatam`) and the clothing group table, so they can be viewed, edited and merged like other data.

### Note
- New clothing items are tested in the program but not confirmed in-game yet.

## 1.4.0 - 2026-11-16

### New
- **Audio tab**: every song, jingle, sound effect, Pokémon cry, voice and ambience sound in the game (over 14,000), with:
  - **Names** you can search: the game's own sound event names (like `PLAY_BGM_VS_BOSS_01`), found for about 85% of sounds. Cries and Pokémon sounds also show the Pokémon, move sounds the move.
  - **Type**: Music, Jingle, Sound effect, Ambience, Pokémon cry, Voice or Vibration, plus a group (Moves, Menus, Cutscenes…).
  - **Play**, **Export WAV…**, and a mark on every sound your current mod changes.
- **Replace any sound or song** with WAV, MP3, OGG, FLAC, M4A and more:
  - **Volume** slider, or **Match the original's loudness** (never boosted into distortion).
  - **Loop points**: for songs, where the game loops them; for sounds, loop markers the game uses when it loops them.
  - **Preview** plays the result, with loop points the loop seam.
  - **Restore original** puts the game's sound back.

### Improved
- Replaced sounds go inside the sound bank, so a mod no longer needs a copy of the 0.9 GB `Stream.pck`. Songs get their new length, so they don't cut off or loop early. Every replacement is checked by playing the rebuilt bank back.
- Browse: **Replace in Audio tab…** on a sound opens it in the Audio tab.
- Textures: **Show its model in 3D** opens the texture's model in the preview pane, like other 3D views (or in its own window if **Preferences… → 3D view** says so). **Its model in viewer window** always opens a separate window.

### Fixed
- **Renaming a mod** now renames its folder too, and the current mod and load order follow it. Before, only the name inside the mod changed and the program could switch to another mod.
- **Texture preview shows your mod's version**: when the current mod has its own copy of a texture, the preview shows it (marked "your mod … version"); **Show game original** shows the game's. After every save from the image editor (or **Import PNG into mod…**) the preview updates by itself, so you can see the edit went into the mod.

### Note
- Replacing songs is confirmed working in-game. Sound effects, cries and loop points are tested in the program but not confirmed in-game yet - try them before sharing a mod.

## 1.3.2 - 2026-11-15

### Fixed
- **Imported models crashed the game.** When the toolkit rebuilt a game file, it dropped every stored value of 0. For some values the game's default is -1, so an imported model's mesh pointed at a vertex layout that doesn't exist. Every rebuilt file now keeps all of its original values (checked on every file type).
- **Repair for older mods**: mod files saved by 1.3.1 or older are found when the program starts, and it offers to repair them once. Only the missing values are restored; the old versions stay in History. Skeletons, materials, clothing animation data and the Pokémon resource table could be affected too.

### Improved
- **Model import**:
  - Tangents are computed from the UVs, matching the game's own. Blender doesn't export them, so shading was wrong before.
  - Each vertex keeps its strongest bone influences, stored the way the game stores them.
  - A warning when a part ends up on the other side of the body from the bones it follows (for example a mirrored wing), because it will swing around in-game.
  - Warnings for bones the game skeleton doesn't have and for materials left with no triangles.
  - A glTF file from a different model is refused instead of written.

### New
- **Report a bug…** (bottom right, also offered when an error pops up): collects your version, Windows version and recent errors, with your user name and folders replaced by placeholders. Copy them and paste them into the bug report form on GitHub; nothing is sent automatically.
- **Patch notes** (bottom right): these notes, in a What's new window. It also opens by itself once after each update.

## 1.3.1 - 2026-11-15

### Fixed
- The window sometimes started at its smallest size (900×520) instead of full size.

## 1.3.0 - 2026-11-15

### New
- **3D view in the main window**: click a model in the file list and it shows in 3D in the preview pane, with orbit, animations, variants and clothing colours. *Try on player body*, *Play in 3D* and the colour-variant buttons open there too. **Pop out (own window)** still gives you the separate viewer.
- **Built-in image editor**: live texture edits open in **miniPaint**, bundled and working offline. **Ctrl+S** or **Save to mod** saves straight into your mod, and the 3D view refreshes.
- **Photopea** as an option: opens photopea.com in a toolkit window with your texture loaded. It needs an internet connection and its free version shows ads.
- **Preferences…** (bottom right): theme, interface size, update check, image editor, 3D view (click-to-3D, pane or window, texture quality) and how many history versions each mod file keeps.
- **Show filter**: *Models (.trmdl)*, *Models + textures* and *Animations (.tranm)*.

### Fixed
- **Live edit** opened the PNG in the Windows photo viewer instead of an editor.

## 1.2.0 - 2026-11-14

### New
- **Custom clothing colours**: in **Edit clothing colours…**, **Custom colour…** opens a colour wheel with a hex code box. The palette gets private cells, so other clothing keeps its exact colours.
- **Edit pattern textures…**: every texture of a clothing pattern with a preview and what it does, which ones other items share, and live editing in your image editor.
- **Mod conflict check**: **Check conflicts** on the Mods tab lists every game file that more than one mod changes: your mods, the load order and the mods installed in ZA Recomp / Lumiose Online, with which version the game uses.
- **Install to Recomp** warns before installing over files another installed mod changes.

## 1.1.0 - 2026-11-14

### New
- **Built-in updater**: the toolkit checks GitHub for a new version when it starts. **★ Update to x.y.z** (bottom right) downloads it, checks it and installs it, keeping your mods and settings.
- **Clothing colour variants**: every colour variant the game sells is listed under each clothing item in Search, with its in-game colour names, item id and price. **Try on player body** and **Open 3D viewer** show exactly that variant.
- **Clothing colour editor**: change which palette colour each in-game colour option uses, saved into your mod, with a live 3D preview. *Confirmed working in-game.*
- **Game data → Clothing items**: **Try on**, **View in 3D** and **Edit colours…** buttons.

### Improved
- Scrollbars on the Pokémon data and Game data lists, and the Game data tab follows the dark theme.

## 1.0.0 - 2026-11-14

The first Windows release.
- **Import game files**: unpacks your own game dump, with stop and resume.
- **Browse & extract**: search by in-game name, file name or game text; preview textures, models, text, data files and sounds; export to PNG/DDS, glTF, JSON, WAV and text.
- **3D viewer**: models with variants (Normal / Shiny…), animations with "Keep in place", and **Try on player body** for player clothing with all player animations.
- **Edit and mod**: import PNGs into textures (also inside UI archives), edit text, material colours with a live 3D preview, game data tables (moves, items, shops, clothing, trainers, item balls), Pokémon data (types, abilities, stats, moves, evolutions) and map spawns and item balls.
- **Mods tab**: mod projects, history / compare / restore for every file, packages for ZA Recomp, Switch (Atmosphere) and emulators, and **Install to Recomp**.
- *Experimental*: import an edited glTF model, and replace sounds with a WAV.
