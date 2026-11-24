# Lucide 0.468.0

The SVG files in this directory are unmodified assets from the pinned `lucide-static@0.468.0` package. `sources.json` records each upstream URL, byte count, and SHA-256 digest. See `LICENSE` for the upstream license and Feather attribution.

`icons.js` is a generated, local map (`window.XHS_ICONS`) of these original SVG strings. The popup view inserts only named, bundled icons, preserving their `currentColor` strokes. This avoids remote requests and the restrictions that browsers apply to CSS masks loaded through `file://`; no icon runtime framework is needed.

To update, select an explicit upstream version, download the original assets and license, regenerate the map from the SVG filenames and contents, and update the provenance manifest. The popup's application CSS owns icon sizing and color.
