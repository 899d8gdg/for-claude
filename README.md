# Car display model extractor

`tools/extract-models.mjs` opens a web page in headless Chromium and saves the
3D models its viewer loads (`.glb`, `.gltf` + `.bin`, `.usdz`, `.fbx`, `.obj`,
Draco/KTX2 files, HDR environment maps). This includes files loaded inside
viewer iframes and model URLs mentioned in the page's scripts but not yet
requested, such as other paint colours.

## Run

```sh
npm install
npx playwright install chromium
npm run extract -- https://jetour.com.pk/t1iDM models --images
```

- `models` is the output folder. Files keep their original host/path layout,
  and `models/manifest.json` lists every file captured with its source URL.
- `--images` also saves the page's images at 2x resolution.
- `--wait=30000` waits longer for slow viewers (the default is 15 s).

If no models appear, the 3D viewer probably needs a click to start, or it is
on a separate configurator page. Run the script on that page's URL.

The models belong to the site owner. Use them for personal viewing only.

# Download every PNG from a page (no install)

1. Open the page in Chrome or Edge and press F12, then open the **Console** tab.
2. Paste the contents of `tools/download-pngs.js` and press Enter. If Chrome
   warns about pasting, type `allow pasting` first.
3. Chrome asks to allow multiple downloads. Click **Allow**.

The snippet scrolls the page so lazy images load, then collects every PNG the
page loaded (the same list as Network → Img), including CSS backgrounds.
It downloads each one to your Downloads folder. PNGs on a CDN that blocks
cross-origin requests are listed in the console instead: open each link and
save it. The full URL list is also copied to your clipboard.
