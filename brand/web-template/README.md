# Kairos web template

A responsive, dependency-free UI starter using the Kairos #09 brand system.

## Run

Serve the **parent brand-pack directory** so palette and PDF guide links work:

```sh
python -m http.server 8000
```

Then open `http://localhost:8000/web-template/`. Opening `index.html` directly also works in modern browsers; local storage and clipboard behavior may vary under `file://`.

## Files

- `index.html`: semantic markup, SVG UI symbols, dashboard, activity, component library and dialogs.
- `styles.css`: layout, responsive breakpoints, components, font mappings and reduced-motion support.
- `tokens.css`: complete palette with light/dark and violet/green/red accents.
- `app.js`: browser-only interactions; no backend or network requests.
- `manifest.webmanifest`: app name and icon metadata.
- `assets/`: primary logo modes, app icons and fallback fonts.
- `previews/`: all six accent/mode combinations, including desktop, mobile and UI-library PNGs.
- `FONTS.md`: primary-font setup.

## Interactive behavior

- Start/pause a presentation-only demo and record activity.
- Change illustrative chart periods.
- Filter the position table, including an empty state and reset.
- Review an assessment and mark it reviewed.
- Edit settings with validation; adjust the illustrative threshold.
- Switch light/dark/system appearance and violet/green/red accent. Both preferences persist locally.
- Use button states, loading example, form controls, switch, radio groups, slider, keyboard tabs, alerts, toast, disclosure, confirmation dialog and color copying in the UI library.

Balances and sample prices are static. Changing the allocation field does not recalculate them. The demo makes no exchange requests or model calls. Preferences other than theme/accent and the activity log are session-only.

## Theme contract

```html
<html data-theme="light" data-accent="red">
```

The primary logo's lilac capsule remains unchanged. The accent changes action, hover, selected-background and focus tokens. Success and loss/error colors remain semantic in all modes.

## Fonts

Primary: Neo Sans Pro. Monospace: Operator Mono Lig Nerd Font. Bold monospace: OperatorMonoSSmLig Nerd Font. The `Kairos UI` and `Kairos Mono` aliases resolve local fonts; see `FONTS.md` for deployment.

## Integration

1. Keep `tokens.css` as the token authority.
2. Replace the sample data and demo handlers with your application state.
3. Keep explicit simulation/live labels when connecting real services.
4. Serve your licensed primary fonts; maintain the supplied accessible labels and focus states.
5. If extracting only this folder, update the two parent-relative guide/palette links.

Use the full brand guide for logo clear space, mode mapping, color roles, typography, layout and product voice.
