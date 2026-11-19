// Keeps, while Playwright records a page, where the marks of the recording go:
// the mouse pointer and where it pressed (Playwright's video shows neither),
// and the boxes, captions, and zooms the recording script asks for. It does
// not touch the recording; save() writes a spec, and annotate-video.mjs draws
// the marks over the recording from it. Fix a label or a time in the spec and
// draw again, without recording again.
//
//   import { openMarkedPage } from "<this folder>/video-marks.mjs";
//   const context = await browser.newContext({ viewport, recordVideo: { dir, size: viewport } });
//   const { page, marks } = await openMarkedPage(context);
//   await page.goto(url);
//   marks.caption("① Hover a row to open its card");
//   await page.mouse.move(x, y, { steps: 8 });
//   marks.box(await page.locator(".card").boundingBox(), "① Card", { seconds: 2 });
//   ...
//   const video = page.video();
//   await context.close();
//   await video.saveAs("raw.webm");
//   await marks.save("spec.json", { input: "raw.webm", output: "marked.mp4" });
//
// Playwright starts a page's video when it creates the page, so
// openMarkedPage creates it and counts the spec's seconds from then.
// Positions are the page's CSS pixels.

import { writeFile } from "node:fs/promises";

// Runs in the page before its own scripts, on every page load.
function reportPointer() {
  for (const [type, kind] of [["mousemove", "pointer"], ["mousedown", "presses"]]) {
    addEventListener(type, (e) => globalThis.askmeclearlyMarks(kind, Date.now(), e.clientX, e.clientY), { capture: true, passive: true });
  }
}

// Seconds kept before the first mark.
const EDGE = 0.5;

function rect(box) {
  if (!box) throw new Error("video-marks: nothing to mark there (the element is not visible)");
  const { x, y, width, height } = box;
  return { x, y, width, height };
}

export async function openMarkedPage(context) {
  const page = await context.newPage();
  // Playwright's first frame comes within a frame or two (about 0.05 s)
  // after newPage resolves, however long newPage took.
  const startedAt = Date.now();
  const at = (time) => (time - startedAt) / 1000;
  const spec = { viewport: page.viewportSize(), pointer: [], presses: [], marks: [], captions: [], zoom: [] };
  const open = (list, item, seconds) => {
    const from = at(Date.now());
    spec[list].push({ from, to: seconds == null ? null : from + seconds, ...item });
  };
  const close = (list) => {
    const to = at(Date.now());
    for (const item of spec[list]) if (item.to == null) item.to = to;
  };

  await page.exposeBinding("askmeclearlyMarks", (_source, kind, time, x, y) => spec[kind].push([at(time), x, y]));
  await page.addInitScript(reportPointer);

  const marks = {
    // Outlines { x, y, width, height } (a locator's boundingBox()) with a
    // label. Without seconds it stays until clear().
    box(box, label, { seconds } = {}) {
      open("marks", { ...rect(box), label }, seconds);
    },
    // Shows a caption at the bottom, in place of the one before.
    caption(text, { seconds } = {}) {
      close("captions");
      open("captions", { text }, seconds);
    },
    // Zooms the recording into { x, y, width, height }, in place of any zoom before.
    zoom(box, { seconds } = {}) {
      close("zoom");
      open("zoom", rect(box), seconds);
    },
    // Ends every box, caption, and zoom still shown.
    clear() {
      for (const list of ["marks", "captions", "zoom"]) close(list);
    },
    // Writes the spec for annotate-video.mjs; paths are relative to the spec.
    // The drawing keeps from just before the first caption, box, or zoom
    // (cutting the page's loading) to when the last one ends, so it ends on
    // the last result rather than zooming back out.
    async save(path, { input, output }) {
      for (const kind of ["pointer", "presses"]) spec[kind].sort((a, b) => a[0] - b[0]);
      const items = [...spec.marks, ...spec.captions, ...spec.zoom];
      const start = items.length ? Math.max(0, Math.min(...items.map((item) => item.from)) - EDGE) : null;
      const end = items.length && items.every((item) => item.to != null) ? Math.max(...items.map((item) => item.to)) : null;
      await writeFile(path, JSON.stringify({ input, output, start, end, ...spec }, null, 2) + "\n");
    },
  };
  return { page, marks };
}
