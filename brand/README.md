# Kairos / Complete brand pack 1.0

The selected **09 / AI module** is the primary logo. This pack preserves its outlined letterforms and lavender capsule, combines them with Colin Eckert's **Things 2.2.4** surfaces, and uses **Neo Sans Pro** and **Operator Mono Lig Nerd Font**, as in the Kairos repository.

## Start here

- Open **index.html** for a visual asset catalog with light/dark viewing.
- Open **guide/kairos-brand-guide-light.pdf** or **guide/kairos-brand-guide-dark.pdf** for the complete 19-page identity and interface guide.
- Open **web-template/index.html** for the working UI starter. For the most reliable local preview, run `python -m http.server 8000` from this folder and visit `http://localhost:8000/web-template/`.

## What's included

| Folder | Contents |
| --- | --- |
| `logos/` | Primary #09 wordmark and optional horizontal icon/title lockup; on-dark, on-light, black and white monochrome; SVG and high-resolution transparent PNG. |
| `icons/` | KAI-only artwork, simplified small icons, app icons, maskable icons, SVG/PNG favicons, multi-size ICO and Apple touch icon. Light/dark and monochrome treatments. |
| `palette/` | 34-color master palette; six semantic palettes (violet/green/red × light/dark); SVG/PNG boards, CSS, GIMP `.gpl`, JSON and contrast measurements. |
| `boards/` | Full identity, typography, icon and accent boards in light/dark SVG and PNG, plus a six-interface screenshot overview. |
| `guide/` | Complete 19-page PDF guides, one light and one dark edition. |
| `web-template/` | Responsive HTML/CSS/JS dashboard, activity view, settings dialog, UI library and 18 desktop/mobile previews. |
| `source/` | Selected concept SVG master and complete brand-token configuration. |
| `verification/` | Browser checks and asset-validation report. |

## Modes and accent variants

The signature palette uses **Lilac #C1A0FF**, **Ink #161022** and **Cloud #E5EEF9**. In light interfaces, **Violet #6B43B5** provides accessible actions and links. The capsule itself remains lilac in every interface variant.

| Accent | Dark action | Light action |
| --- | --- | --- |
| Violet / signature | `#C1A0FF` | `#6B43B5` |
| Green / market | `#44CF6E` | `#187D3D` |
| Red / market | `#FF7378` | `#C83043` |

Choose **Settings → Color theme** and **Settings → Interface accent** in the template. The choices persist independently. Gains remain green, losses remain red, and both include signs or labels in all themes.

For integration:

```html
<html data-theme="dark" data-accent="green">
<link rel="stylesheet" href="palette/kairos-palette.css">
```

Use semantic tokens such as `--surface`, `--text`, `--accent`, `--success` and `--danger`. The main CSS includes every theme and accent; `kairos-accent-variants.css` is also supplied as a standalone override for an existing base palette. Set both attributes for deterministic overrides.

## Asset conventions

- **on-dark**: light outer lettering, for dark backgrounds.
- **on-light**: dark outer lettering, for light backgrounds.
- **mono-black / mono-white**: a single ink with transparent AI knockout. No fake background-colored knockout.
- **simplified**: optically simplified KAI, intended for 16-48 px icons. Prefer it wherever the full glyphs become too small.
- **maskable**: icon artwork within the central 80% safe circle.
- All standalone icons contain only **KAI**, on one horizontal line. No extra emblem is added.
- The primary logo is wordmark-only. The optional icon/title lockup places its KAI icon **only to the left**.
- Wordmark SVG viewBox: **409 × 104**. Standard KAI SVG viewBox: **199 × 104**. App-icon SVG: **1024 × 1024**.
- Wordmark PNGs: **818 px** and **1636 px** wide. Standard icon PNGs: **398 px** and **796 px** wide. App PNGs: **64, 128, 180, 192, 256, 512, 1024 px**.
- Main logo and standalone icon artwork is transparent. App icons have a full-bleed background, ready for platform masks. Boards are presentation surfaces.
- Lettering in SVG artwork is converted to paths. SVG assets contain no linked fonts, raster images or remote dependencies. Web preview PNGs are screenshots, not vector artwork.
- Dark editions are the default for unqualified board filenames; explicit `-dark` and `-light` filenames are preferred.

## Typography

**Neo Sans Pro** is the main family. Use Regular, Medium and Bold for UI; retain Ultra only in the supplied logo outlines. **Operator Mono Lig Nerd Font** is the monospace family; **OperatorMonoSSmLig Nerd Font** supplies bold monospace, matching the repository.

The template resolves your locally installed fonts by the same family/PostScript names used in the repo. Licensed primary font binaries are not redistributed in the ZIP. See **web-template/FONTS.md** to connect licensed webfonts for deployment. Included Noto Sans / Noto Sans Mono fallbacks keep the starter usable on machines without the primary fonts. Board lettering is outlined, and the PDFs embed/outline the intended typography.

## Use and scope

The web starter uses illustrative data and browser-only demonstration actions. It contains no credentials, API calls, trading execution or backend. Start/pause changes the demo state; settings demonstrate controls rather than recalculating fixed portfolio figures. The manifest provides icon/display metadata, not offline caching or an installability guarantee.

This is a brand handoff; no changes were made to the GitHub repository. Refer to the supplied guide for clear space, minimum sizes, color hierarchy, typography, responsive layout, voice, component behavior and implementation.

## Verification

- **81** contrast checks passed across base colors and alternate accents. Text combinations are checked against 4.5:1; control/focus combinations against 3:1.
- Chromium interaction checks cover the demo controls, chart periods, search, empty state, form validation, settings, theme/accents, keyboard tabs, dialog Escape/focus return and activity logging.
- Overview, activity and UI library layouts were checked for page overflow at **360, 390, 768, 1024 and 1480 px**.
- Both 19-page PDF editions were rendered and visually reviewed.
- These checks are not a full screen-reader or WCAG conformance audit.

## Sources

- Kairos remote `develop` snapshot: [`daf7bce4b58054f68b8c9b36e5144b7fc4cfcc61`](https://github.com/kyaulabs/kairos/tree/daf7bce4b58054f68b8c9b36e5144b7fc4cfcc61). README, stylesheet, index and favicon informed the project context and font mapping.
- Things 2.2.4 by Colin Eckert: [`9b8bef93d3919f7693ac78597beaa35bbbd4cfff`](https://github.com/colineckert/obsidian-things/blob/9b8bef93d3919f7693ac78597beaa35bbbd4cfff/theme.css). See JSON `origin` fields for exact Things colors versus Kairos derivatives.
- [TypeSafe / Jev](https://typesafe.ai/) informed the emphasis on typed decisions and visible uncertainty.
- Noto fallback fonts: SIL Open Font License, included beside the fonts.

The Things stylesheet is not bundled or copied as an interface. Its palette and principles inform the original Kairos system. `.gpl` in `palette/` means **GIMP Palette**, not a software license.
