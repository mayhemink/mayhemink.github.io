# Mockup Lab production PDF export — v8.39

Released September 7, 2026. Extends the existing Mockup Lab download step with artwork PDFs for Separation Studio NXT.

## Shop workflow

1. Upload the original PDF in Logos. Use flat vector artwork with text converted to curves.
2. Set the print location and width. Original PDF path colors are matched to the existing Mockup Lab Pantone library; review or change those assignments in Ink Colors.
3. Save This Group includes approval pages, the shop sheet, and a `production-pdfs` folder. PDFs are generated for the light/dark setups used by that group's garments. Production PDFs can also be downloaded individually from the Production PDFs panel.

The PDFs have named spot colors, preserve vector paths, and use the selected width with a fitted page. Light/dark overrides, back-side overrides, sleeve inks, sleeve quarter turns, and ink-off settings follow the app. Live Ink off retains original source colors.

Source PDF files are stored in the current browser's IndexedDB (`mayhem-production-pdf`, `sources`). Saved-job JSON contains source identifiers and crop metadata, not the original file bytes. Resuming in the same browser retains production export when storage is available. Another browser/computer, cleared storage, or older jobs require re-uploading the originals. No source PDF is sent to a server by this feature.

## Supported scope and limits

- Flat, outlined vector PDFs, up to eight detected source inks. PDF page rotations and sleeve 90° turns are supported. Placement rotation must be zero for production export.
- Text objects, embedded images/forms, patterns, gradients, annotations, and transparency/effects are rejected for production export with a preparation message. The normal mockup workflow remains available.
- PNG/SVG/JPG locations are not converted to production PDFs. The download panel and the ZIP manifest identify locations without an original PDF. If an uploaded PDF cannot be exported, the combined download stops and explains why. Unchecking production artwork keeps the approval/shop-only workflow available.
- The crop is measured from a transparent rendering of the source page. The exported artwork itself stays vector. Page dimensions preserve the original visible footprint, including when an ink is disabled, so print alignment stays consistent. A painted white background counts as source artwork and is preserved.
- The production PDF is artwork only. Underbase generation, separations, registration marks, and film preparation remain in NXT. Approval branding/watermarks remain unchanged.
- Pantone RGB swatches are screen approximations. Automatic matching selects the closest color in the app's existing library, not a physical ink measurement.

## Implementation and verification

The app remains a single HTML file, with bundled pdf-lib and an isolated production module after the existing scripts. `buildJobZip` adds production entries directly to the original ZIP. Existing `mockuplab.job.v1` saves remain readable; production metadata is additive. The test fixture and test controls are not published.

Verified with Joe's Corel PDF: 4.000-inch page width, PMS 225 and PMS White, no raster image objects. Joe manually confirmed the prototype's names and dimensions in NXT. The integrated build also passed changed dark inks, white-off, browser source recovery, light/dark batch files, ZIP CRC/manifest validation, approval/shop-only downloads, and explicit missing/unsupported-source errors. All inline scripts pass Node syntax checks.
