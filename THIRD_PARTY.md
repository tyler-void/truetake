# Third-party software

truetake bundles no third-party code, fonts or model weights. It calls external programs and uses Python
packages that you install yourself. Each license below was read from the project's own LICENSE file or its
package metadata, not recalled.

## External programs

| Program | Used for | License | Where the license was read |
|---|---|---|---|
| [agg](https://github.com/asciinema/agg) | asciicast → GIF | GPL-3.0 | `LICENSE` in asciinema/agg |
| [FFmpeg](https://ffmpeg.org) (`ffmpeg`, `ffprobe`) | video and audio | LGPL-2.1-or-later; GPL-2.0-or-later when built with `--enable-gpl` | `LICENSE.md` in FFmpeg. Many distribution builds enable GPL parts: `ffmpeg -L` prints your build's license |
| [Poppler](https://poppler.freedesktop.org) (`pdftoppm`, `pdftotext`), optional | slides | GPL-2.0 and GPL-3.0 (`COPYING`, `COPYING3`) | Poppler source; distribution metadata also lists LGPL, MIT and HPND parts |
| [fontconfig](https://www.freedesktop.org/wiki/Software/fontconfig/) (`fc-match`, `fc-list`) | finding fonts | HPND AND Unicode-DFS-2016 | distribution package metadata |
| [Git](https://git-scm.com) | card excerpts (`git show`), sandbox resets | GPL-2.0-only | distribution package metadata |
| [Claude Code](https://github.com/anthropics/claude-code) | the agent that is recorded | Proprietary: "© Anthropic PBC. All rights reserved. Use is subject to Anthropic's Commercial Terms of Service." | `LICENSE.md` in anthropics/claude-code |

## Python packages

| Package | Used for | License | Where the license was read |
|---|---|---|---|
| [PyYAML](https://pypi.org/project/PyYAML/) | `truetake.yaml` | MIT | package metadata |
| [Pillow](https://pypi.org/project/pillow/) | cards, captions, title cards, frame reading | MIT-CMU | package metadata |
| [NumPy](https://pypi.org/project/numpy/) | frame and calibration analysis | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | package metadata |
| [Pygments](https://pypi.org/project/Pygments/) | syntax highlighting on code cards | BSD-2-Clause | package metadata |
| [catppuccin](https://pypi.org/project/catppuccin/) (with the `pygments` extra) | the `catppuccin-mocha` Pygments style | MIT | `LICENSE` in the package (catppuccin/python) |
| [kokoro](https://pypi.org/project/kokoro/), optional | local TTS | Apache-2.0 | package metadata; `LICENSE` in hexgrad/kokoro |
| [soundfile](https://pypi.org/project/soundfile/), optional | audio I/O for Kokoro | BSD-3-Clause | package metadata |

Installed along with `kokoro` (not used by truetake directly):
- misaki, Apache-2.0 (package metadata);
- espeakng-loader, MIT (`LICENSE` in thewh1teagle/espeakng-loader). It ships a build of
  [eSpeak NG](https://github.com/espeak-ng/espeak-ng), which is GPL-3.0 (`COPYING`);
- spaCy's `en_core_web_sm` model, MIT (package metadata);
- PyTorch, Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND
  MIT (package metadata of the installed wheel).

## Model weights and fonts (not bundled)

| Asset | License | Where the license was read | How to get it |
|---|---|---|---|
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) weights and voices | Apache-2.0 | model card front matter (`license: apache-2.0`) | downloaded by `kokoro` on first use into the Hugging Face cache |
| [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) | OFL-1.1 | the font's own name table ("licensed under the SIL Open Font License, Version 1.1") and `OFL.txt` in JetBrains/JetBrainsMono | your package manager, or the release on GitHub; any monospace font works via `terminal.font_family` |

## Colours

The terminal palette, card and caption colours are the [Catppuccin](https://github.com/catppuccin) Mocha
palette, MIT (`LICENSE` in catppuccin/python).
