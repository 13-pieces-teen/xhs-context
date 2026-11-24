# daisyUI 5.8.2

This directory contains selected, unmodified CSS assets from the official daisyUI npm package, distributed through jsDelivr. The extension loads these local files and makes no CDN requests at runtime. No JavaScript runtime or frontend framework is required.

- Upstream: https://github.com/saadeghi/daisyui
- Documentation: https://daisyui.com/docs/cdn/
- Version: **5.8.2** (pinned; do not use an unversioned CDN URL)
- License: **MIT**, Copyright (c) 2020 Pouya Saadeghi. See [LICENSE](LICENSE).
- Exact source URLs, byte counts, and SHA-256 hashes: [sources.json](sources.json).

## Included assets

| File | Purpose |
| --- | --- |
| `base/properties.css` | Registers the CSS custom property used to animate radial progress. |
| `theme/light.css` | Supplies component color, radius, sizing, border, and depth variables. |
| `components/button.css` | Native button styling and interaction states. |
| `components/radialprogress.css` | Circular progress indicators. |
| `components/badge.css` | Compact status badges. |
| `components/alert.css` | Status and error messages. |
| `components/input.css` | Native text and number input fields. |
| `components/checkbox.css` | Native checkbox styling and checked/focus states. |
| `components/collapse.css` | Expandable sections using native `details` and `summary`. |
| `components/card.css` | Content containers, titles, bodies, and grouped actions. |
| `components/list.css` | Structured rows for the unavailable-note list. |
| `components/navbar.css` | Header and footer alignment. |
| `components/link.css` | Anchor styling and interaction states. |
| `components/fieldset.css` | Form-field grouping and spacing. |
| `components/label.css` | Labels for form fields. |

The thirteen component stylesheets total **228,554 bytes** (223.20 KiB). All fifteen CSS files total **229,917 bytes** (224.53 KiB), before compression.

## Integration

Load `base/properties.css`, `theme/light.css`, and the component stylesheets before the application's `popup.css`. Apply project-specific colors, sizes, spacing, and reduced-motion rules in application CSS; do not edit the upstream files.

The existing application owns its document reset, body background, and foreground. daisyUI's `base/reset.css`, `base/rootcolor.css`, scrollbar utilities, and SVG data are intentionally omitted. Set `--fx-noise: none` on the application theme because the selected components reference this variable from daisyUI's optional SVG base stylesheet. The included light theme already sets `--noise: 0`. Optional `--join-*` variables in button styles have radius fallbacks and need no separate definitions.

The stylesheets contain no `@import` declarations. Component appearance can use standard HTML classes; the circular progress component also uses `--value`, `--size`, and `--thickness`. Retain native button semantics and provide `role="progressbar"` with appropriate ARIA values for progress indicators.

Without a global reset, explicitly retain application font inheritance on `body` and form controls, reset `fieldset` margins/borders/padding, and reset list margins/padding/markers where needed. The input component supplies its own border and appearance but does not set `font-family` or line height. Native fieldsets otherwise retain browser borders. The label component defaults to `white-space: nowrap`; labels with explanatory text may need wrapping in the compact panel.

For a native disclosure use `details.collapse`, `summary.collapse-title`, and one direct `.collapse-content` wrapper. Keep `details.open` and native keyboard behavior. The component's default spacing is sized in rem units; adjust padding and font sizing in application CSS to fit the panel. A list row's direct children form columns; wrap a single text column in `.list-col-grow`. The `.navbar` default minimum height is `4rem`, so the footer needs its own compact size. `.card-border` uses `--color-base-200`; set that border color through the application theme when it must differ from the component background.

Reserve `.link` for actual anchors; application-specific quiet-button classes must not share its name. Otherwise anchor underlines and focus rules would also affect those buttons. No new JavaScript, remote image, or runtime dependency is required by these components.

## Updates

Download the same paths from a deliberately selected exact package version, preserve the upstream bytes, update this document and `sources.json`, and verify every SHA-256 hash. Retain the upstream license. Validate the popup at its supported widths after any update.
