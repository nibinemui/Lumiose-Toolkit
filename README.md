# Lumiose Toolkit

A Windows toolkit for exploring and modding **Pokémon Legends: Z-A** game files.
Browse and preview everything in your own game dump, export it to standard formats
(PNG, glTF, JSON, WAV, text), edit textures, models, text, Pokémon, moves, items, shops,
trainers and map placements, then package your changes as a mod for ZA Recomp (and builds based
on it), a modded Switch (Atmosphere) or an emulator.

> **No game files are included.** You need your own legally dumped copy of the game.
> This project is not affiliated with Nintendo, The Pokémon Company, Game Freak or Creatures.

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation (from a fresh download)](#installation-from-a-fresh-download)
- [Preparing the game files](#preparing-the-game-files)
- [First start](#first-start)
- [Using the program](#using-the-program)
- [Making and sharing mods](#making-and-sharing-mods)
- [Troubleshooting](#troubleshooting)
- [Known limits](#known-limits)
- [Project layout](#project-layout)
- [Credits and licences](#credits-and-licences)

---

## Features

### Browse & extract
- **Search by in-game name**: Pokémon (`pikachu`, `25`), clothing (`v-neck t-shirt`), or any file/folder name. Small typos are fine.
- **Search the game's text**: every line of dialogue, item and move names in any language; click a result to jump to that line.
- **Preview every file type**:
  - **Textures** (`.bntx`): every BC1–BC7/BC6H format, with channels, mip levels, array layers, zoom and pixel values.
  - **Models** (`.trmdl`): materials, meshes and bones, plus an interactive **3D viewer**.
  - **Animations** (`.tranm`): play them on their model in the 3D viewer.
  - **Data**: FlatBuffer `.bin` files shown as readable JSON. This covers every Trinity format (`.trmtr`, `.trskl`, `.trmsh`, `.trscn`, …) and any `.bin` with a schema.
  - **Text** (`.dat`/`.tbl`): decoded message files.
  - **Audio** (Wwise `.bnk`/`.pck`): list and play sounds.
  - **UI archives** (`.arc`): open like folders; the textures inside preview too.
- **Batch extraction** (Ctrl+E): textures → PNG (+DDS), models → glTF/GLB (optionally with all animations), data → JSON, audio → WAV, and other files copied as-is.

### 3D viewer
- Orbit camera, wireframe, bones, textures on/off, lighting.
- All animations of a model, with filter, speed, timeline and **Keep in place** (removes root motion).
- Faithful colours: the game's layer-colour system, colour-table palettes, UV scale/offset and mirror/wrap modes.
- **Variants**: normal and shiny Pokémon, plus other material sets.
- **Try on player body**: shows a clothing item on the player with face, hair and other clothes and all 301 player animations.
- **Clothing patterns and colours**, just like the in-game boutique: pick the pattern, main colour, second colour and skin tone. Colours use their in-game names (e.g. "Scizor Orange").
- **Live reload**: the viewer refreshes by itself whenever your mod changes.

### Editing (everything is saved into the *current mod*, never into the game files)
- **Textures**:
  - Import a PNG. It is re-compressed to the original format with all mips, optionally at a new size. Textures inside `.arc` archives work too.
  - **Live editing**: opens the texture in your image editor; every save is imported automatically and shows in the 3D viewer.
- **Material colours**: colour pickers and numbers for every material parameter, with a live 3D preview.
- **Game text**: in-app editor with search, highlighted `[VAR …]` codes and multi-line text. Export/import as a text file also works.
- **Pokémon data** (`personal_array`): types, abilities, base stats, EV yield, catch rate, growth rate, gender, egg groups, size, level-up moves, evolutions, TM moves and egg moves.
- **Game data tab**: a table editor for:
  - **Moves**: power, accuracy, PP, type, category, priority, status effects, stat changes and every flag.
  - **Items**: price, max stack and TM move.
  - **Item shops**: what each shop sells and when it appears.
  - **Clothing items**: prices, colours and icons.
  - **Clothing shops** and **hair & make-up** salons.
  - **Trainers**: team species, level, moves, item, nature, ability, IVs, EVs, shiny and ball.
  - **Item balls**: contents and drop weights.
  - **Any other data file** with a schema ("Open other data file…" or "Open in table editor").
- **Map editor**:
  - Shows the Lumiose City map with every placement layer: Pokémon spawns, item balls, random items, NPCs, walking NPCs, royale cards and move gimmicks.
  - Move, rotate, duplicate and delete points.
  - Edit the **encounters** at a spawn: species, levels and weight.
  - Edit the **contents of item balls** and random items, with "Make unique" so other spots aren't affected.
  - Pick any **interior, dungeon or zone** from the Area list. Optionally lay a dungeon floor map underneath and align it; the alignment is saved per area.
- **Experimental**:
  - Import an edited glTF back into a model.
  - Replace a sound with a WAV.

### Mods
- Mod projects with name, author, version and description; "Add original to mod" copies any file into a mod for hand editing.
- **History**: every version of every changed file is kept. Compare any version with the game original or the previous version (text line by line, data field by field, textures side by side), and restore any version.
- **Load order** across your own mods and downloaded mods (`.zip` or folders), with conflict check.
- **Packaging** for:
  - **ZA Recomp / Lumiose Online**: one-click "Install to Recomp", or a package to install from the Mods page.
  - **Switch (Atmosphere)**: `atmosphere/contents/0100F43008C44000/romfs/…` with a patched `data.trpfd`.
  - **Emulators** (Ryujinx-style mod folders) with a patched `data.trpfd`.
- Launch ZA Recomp / Lumiose Online straight from the program to test a mod.

### Extras
- Dark mode.
- `restore_missing.py` extracts every file directly from the game's `data.trpfs` (see below).
- Unnamed hash files are identified by their contents.

---

## Requirements

| What | Version | Notes |
|---|---|---|
| Windows | 10 or 11 (64-bit) | The bundled tools are Windows builds. |
| [Python](https://www.python.org/downloads/windows/) | 3.10 – 3.12 (tested on 3.11) | Tick **"Add python.exe to PATH"** during setup. |
| Python packages | numpy, Pillow, pywebview | Installed from `requirements.txt` (see below). |
| [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) | any | Already present on Windows 11 and up-to-date Windows 10; needed for the 3D viewer window. |
| Oodle DLL (`oo2core_8_win64.dll`) | – | Only for extracting the game archive the first time (see below). Not included. |
| Disk space | ~25 GB | For the extracted game files. |

The `tools/` folder contains the helper programs the editor uses. Release downloads include them:

| Tool | Used for | Licence |
|---|---|---|
| `tools/flatc/flatc.exe` ([FlatBuffers](https://github.com/google/flatbuffers/releases)) | data ⇄ JSON | Apache-2.0 |
| `tools/texconv/texconv.exe` ([DirectXTex](https://github.com/microsoft/DirectXTex/releases)) | texture compression | MIT |
| `tools/vgmstream/vgmstream-cli.exe` + DLLs ([vgmstream](https://github.com/vgmstream/vgmstream/releases), win64 build) | audio decoding | ISC-style |
| `tools/three/` ([three.js](https://github.com/mrdoob/three.js) r160) | 3D viewer | MIT |
| `tools/schemas/` | data formats (generated, see credits) | – |
| `tools/gftool_src/` ([GFTool](https://github.com/pkZukan/gftool) source) | regenerating the Trinity schemas | GPL-2.0 |

---

## Installation (from a fresh download)

1. **Install Python** from python.org (3.11 recommended). On the first installer page tick
   **"Add python.exe to PATH"**, then click *Install Now*.

2. **Download Lumiose Toolkit.** On the [GitHub page](https://github.com/nibinemui/Lumiose-Toolkit) click **Code → Download ZIP** and unzip it, or clone it:
   ```bash
   git clone https://github.com/nibinemui/Lumiose-Toolkit.git
   ```
   You should now have a folder (called `Lumiose Toolkit` below) containing `za_extractor.py`, `Lumiose Toolkit.bat` and `tools/`.

3. **Install the Python packages.** Open a Command Prompt in that folder (type `cmd` in the Explorer address bar and press Enter), then run:
   ```bash
   python -m pip install -r requirements.txt
   ```
   Then download the PokeDocs data files (file-name list and data schemas; they are fetched from
   [pkZukan/PokeDocs](https://github.com/pkZukan/PokeDocs) instead of being shipped, and the program also does this by
   itself on first start):
   ```bash
   python za_deps.py
   ```

4. **Check the tools folder.** If you downloaded a release zip, `tools/` is complete. If you cloned the source and the tools are missing, download them from the links in the table above and place them exactly like this:
   ```
   tools/flatc/flatc.exe
   tools/texconv/texconv.exe
   tools/vgmstream/vgmstream-cli.exe   (plus all the .dll files from the same zip)
   tools/three/three.module.js                       (three@0.160.0  build/three.module.js)
   tools/three/addons/controls/OrbitControls.js      (examples/jsm/controls/)
   tools/three/addons/loaders/GLTFLoader.js          (examples/jsm/loaders/)
   tools/three/addons/utils/BufferGeometryUtils.js   (examples/jsm/utils/)
   tools/viewer/viewer.html                          (part of this repository)
   ```

5. **WebView2**: on Windows 10, install the *Evergreen* WebView2 Runtime if the 3D viewer window stays blank.

---

## Preparing the game files

Lumiose Toolkit works on an **extracted** copy of the game's `romfs`.

1. **Dump the game** from your own Switch with a homebrew dumping tool. Dump the RomFS of the base game (title ID `0100F43008C44000`; apply the latest update if your tool supports it).
   You need the folder that contains `arc\data.trpfd` and `arc\data.trpfs`.

2. **Get the Oodle DLL** `oo2core_8_win64.dll`. It cannot be redistributed. Several Pokémon modding tools (for example pkNX) ship it alongside their program; copy it from there.

3. **Make an export folder**, for example `D:\ZA EXPORT`, and **put the `Lumiose Toolkit` folder inside it**:
   ```
   D:\ZA EXPORT\
       Lumiose Toolkit\      <- this program
   ```
   The program treats its parent folder as the game folder by default. You can choose another folder later with *Browse…* in the top bar.

4. **Extract the archive** with the included script. It writes every file from `data.trpfs` into the export folder, never overwrites anything, and can be run again safely:
   ```bash
   python restore_missing.py "D:\Dump\romfs\arc" "D:\ZA EXPORT" "C:\path\to\oo2core_8_win64.dll" names\za_hashes_inside_fd.txt
   ```
   Add `--dry-run` first to see how many files it would write. Files without a known name are saved as `<HASH>.bin`; the program labels what they are.

5. **Copy the loose folders** that are not inside the archive: copy `romfs\audio` (and `romfs\system`, if present) into the export folder, so you get `D:\ZA EXPORT\audio\…`.

---

## First start

- Double-click **`Lumiose Toolkit.bat`**. It starts the program without a console window.
  To make a desktop shortcut, right-click the .bat → *Send to* → *Desktop (create shortcut)*. You can set `lumiose_toolkit.ico` as its icon in the shortcut's Properties.
- The first start indexes the files and names, which takes a few seconds. The first *Game text* search builds a text index.
- In the top bar, check **Game folder** (the export folder) and **Output** (where extractions go; default `Lumiose Toolkit\extracted`).
- Click **New mod…** before editing anything. All edits go into the *current mod*.
- On the **Mods** tab, set **Game romfs** to your unmodified dump's `romfs` folder (needed for Switch/emulator packages). If you use ZA Recomp, also pick the **ZA Recomp / Lumiose Online folder** there (the folder with `lasrecomp.exe`) so "Install to Recomp" and the launch buttons work. Its drop-down lists every install found on your drives, so if you have a separate dev and player copy, choose the one you want the mod in.

---

## Using the program

### Browse & extract
Type in the search box at the top left to search. Use **in: Files & names** for names, or **in: Game text** plus a language to search the text.
Click a file to preview it. The buttons above the preview change with the file type. Right-click files for Extract, *Add original file(s) to current mod*, *Show in Explorer* and *Copy path*.
Select files or folders, tick the conversions at the bottom, then press **Extract selected** (Ctrl+E).

### Textures
- **Import PNG into mod…**: replaces the texture. If the PNG has a different size, you choose whether to rebuild the texture at that size.
- **Live edit in image editor**: exports the PNG and opens it in your default image editor. Every time you save, it is re-imported into the mod. Use **Open its model in 3D viewer** to watch the changes. Stop with **Stop live edits**.

### Models, clothing and the 3D viewer
- **Open 3D viewer**, **Export glTF (.glb)…**
- **Edit colours…** for materials.
- **Try on player body** for `p1_`/`p2_` clothing.
- In the viewer, *Variant* switches normal/shiny or the clothing **pattern**. The colour menus below it pick the clothing colours and skin tone.

### Text
**Edit text…** opens the in-app editor:
1. Filter lines with *Find*.
2. Edit the selected line. Line breaks are allowed; keep the blue `[VAR …]` codes.
3. Press Ctrl+Enter to apply.
4. Press **Save to current mod**.

*Only changed lines* shows your edits. *Revert line to original* undoes one.

### Pokémon data tab
Pick a Pokémon on the left, edit it, then press **Save changes to current mod**.
- Evolutions are one per line: `method level argument species form`.
- TM moves and egg moves can be move names or numbers.

### Game data tab
1. Pick a table, find a row and edit its fields.
   - Names are shown for moves, items, species, natures and clothing. Comboboxes filter as you type.
   - Nested lists (a shop's items, a trainer's conditions) have **Add copy / Add new / Remove / Up / Down**.
   - **Duplicate row**, **Delete row** and **Edit row as JSON…** work on whole rows.
2. Press **Save changes to current mod**.

Notes:
- The move, item and shop layouts were reverse-engineered for this tool. Fields still named `Unknown…` are not identified yet.
- The move, item, shop and clothing tables rebuild byte-for-byte identical when nothing is changed.

### Map tab
- **Navigate**: mouse wheel zooms, dragging pans, clicking selects a point. *Find* searches point names and switches to the right area automatically.
- **Edit a point**: change X/Y/Z/rotation and press **Apply**, or tick **Edit mode** and drag the selected point. **Duplicate** and **Delete** are below.
- **Pokémon spawns**: edit species, levels and weight under *Encounters at this spawn*. *Add (copy)* makes an encounter used only by this spawn.
- **Item balls and random items**: edit item, count and weight under *Contents*. Use **Make unique** first if the item table is shared with other points.
- **Interiors, dungeons and zones**: pick one from **Area**. Its points are shown on a 10 m grid. To use a floor map as the background:
  1. Choose it under **Background** (the lab and sewer floors are included).
  2. Tick **Align**.
  3. Drag and use the mouse wheel to move and scale the points onto the image. *Flip X / Flip Z / Rotate 90°* help if needed.
  4. Press **Save alignment**. It is stored in `map_calibration.json`, so you can share it.
- **Save changes to current mod** writes all edited tables.

---

## Making and sharing mods

A mod is just a folder: `Lumiose Toolkit\mods\<name>\romfs\<game paths>` plus `mod.json`.

On the **Mods** tab:
1. Select your mod and fill in its details. You can see and remove its files there.
2. Use **History / compare / restore…** (or double-click a file) to see every saved version, compare it, or roll it back.
3. Add the mods you want to the **load order**. The top one wins when two mods change the same file; **Check conflicts** lists those.
4. Pick the targets and press **Build packages**:
   - **ZA Recomp / Lumiose Online**: `<name>/romfs/…`. Or press **Install to Recomp** to copy it straight into Recomp's `mods` folder, then turn it on in the Mods page and start the game with **▶ ZA Recomp** or **▶ Lumiose Online**. Recomp builds the file index itself, so no patched `data.trpfd` is needed, and mods can be switched on and off individually.
   - **Switch (Atmosphere)**: copy the `atmosphere` folder to the root of the SD card.
   - **Emulator**: copy `0100F43008C44000/<name>` into the emulator's mod folder (Ryujinx: right-click the game → *Open Mods Directory*).

Switch and emulator packages include a patched `data.trpfd` that redirects the modded files (the same method as TrinityModLoader).
That index is made for exactly the mods in the package, so **build one combined package** with every mod you use.
"Game romfs" must point at an **unmodified** dump.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Double-clicking the .bat does nothing | Python isn't on PATH. Reinstall Python with "Add python.exe to PATH", or run `python za_extractor.py` in a Command Prompt to see the error. |
| `ModuleNotFoundError: numpy / PIL / webview` | Run `python -m pip install -r requirements.txt` again. |
| 3D viewer window is blank | Install the WebView2 Runtime. |
| "No schema known for …" | That data file has no known layout yet; it can still be extracted as raw bytes. |
| Data/texture import fails | Check that `tools/flatc/flatc.exe` and `tools/texconv/texconv.exe` exist. |
| No sound / audio list empty | `tools/vgmstream/vgmstream-cli.exe` and its DLLs must be present; copy `romfs\audio` into the export folder. |
| "names\za_hashes_inside_fd.txt not found" / Pokémon data tab won't load | Run `python za_deps.py` while online (downloads the PokeDocs files). |
| `restore_missing.py` fails | Point it at the `arc` folder (the one containing `data.trpfd`/`data.trpfs`) and at a 64-bit `oo2core_8_win64.dll`. |
| Something else | Program errors are written to `Lumiose Toolkit\error_log.txt`; extraction errors go to `_extract_errors.txt` in the output folder. |

---

## Known limits

- **Not yet confirmed in-game**: glTF model import and WAV sound replacement are marked experimental. Test them before sharing a mod.
- **Viewer approximations**: eye effects and special shaders (hologram, fur, emission) are simplified. Clothing is shown in its rest pose unless you use *Try on player body*.
- **Interior and dungeon areas**: these reuse coordinates near the world origin, so they are told apart by the area code in point names. Floor-map backgrounds must be aligned by hand once per area.
- **Duplicated map points**: a copy only spawns something if the spawner data tables have an entry with that name.
- **Field names**: some data fields keep generic names (`Unknown12`, `Flag_ZA60`…) until someone identifies them.

---

## Project layout

```
Lumiose Toolkit/
  Lumiose Toolkit.bat     start the program
  za_extractor.py      main window (Browse & extract, Mods tabs)
  za_formats.py        BNTX textures, message text, SARC archives, file detection
  za_trinity.py        Trinity models, materials, skeletons, animations, glTF export, clothing colours
  za_viewer.py         3D viewer (local web server + pywebview window)
  za_modding.py        JSON ⇄ data, text/texture import, mods, history, packaging, Recomp install
  za_dataedit.py       Game data tab (schema-driven table editor)
  za_pokedit.py        Pokémon data tab
  za_map.py            Map tab
  za_mated.py          material colour editor
  za_textedit.py       in-app text editor
  za_history.py        mod history / compare / restore window
  za_import.py         experimental glTF → model import
  za_audio.py          experimental WAV → Wwise import
  za_names.py          name index (Pokémon, clothing)
  restore_missing.py   extract files from data.trpfs
  za_deps.py           downloads the PokeDocs files (hash list, schemas) on first start
  gen_trinity_fbs.py   build Trinity schemas from GFTool's sources
  bfbs_dump.py         print a binary schema (.bfbs) as text
  names/               fallback name lists (+ the PokeDocs hash list, downloaded)
  LICENSE              GPL-2.0
  THIRD_PARTY_NOTICES.md, licenses/   licences of everything bundled
  tools/               helper programs and schemas (see Requirements)
  mods/                your mods (created on first use)
```

---

## Credits and licences

Lumiose Toolkit is licensed under the **GNU General Public License v2.0**; see [`LICENSE`](LICENSE).
Every bundled component, its licence and its source is listed in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md); full licence texts are in [`licenses/`](licenses/).

- **Trinity format knowledge** (models, materials, skeletons, animations, mod-index patching) is ported from **[pkZukan/gftool](https://github.com/pkZukan/gftool)** (GPL-2.0), which is why Lumiose Toolkit is GPL-2.0.
- **Hash list and Scarlet/Violet schemas**: [pkZukan/PokeDocs](https://github.com/pkZukan/PokeDocs).
- **Gen 9 species numbering** (reference data): [kwsch/PKHeX](https://github.com/kwsch/PKHeX).
- **Move / item / shop / clothing-colour layouts**: reverse-engineered for this project.
- **Bundled tools**: FlatBuffers `flatc` (Google, Apache-2.0), `texconv` (Microsoft DirectXTex, MIT), vgmstream (ISC; its FFmpeg/mpg123 DLLs are LGPL-2.1, its Xiph codec DLLs are BSD), three.js (MIT). **Python packages** (installed separately): NumPy (BSD), Pillow (MIT-CMU), pywebview (BSD).
- *Pokémon* and *Pokémon Legends: Z-A* are trademarks of Nintendo / Creatures Inc. / GAME FREAK inc. This is an unofficial fan tool for use with your own legally obtained copy of the game; no game files are included.
