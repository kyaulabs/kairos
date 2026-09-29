# Font handoff

The intended typography matches the Kairos `develop` branch:

| Role | Family | Existing local PostScript name |
| --- | --- | --- |
| Body / UI | Neo Sans Pro Regular | `NeoSansPro-Regular` |
| Labels | Neo Sans Pro Medium | `NeoSansPro-Medium` |
| Headings | Neo Sans Pro Bold | `NeoSansPro-Bold` |
| Logo | Neo Sans Pro Ultra, supplied as paths | `NeoSansPro-Ultra` |
| Monospace | Operator Mono Lig Nerd Font Regular | `OperatorMonoLigNF` |
| Bold monospace | OperatorMonoSSmLig Nerd Font Bold | `OperatorMonoSSmLigNF-Bold` |

`styles.css` declares `Kairos UI` and `Kairos Mono` using these local names. `tokens.css` selects those aliases first. This reproduces the intended fonts on a machine where they are installed.

For production, use your licensed webfont files. Place them under `assets/fonts/` and extend the corresponding rules, for example:

```css
@font-face {
  font-family: "Kairos UI";
  src: local("NeoSansPro-Regular"),
       url("assets/fonts/NeoSansPro-Regular.woff2") format("woff2");
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "Kairos Mono";
  src: local("OperatorMonoLigNF"),
       url("assets/fonts/OperatorMonoLigNF.woff2") format("woff2");
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}
```

Repeat for Medium, Bold and bold monospace as needed. The `.woff2` files in the example are deployment slots, not files included in this ZIP.

The pack does not redistribute Neo Sans Pro or Operator Mono font binaries. Its logos and vector boards are outlined and do not require them. The PDF guide uses the intended fonts through embedding/outlined specimens. The template also includes licensed Noto Sans and Noto Sans Mono as explicit fallbacks; these are not the primary brand typography. Their SIL OFL is in `assets/fonts/OFL.txt`.
