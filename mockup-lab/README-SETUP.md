# Mayhem Mockup Lab — Hosting Setup (mayhemink.github.io)

## Folder structure on the site

```
mayhemink.github.io/
└── mockup-lab/
    ├── index.html          ← the app (this folder's index.html)
    ├── styles.json         ← the brain: calibrations + placement rules + photo list
    └── styles/
        ├── DM108/
        │   ├── front/      ← drop SanMar front photos here, original filenames
        │   └── back/
        ├── DM130/
        │   ├── front/
        │   └── back/
        └── NL6210/
            ├── front/
            └── back/
```

## One-time setup

1. In the GitHub repo for mayhemink.github.io, create the `mockup-lab` folder and upload `index.html`.
2. Create the `styles/` folders and drag the SanMar photos in — **no renaming needed**, original filenames work (the app parses style/color/side from them).
3. Open the app locally, drop the same photos in, run **Calibrate this style + side** for each style front + back (5 clicks front, 4 back — it asks for body length from the spec sheet).
4. Hit **Export styles.json** and upload that file to `mockup-lab/`.
5. Done. `mayhemink.github.io/mockup-lab/` now loads the entire catalog automatically — badge in the header shows "catalog: hosted · live".

## Adding a new style later

1. Drop the new style's photos into `styles/{STYLE}/front/` and `back/` in the repo.
2. Open the live app, drop the same photos in locally, calibrate once, Export styles.json, replace the old styles.json in the repo.
3. That style is now permanently ready.

(For styles the app doesn't know yet, the exported styles.json carries the new style definition too — the app reads styles from the manifest, so new styles don't need code changes.)

## Calibration cheat sheet

| Click | What it sets |
|---|---|
| Collar top → hem bottom + body length (spec sheet, size M) | Real inches-per-pixel |
| Center line | Center seam anchor |
| Neckline-meets-shoulder point | Collarbone line (all "X inches down" rules measure from here) |
| Left-chest line (front only) | The vertical line left-chest logos center on |

Placement rules ("left chest = 3.5-inch wide, 2.5-inch below collarbone on the left-chest line") are stored in real inches in styles.json and apply to every calibrated style identically.
