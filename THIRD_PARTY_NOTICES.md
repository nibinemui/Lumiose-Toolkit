# Licences and third-party notices

## Lumiose Toolkit itself

Lumiose Toolkit is free software, licensed under the **GNU General Public License, version 2**
(full text: [`LICENSE`](LICENSE)).

Copyright (C) 2026 nibinemui.

This program is free software; you can redistribute it and/or modify it under the terms of the
GNU General Public License version 2 as published by the Free Software Foundation.
This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
General Public License for more details.

**Why GPL-2.0:** the Trinity code in Lumiose Toolkit (models, materials, skeletons, animations,
mod-index patching, in `za_trinity.py`, `za_modding.py` and `gen_trinity_fbs.py`) is ported from
GFTool, which is GPL-2.0. A program containing GPL-2.0 code must be distributed as a whole under
GPL-2.0, with its source. Because Lumiose Toolkit is written in Python, the source is the program.

## Bundled third-party components

These ship inside `tools/`, unmodified unless noted. The executables are **separate programs**
that Lumiose Toolkit starts as child processes; they are not linked into it. Shipping them next to
a GPL program is "mere aggregation" (GPL-2.0 section 2), so each keeps its own licence below.

| Component | Where | Licence | Licence text | Source |
|---|---|---|---|---|
| **GFTool** source (pkZukan and contributors) | `tools/gftool_src/` (reference); ported code throughout | GPL-2.0 | [`LICENSE`](LICENSE) | https://github.com/pkZukan/gftool |
| **FlatBuffers** `flatc` compiler (Google) | `tools/flatc/flatc.exe` | Apache-2.0 | [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt) | https://github.com/google/flatbuffers |
| **DirectXTex** `texconv` (Microsoft) | `tools/texconv/texconv.exe` | MIT | [`licenses/MIT-DirectXTex.txt`](licenses/MIT-DirectXTex.txt) | https://github.com/microsoft/DirectXTex |
| **vgmstream** `vgmstream-cli` (Adam Gashlin, bnnm and contributors) | `tools/vgmstream/` | ISC-style | [`licenses/vgmstream-ISC.txt`](licenses/vgmstream-ISC.txt) (also `tools/vgmstream/COPYING`) | https://github.com/vgmstream/vgmstream |
| **FFmpeg** libraries (`avcodec`, `avformat`, `avutil`, `swresample`), part of the vgmstream build | `tools/vgmstream/*.dll` | LGPL-2.1-or-later | [`licenses/LGPL-2.1.txt`](licenses/LGPL-2.1.txt) | https://ffmpeg.org/download.html |
| **mpg123** (`libmpg123-0.dll`), part of the vgmstream build | `tools/vgmstream/` | LGPL-2.1 | [`licenses/LGPL-2.1.txt`](licenses/LGPL-2.1.txt) | https://www.mpg123.de/download.shtml |
| **libvorbis**, **Speex**, **CELT** (Xiph.Org and contributors), part of the vgmstream build | `tools/vgmstream/*.dll` | BSD-3-Clause | [`licenses/BSD-Xiph.txt`](licenses/BSD-Xiph.txt) | https://xiph.org |
| **three.js** r160 (three.js authors) | `tools/three/` | MIT | [`licenses/MIT-three.js.txt`](licenses/MIT-three.js.txt) | https://github.com/mrdoob/three.js |

**LGPL libraries (FFmpeg, mpg123):** these DLLs are redistributed unmodified, exactly as shipped
in the official vgmstream Windows release (`tools/vgmstream/vgmstream-win64.zip`). Their
complete source code is available from the links above; the build scripts that produced them
are in the vgmstream repository. You may replace these DLLs with your own builds of the same
libraries. On request we will provide a copy of the corresponding source code for at least three
years from the date of each release.

## Python and Python packages

The **release .exe bundles** the Python runtime (PSF licence), Tcl/Tk (BSD-style) and the packages below,
plus their own dependencies (in the `_internal` folder). The build copies every one of their licence files to
`licenses/bundled-python/` in the release, with a list in `licenses/bundled-python/README.txt`.
The .exe itself is made with PyInstaller, whose bootloader licence (GPL with an exception) allows this.
When running from source, `requirements.txt` installs these from PyPI instead.

| Package | Licence | Source |
|---|---|---|
| NumPy | BSD-3-Clause | https://numpy.org |
| Pillow | MIT-CMU (HPND) | https://python-pillow.org |
| pywebview | BSD-3-Clause | https://github.com/r0x0r/pywebview |

## Data and knowledge from other projects

| Project | What was used | Licence status |
|---|---|---|
| **pkZukan/PokeDocs** (https://github.com/pkZukan/PokeDocs) | file-hash list (`names/za_hashes_inside_fd.txt`) and data schemas (`tools/schemas/pokedocs/`) | PokeDocs publishes no licence, so **these files are not distributed with Lumiose Toolkit**. `za_deps.py` downloads them from the PokeDocs repository on first start; `za_personal.fbs` is built locally from PokeDocs' `personal.fbs` with one fix (evolutions as a list). |
| **kwsch/PKHeX** (https://github.com/kwsch/PKHeX, GPL-3.0) | the Gen 9 internal species ID to National Dex number table, as factual reference data | No PKHeX code is included. |

Reverse-engineered formats written for this project (`tools/schemas/za/*.fbs`: moves, items, shops,
clothing shops) are part of Lumiose Toolkit and covered by its GPL-2.0 licence.

## Game content

Pokémon, Pokémon Legends: Z-A and all related names, characters, models, textures, text and audio
are © Nintendo / Creatures Inc. / GAME FREAK inc. Lumiose Toolkit is an unofficial fan tool and is
not affiliated with or endorsed by them. No game files are included; users work with their own
legally dumped copy. The fallback name lists in `names/` and the program icon (a render of the
game's Luxury Ball model) are used for identification only.
