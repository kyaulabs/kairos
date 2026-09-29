# Cryptocurrency icons

Color SVGs from [spothq/cryptocurrency-icons](https://github.com/spothq/cryptocurrency-icons), created by Christopher Downer and contributors.

- Source revision: `1a63530be6e374711a8554f31b17e4cb92c25fa5`
- Source directory: `svg/color/`
- License: CC0 1.0 Universal; the unmodified upstream text is in `LICENSE.md`.
- Included: 90 icons for Kraken's online crypto catalog at import time. SVG contents are unchanged. Kraken's `XDG` symbol maps to the upstream `doge.svg`.
- Ambiguous or reused symbols are intentionally omitted: `ACT`, `BEAM`, `BLZ`, `BOS`, `CC`, `CTR`, `POLIS`, `SAFE`, `SKY`, and `WINGS`. Matching a ticker alone can assign another project's artwork.

`symbols.js` lists the bundled filenames without extensions. Kairos serves these assets locally; it does not call an icon API or CDN. Currencies without artwork, or images that fail to load, use ticker-letter badges instead. To add an icon from this source, copy its color SVG and add its lowercase filename stem to `symbols.js`.
