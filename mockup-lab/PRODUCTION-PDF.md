# Mockup Lab production PDF export — v8.40

Released September 7, 2026. Extends the existing Mockup Lab download step with artwork PDFs for Separation Studio NXT.

## Shop workflow

1. Upload an original SVG or PDF in Logos. For SVGs, review the detected background color and choose whether to remove that color throughout the artwork. Use flat vector artwork with text converted to curves.
2. Set the print location and width. Original vector path colors are matched to the existing Mockup Lab Pantone library; review or change those assignments in Ink Colors.
3. Save This Group includes approval pages, the shop sheet, and a `production-pdfs` folder. PDFs are generated for the light/dark setups used by that group's garments. Production PDFs can also be downloaded individually from the Production PDFs panel.

The PDFs have named spot colors, preserve vector paths, and use the selected width with a fitted page. Light/dark overrides, back-side overrides, sleeve inks, sleeve quarter turns, and ink-off settings follow the app. Live Ink off retains original source colors.

Source PDF files are stored in the current browser's IndexedDB (`mayhem-production-pdf`, `sources`). Saved-job JSON contains source identifiers and crop metadata, not the original file bytes. Resuming in the same browser retains production export when storage is available. Another browser/computer, cleared storage, or older jobs require re-uploading the originals. No source PDF is sent to a server by this feature.

## Supported scope and limits

- Flat, filled SVGs and outlined vector PDFs, up to 32 detected source inks. PDF page rotations and sleeve 90° turns are supported. Placement rotation must be zero for production export.
- Text objects, embedded images/forms, patterns, gradients, annotations, and transparency/effects are rejected for production export with a preparation message. The normal mockup workflow remains available.
- PNG/JPG locations are not converted to production PDFs. The download panel and the ZIP manifest identify locations without an original PDF. If an uploaded PDF cannot be exported, the combined download stops and explains why. Unchecking production artwork keeps the approval/shop-only workflow available.
- The crop is measured from a transparent rendering of the source page. The exported artwork itself stays vector. Page dimensions preserve the original visible footprint, including when an ink is disabled, so print alignment stays consistent. A painted white background counts as source artwork and is preserved.
- The production PDF is artwork only. Underbase generation, separations, registration marks, and film preparation remain in NXT. Approval branding/watermarks remain unchanged.
- Pantone RGB swatches are screen approximations. Automatic matching selects the closest color in the app's existing library, not a physical ink measurement.

## Implementation and verification

The app remains a single HTML file, with bundled pdf-lib and an isolated production module after the existing scripts. `buildJobZip` adds production entries directly to the original ZIP. Existing `mockuplab.job.v1` saves remain readable; production metadata is additive. The test fixture and test controls are not published.

Verified with Joe's Corel PDF: 4.000-inch page width, PMS 225 and PMS White, no raster image objects. Joe manually confirmed the prototype's names and dimensions in NXT. The integrated build also passed changed dark inks, white-off, browser source recovery, light/dark batch files, ZIP CRC/manifest validation, approval/shop-only downloads, and explicit missing/unsupported-source errors. All inline scripts pass Node syntax checks.

## SVG background knockout — v8.40, September 7, 2026

Kolton approved the all-background-color knockout (option B) on the Music Speaks / Dixie Flyer design. Remove the selected exact RGB color throughout the SVG, including enclosed holes and dark details, so the garment shows through. The upload dialog displays original and print artwork side by side, defaults to the color of a shape covering all four page corners, and allows another source color or Keep all colors. Cancellation leaves the job unchanged; a choice that removes all artwork cannot be applied.

The importer converts filled SVG paths and basic shapes (including transforms, compound paths, arcs, and rounded rectangles) into vector PDF paths. Recognizable Vectorizer seam-filler strokes are removed: only a leading stroke-only group with non-scaling paths and stroke colors matching midpoints of the actual filled palette qualifies. Other strokes and unsupported SVG effects are rejected for vector production, with an optional fallback to the existing mockup-only SVG loader. Source artwork is parsed with a strict tag/attribute allowlist; scripts, external references, masks, CSS, nested viewports, and text are outside this release's vector-import scope.

Retained vector colors are detected before antialiasing; unlike the raster clustering path, distinct vector colors are not silently truncated to eight or merged just for being similar. They may still resolve to the same Pantone through automatic matching. User-selected Pantones, light/dark setup choices, back overrides, ink-off, and source-preserving Live Ink off follow the existing production pipeline.

The cleaned source SVG is also stored in IndexedDB and can be downloaded from Production PDFs with CLEANED SVG — SOURCE COLORS. It is cropped and editable with original RGB fills; use the PDF for the selected production width, Pantone inks, and sleeve rotation. Re-upload the untouched SVG to change the background-removal choice. Cleanup metadata is included in the production ZIP manifest. Jobs resumed on another computer need their vector originals re-uploaded.

Verified directly from both untouched Vectorizer files supplied by Kolton: music SVG auto-detects #131212, removes 202 background-color shapes and 959 seam strokes, exports exactly 4 x 4.4923747277 inches with PMS 7500 and PMS 7545 and zero raster images. The plumbing SVG retains all 14 colors with Keep all colors, or removes all 15 white shapes when white is selected. Tests cover original-color retention, no-background and all-white detection, malformed/unsupported input, source recovery after saved-job restore, actual file-input upload and acceptance, sleeve rotation, combined approval/shop/production ZIP CRC and metadata, and all inline-script syntax checks. The approved B sample was visually confirmed by Kolton; the integrated output was structurally and visually verified.
