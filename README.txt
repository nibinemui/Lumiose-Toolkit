Lumiose Toolkit
===============

Double-click "Lumiose Toolkit.exe" (release download) or "Lumiose Toolkit.bat" (running from source).
The first start opens "Import game files" to unpack your game dump. The window has six tabs:
Browse & extract, Mods, Map, Pokémon data, Game data and Audio. "Dark mode" (top right) switches the theme.
README.md is the full manual (features, installing, troubleshooting).

WHAT'S NEW IN 2.0.0
-------------------
  Features marked EXPERIMENTAL show a warning in their window: some things may not work properly
  or look incorrect. Test what they make with a copy of your save.
  * EXPERIMENTAL - Port a Pokemon (Pokemon data tab): bring a Pokemon from your Scarlet/Violet dump
    into Z-A as a new Pokemon in a free species slot - model, animations, materials for Z-A, species
    data, moves, alpha move, cry (or your own sound), wild behaviour, optional Pokedex entry and
    wild-zone spawns. Then icons in Z-A's style: pick a pose, fine-tune it in the icon editor.
  * EXPERIMENTAL - Map tab: "Create Spawn Point..." - click an empty spot and add a Pokemon spawner
    (species, form, levels, size, always an alpha; always / by day / at night), an item ball or a move
    gimmick (Mega Shard crystals, Hyperspace boxes, rocks, vines, sludge). Megas get wild behaviour.
  * EXPERIMENTAL - Map tab: "Hyperspace Pokemon..." - which Pokemon Hyperspace brings for each zone
    type and rank: change them, their levels and alpha chance, add or remove them.
  * Map tab: "Show Pokemon icons" on spawn points; points your mod added are drawn black.
    "Add spawn point..." still copies a selected Pokemon spawn point.
  * EXPERIMENTAL - Audio tab: "Add as new battle music..." - your song as a new entry in the
    "Battle BGM" list of online Private Battles, and if you like the music of every trainer battle.
  * EXPERIMENTAL - Mods tab: "Play as a character..." - the player becomes one of the game's
    characters (18 named ones, 93 trainer and townsfolk outfits) - or Delibird.
  * Search all text... (Browse & extract): every line of game text in one language or all of them,
    double-click to edit the line.
  * Mods tab: "Merge mods..." - several of your mods combined into one new mod. Shift / Ctrl + click
    selects several mods to delete at once.
  * Many fixes: tabs no longer save an old copy of a table into another mod, closing asks about
    unsaved changes, safer installs, imports, updates and history, sound and text fixes.

WHAT'S NEW IN 1.6.2
-------------------
  * Search all tables... (Game data tab): search every data table at once by name or number
    ("Pikachu", "Tackle", "a0403_w01") and double-click a result to open that row.
  * Mods tab: scrollbars on My mods and the mod's file list, and a Refresh button (the Current mod
    list also picks up mods added outside the program).

WHAT'S NEW IN 1.6.1
-------------------
  * Fixed: numbers 90-99 (Preferences > Mods) broke new items and hairstyles; they now work.
  * Fixed: smaller issues with new hairstyles, the Test checklist window and "Add original to mod".

WHAT'S NEW IN 1.6.0
-------------------
  * New hairstyles: Game data > "Hairstyles, hair colours & make-up" > "New hairstyle from this..." makes
    a new hairstyle (own model and hair animations, id, name, salons) without replacing anything.
  * Test checklist... (Mods tab): what to check in game, made from what the mod changes. Tick, copy, save.
  * Check new items (Mods tab): checks a mod's new clothing items and hairstyles before you share it.
  * Your own id series (Preferences > Mods): each person picks a number so new items never clash.
  * New clothing items now work in Search, Try on, Show in 3D and Edit colours.

WHAT'S NEW IN 1.5.1
-------------------
  * Fixed: new clothing items made in different mods could get the same item ids and clash when the
    mods were installed together. New items now skip every id your other mods already use.

WHAT'S NEW IN 1.5.0
-------------------
  * DDS import: "Import PNG/DDS into mod..." - a DDS in the texture's own format goes in exactly as it is
    (no recompression); other DDS files are decoded and compressed like a PNG.
  * Mod merging: mods that change the same game data table or text are merged row by row when you build
    or install them together (instead of one overwriting the other).
  * Textures say what they are (About column, preview header) and search matches it: "pikachu body",
    "billboard", "poster", "marble", "poke ball icon"... Japanese map names get their English meaning.
  * New clothing items: Game data > Clothing items > "New item from this..." makes a new buyable item
    (own model copy, ids, name, price, shop) without replacing anything. Back up your save first.

WHAT'S NEW IN 1.4.0
-------------------
  * Audio tab: every song, jingle, sound effect, Pokémon cry, voice and ambience (14,000+), searchable
    by the game's own sound names (with Pokémon and move names), with a Type column. Play, export.
  * Replace any of them with WAV, MP3, OGG, FLAC, M4A...: volume, match the original's loudness,
    loop points (songs: where the game loops them), preview, restore original. Stored inside the
    sound bank, so mods don't need a copy of Stream.pck. Song replacement is confirmed in-game.
  * Fixed: renaming a mod renames its folder too. The texture preview shows your mod's version
    ("Show game original" to compare) and updates after every save from the image editor.
  * Texture live edit: "Show its model in 3D" opens the model in the preview pane (or a window, see
    Preferences > 3D view); "Its model in viewer window" always opens its own window.

WHAT'S NEW IN 1.3.2
-------------------
  * Fixed: imported models crashed the game. Rebuilt files lost values the game needs; every
    rebuilt file now keeps all of its original values. Mod files saved by older versions are
    found at startup and can be repaired with one click (old versions stay in History).
  * Model import: tangents are computed from the UVs (Blender's were wrong or missing), each vertex
    keeps its strongest bone influences, and a glTF file from a different model is refused. It warns
    when a part ends up on the other side of the body from the bones it follows (e.g. a mirrored wing).
  * "Patch notes" button (bottom right): what changed in every version. The What's new window
    also opens by itself once after each update. All versions are in CHANGELOG.md.
  * "Report a bug..." button (bottom right): collects version, system and recent errors, with your
    user name and folders hidden, and opens the bug report form on GitHub.

WHAT'S NEW IN 1.3.1
-------------------
  * Fixed: the window sometimes started at its smallest size (900x520) instead of full size.

WHAT'S NEW IN 1.3.0
-------------------
  * Models show in 3D right in the preview pane when you click them ("Pop out" = own window).
  * Show filter: Models (.trmdl), Models + textures, Animations (.tranm).
  * Built-in image editor for live edits: miniPaint (offline), or Photopea (online) in a window.
    Choose it, Paint, or any other program in Preferences… (bottom right). Save = into your mod.
  * Preferences…: theme, interface size, update check, image editor, 3D view (click-to-3D, pane or
    window, texture quality) and how many history versions each mod file keeps.

WHAT'S NEW IN 1.2.0
-------------------
  * Custom clothing colours: "Custom colour…" in Edit clothing colours opens a colour wheel with a
    hex code box. The palette gets private cells, so other clothing keeps its exact colours.
    New - check in-game before sharing.
  * "Edit pattern textures…" (clothing models, colour variants, Clothing items table): every
    texture of a pattern with a preview and what it does; live edit in your image editor.
  * Mods tab > "Check conflicts": files changed by more than one mod - your mods, the load order
    and the mods installed in ZA Recomp / Lumiose Online - with which version the game uses.
    "Install to Recomp" warns before installing over another mod's files.

WHAT'S NEW IN 1.1.0
-------------------
  * Updates: the program checks GitHub for a new version when it starts. "★ Update to x.y.z"
    (bottom right) downloads and installs it, keeping your mods and settings. "Updates…" checks by hand.
  * Clothing colour variants: every colour the game sells is listed under each clothing item in
    Search (in-game colour names, id, price); Try on / Open 3D viewer show exactly that variant.
  * "Edit clothing colours…": change which palette colour each in-game colour option uses, saved
    into your mod, with a live 3D preview. Confirmed working in-game (Lumiose Online).
  * Game data > Clothing items: Try on, View in 3D and Edit colours… buttons.
  * Scrollbars on the Pokémon data and Game data lists.

WHAT'S NEW (2026-11-14, second round)
-------------------------------------
  * Game data tab: table editor for Moves, Items (prices), Item shops, Clothing items (prices),
    Clothing shops, Hair & make-up, Trainers (teams) and Item balls - or any data file with a
    schema ("Open other data file…", or "Open in table editor" on a data file in Browse).
  * Textures: "Live edit in image editor" - every save of the PNG goes into the mod by itself;
    "Show its model in 3D" shows the change live (in the preview pane, or its own window).
  * Text: "Edit text…" opens an in-app editor (search, edit, Ctrl+Enter, Save to current mod).
  * Mods tab: "History / compare / restore…" - every version of every mod file is kept; compare
    with the game original / previous version (text lines, data fields, textures side by side).
  * 3D viewer / Try on player body: clothing patterns, main colour, second colour and skin tone
    with the in-game colour names (palette colours are now used for all clothing and hair).
  * Map: item balls / random items show their contents (hover + "Contents" box) and can be edited
    ("Make unique" first if the item table is shared). "Area" picks interiors, dungeons and zones;
    a dungeon floor map can be put underneath and aligned by hand ("Align", Save alignment).

WHAT'S NEW (2026-11-14)
-----------------------
  * Mods tab: "Install to Recomp" copies the mod straight into <your ZA Recomp folder>\mods\<name>
    (order / on-off stay on Recomp's Mods page); "▶ ZA Recomp" / "▶ Lumiose Online" start the game.
  * Edit colours… (models and .trmtr files): colour pickers for every material colour + numbers;
    "Open live 3D preview" shows the current mod and refreshes by itself every time you save.
  * 3D viewer: Variant menu (Normal / Shiny / other material sets), "Keep in place" for running
    animations, and it always shows your current mod's versions of files.
  * Try on player body (player clothing models): the item on the player with face, hair, other
    clothes and all 301 player animations.
  * Search "in: Game text" (+ language): finds any line of game text and jumps to it.
  * Map tab: "Encounters at this spawn" - change the Pokémon, levels and weight of a spawn point,
    "Add (copy)" a new encounter for just that point, Remove; saved with "Save changes to current mod".
  * Pokémon data tab (avalon/data/personal_array.bin): types, abilities, base stats, EV yield,
    catch rate, growth, gender, egg groups, size, level-up moves, evolutions, TM and egg moves.
  * Textures: importing a PNG of a different size can rebuild the texture at the new size;
    textures inside UI archives (.arc) can be imported too (the archive is rebuilt).
  * EXPERIMENTAL - Import edited glTF into mod (models): replaces the model's meshes with the meshes
    of a .glb (keep mesh names, armature and bone names; materials matched by name).
  * EXPERIMENTAL - Replace with WAV (audio): puts a WAV into a .bnk or .pck as 16-bit PCM.
    These two can only be confirmed in-game - check them before sharing.


BROWSE & EXTRACT
----------------
Search by in-game name (Pokémon "pikachu" / "25", clothing "v-neck t-shirt") or by file name.
Click a file to preview it; the buttons above the preview depend on the file type:

  Textures (.bntx)    view channels/mips/layers, zoom, pixel values · Import PNG into mod
  Models (.trmdl)     materials/meshes/bones summary · Open 3D viewer · Export glTF (.glb)
  Animations (.tranm) Play in 3D viewer (on its model) · Export with model (.glb)
  Data (.bin with a .bfbs schema, and .trmtr/.trskl/.trmsh/.trmdl/.trscn/.trins/...)
                      shown as JSON · Export JSON · Import edited JSON into mod
  Text (.dat)         shown as label<TAB>text · Export text · Import edited text into mod
  Audio (.bnk/.pck)   list of sounds · Play/Stop · Export selected/all as .wav
  UI archives (.arc)  expand like folders; textures inside preview too
  Everything else     readable strings + hex; copied as-is
Right-click files: Extract, Add original file(s) to current mod, Show in Explorer, Copy path.

Extract selected (Ctrl+E) converts as ticked at the bottom: textures → PNG (+DDS),
models → glTF (optionally with all their animations), data → JSON, audio → WAV.
"Folders include" decides what is taken from selected folders.

3D viewer: separate window (orbit with the mouse), animation list with filter, play/pause,
speed, timeline, wireframe, bones, textures on/off. Colours use the game's layer-colour
system (baked into the base texture), UV scaling/mirroring and wrap modes are applied.
Limits: eye pupils/iris effects and special shaders (hologram, fur, emission) are simplified;
clothing items are shown in their rest pose (they bind to the player body skeleton).


MODS
----
"Current mod" (top bar) is where every "...into mod" action writes. Make one with "New mod...".
A mod is a folder: Lumiose Toolkit\mods\<name>\romfs\<game paths> (+ mod.json).

  Textures: export a PNG, edit it, "Import PNG into mod". It is resized to the original size
            if needed and re-compressed to the original format (BC7/BC1/BC3/BC4/BC5/RGBA) with
            all mip levels; the status bar shows the compression error.
  Text:     "Export text", edit only the text after the TAB (keep every line, in order;
            \n = new line, [VAR ...] = game variables), "Import edited text into mod".
  Data:     "Export JSON", edit, "Import edited JSON into mod" (rebuilt with flatc).
  Any file: "Add original to mod" copies it so you can replace it by hand.

Mods tab: edit mod details, see/remove its files, and build packages:
  Load order: add your mods and/or downloaded mods (.zip or folder with a romfs folder);
  the top one wins when two change the same file ("Check conflicts" lists them).
  Package for:
    ZA Recomp / Lumiose Online  -> <name>/romfs/...      install from Recomp's Mods page
    Switch (Atmosphere)         -> atmosphere/contents/0100F43008C44000/romfs/... + patched data.trpfd
    Emulator (Ryujinx-style)    -> 0100F43008C44000/<name>/romfs/... + patched data.trpfd
  Switch/emulator packages redirect the modded files in data.trpfd (same method as
  TrinityModLoader). That index is made for exactly the mods in the package, so build ONE
  combined package containing every mod you use. "Game romfs" must point at an unmodified
  dump (for example D:\Dumps\Legends Z-A\romfs).


MAP
---
The Lumiose City map with every placement table on top: Pokémon spawns (with the species and
levels that appear there), item balls, random items, NPCs, walking NPCs, royale cards, move
gimmicks. Mouse wheel = zoom, drag = pan, click = select. "Find" searches point names.
Edit: change X/Y/Z/rotation and Apply, or tick "Edit mode" and drag the selected point;
Duplicate / Delete; "Save changes to current mod" writes the edited tables into the mod.
Area: Lumiose City, or any interior / dungeon / zone (they reuse coordinates near the world origin
and are told apart by the area code in point names). Their points are drawn on a 10 m grid, or on
a dungeon floor map chosen under Background and aligned once (saved in map_calibration.json).
A duplicated point only spawns something if the spawner data tables have an entry with that name.
New points: "Add spawn point..." copies the selected Pokémon spawn point; "Create Spawn Point..."
(experimental) adds a Pokémon spawner, item ball or move gimmick at an empty spot you click.
"Hyperspace Pokémon..." (experimental) edits which Pokémon Hyperspace brings. "Encounter overview..."
(experimental) lists every wild Pokémon - where, levels, weight, chance, alpha, day or night - and
edits levels, weight and alpha chance in the list. "Show Pokémon icons"
puts each spawn point's Pokémon on the map; points your current mod added are drawn black.


RESTORING FILES THE ORIGINAL EXPORT SKIPPED
-------------------------------------------
restore_missing.py reads the game's romfs\arc\data.trpfs directly and writes any missing
file (never overwrites). Needs an Oodle DLL (oo2core_8_win64.dll, e.g. from pkNX).
The files it wrote are listed in restored_files.txt.
    python restore_missing.py "D:\Dumps\Legends Z-A\romfs\arc" "D:\ZA Game Files" ^
        "D:\Tools\pkNX\oo2core_8_win64.dll" ^
        names\za_hashes_inside_fd.txt          (add --dry-run to only count)
Audio (romfs\audio) and system\config were copied in as well.


NOTES
-----
  * Pokémon model folders come from the game's catalog (ik_pokemon\catalog\...); folder numbers
    are not always the Pokédex number (Chespin #650 = pm0720, Tatsugiri #978 = pm1056).
  * The 25 hash-named .bin files are files nobody has named yet; the tool labels what they are.
  * Errors: _extract_errors.txt in the output folder; program errors in error_log.txt.

CREDITS / LICENCES
------------------
  * Trinity format knowledge (models, materials, skeletons, animations, mod index patching) is
    ported from pkZukan's GFTool (GPL-2.0, https://github.com/pkZukan/gftool); its source is in
    tools\gftool_src and gen_trinity_fbs.py turns its definitions into schemas.
  * Hash lists: pkZukan/PokeDocs. Gen 9 species IDs: kwsch/PKHeX.
  * tools\: flatc (Google, Apache-2.0), texconv (Microsoft DirectXTex, MIT),
    vgmstream (ISC-style), three.js (MIT). pywebview (BSD).
