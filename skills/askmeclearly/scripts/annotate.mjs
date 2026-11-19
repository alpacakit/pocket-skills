#!/usr/bin/env node
// Crops screenshots to the changed area and marks each change with a red box
// and a numbered label, so every choice in one question shows the same area.
// Needs only Node 18.3+: the renderer (resvg, WebAssembly) is bundled under
// vendor/, and label text uses the fonts installed on this computer.
//
//   node annotate.mjs spec.json [--color #e0242a] [--font-size 13] [--stroke 3]
//
// --color       box and label colour (any SVG colour), default #e0242a
// --font-size   label text size before "scale", default 13
// --stroke      box line width before "scale", default 3
//
// spec.json (paths are relative to the spec file; numbers are in the
// coordinates of the screenshots, multiplied by "scale" when given):
// {
//   "scale": 2,              // optional: e.g. CSS px from a 2x browser shot
//   "pad": 24,               // optional: margin around the marks, default 24
//   "lang": "zh-CN",         // optional: BCP 47 tag of the labels, picks regional Han forms
//   "crop": { "x": 0, "y": 0, "width": 800, "height": 400 },  // optional
//   "images": [
//     { "input": "now.png", "output": "q1-now.png", "marks": [] },
//     { "input": "opt1.png", "output": "q1-opt1.png",
//       "marks": [{ "x": 40, "y": 120, "width": 160, "height": 28, "label": "① Name" }] }
//   ]
// }
//
// Inputs are PNG. Without "crop", the area is every mark and label of every
// image plus "pad", the same for all images. Prints the outputs, the area,
// and the fonts used as JSON.

import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { initWasm, Resvg } from "./vendor/resvg-wasm/index.mjs";
import { failWith, readCommand } from "./cli.mjs";
import { pickFonts } from "./fonts.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const fail = failWith("annotate");

function escapeXml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;");
}

// ── Input ─────────────────────────────────────────────────────────────────

function pngSize(buffer, path) {
  if (buffer.length < 24 || buffer.readUInt32BE(0) !== 0x89504e47 || buffer.toString("latin1", 12, 16) !== "IHDR") fail(`${path} is not a PNG`);
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

async function readSpec(specPath) {
  let spec;
  try {
    spec = JSON.parse(await readFile(specPath, "utf8"));
  } catch (error) {
    fail(`cannot read ${specPath}: ${error.message}`);
  }
  if (!Array.isArray(spec.images) || spec.images.length === 0) fail("spec.images must list at least one image");
  const base = dirname(resolve(specPath));
  const images = [];
  for (const image of spec.images) {
    if (!image.input || !image.output) fail("each image needs input and output");
    const input = resolve(base, image.input);
    let png;
    try {
      png = await readFile(input);
    } catch (error) {
      fail(`cannot open ${input}: ${error.message}`);
    }
    images.push({ png, ...pngSize(png, input), output: resolve(base, image.output), marks: image.marks ?? [] });
  }
  return { spec, images };
}

// ── Labels ────────────────────────────────────────────────────────────────

// Each font run is its own <text>: inside one <text>, the renderer keeps
// drawing later runs with an earlier run's font.
function runText(run, x, y, look) {
  return `<text x="${x}" y="${y}" font-family="'${escapeXml(run.family)}'" font-size="${look.fontSize}" font-weight="700" fill="#fff" xml:space="preserve">${escapeXml(run.text)}</text>`;
}

// Left-to-right runs are cut at spaces and the spaces laid out here: the
// renderer drops the space between words in some fonts (Devanagari in
// Kohinoor). Right-to-left runs stay whole, or their word order would flip.
const RTL = /[\p{Script=Arabic}\p{Script=Hebrew}\p{Script=Syriac}\p{Script=Thaana}\p{Script=Nko}]/u;
function words(run) {
  if (RTL.test(run.text)) return [run];
  return run.text.split(/( +)/).filter(Boolean).map((text) => ({ family: run.family, text }));
}

// Lays out every label from one render that draws each font run on its own
// row: the drawn pixels give each run's width and its height above and below
// the baseline.
function layOutLabels(labels, fonts, look, renderOptions) {
  const { fontSize } = look;
  const labelRuns = labels.map((text) => fonts.runs(text).flatMap(words));
  const runs = labelRuns.flat();
  if (runs.length === 0) return [];
  const row = fontSize * 3;
  const baselineOf = (i) => i * row + fontSize * 2;
  const space = fontSize * 0.3; // trailing spaces draw nothing to measure
  const sheet = new Resvg(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${fontSize * 80}" height="${row * runs.length}">` +
      runs.map((run, i) => runText(run, 0, baselineOf(i), look)).join("") +
      "</svg>",
    renderOptions,
  ).render();
  const pixels = sheet.pixels; // a copy out of WebAssembly memory: read it once
  const metrics = runs.map((run, i) => {
    let right = -1, top = Infinity, bottom = -1;
    for (let y = i * row; y < (i + 1) * row; y++) {
      for (let x = 0; x < sheet.width; x++) {
        if (pixels[(y * sheet.width + x) * 4 + 3] > 24) {
          right = Math.max(right, x);
          top = Math.min(top, y);
          bottom = Math.max(bottom, y);
        }
      }
    }
    if (right < 0) return { width: run.text.length * space, above: 0, below: 0 };
    const trailing = run.text.length - run.text.trimEnd().length;
    return { width: right + 1 + Math.round(fontSize * 0.06) + trailing * space, above: baselineOf(i) - top, below: bottom + 1 - baselineOf(i) };
  });
  let measured = 0;
  return labelRuns.map((parts) => {
    let width = 0, above = 0, below = 0;
    const placed = parts.map((run) => {
      const m = metrics[measured++];
      const part = { run, dx: width };
      width += m.width;
      above = Math.max(above, m.above);
      below = Math.max(below, m.below);
      return part;
    });
    return { parts: placed, width: Math.round(width), above, below };
  });
}

// ── Geometry ──────────────────────────────────────────────────────────────

function scaled(rect, k) {
  return { x: rect.x * k, y: rect.y * k, width: rect.width * k, height: rect.height * k };
}

function union(rects) {
  const x1 = Math.min(...rects.map((r) => r.x));
  const y1 = Math.min(...rects.map((r) => r.y));
  const x2 = Math.max(...rects.map((r) => r.x + r.width));
  const y2 = Math.max(...rects.map((r) => r.y + r.height));
  return { x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
}

function clamp(rect, width, height) {
  const x = Math.max(0, Math.floor(rect.x));
  const y = Math.max(0, Math.floor(rect.y));
  return { x, y, width: Math.min(width, Math.ceil(rect.x + rect.width)) - x, height: Math.min(height, Math.ceil(rect.y + rect.height)) - y };
}

// The box outline sits outside the marked area; its label sits above it, or
// below it when there is no room.
function placeMark(mark, layout, look) {
  const box = scaled(mark, look.k);
  const s = look.stroke;
  const outline = { x: box.x - s, y: box.y - s, width: box.width + s * 2, height: box.height + s * 2 };
  if (!layout) return { outline, label: null };
  const height = Math.max(Math.round(look.fontSize * 1.5), layout.above + layout.below + look.padY * 2);
  const above = outline.y - look.gap - height;
  return {
    outline,
    label: {
      parts: layout.parts,
      x: outline.x,
      y: above >= 0 ? above : outline.y + outline.height + look.gap,
      width: layout.width + look.padX * 2,
      height,
      baseline: Math.round((height - layout.above - layout.below) / 2 + layout.above),
    },
  };
}

function chooseArea(spec, images, look) {
  const width = Math.min(...images.map((i) => i.width));
  const height = Math.min(...images.map((i) => i.height));
  const rects = images.flatMap((i) => i.placed.flatMap((p) => (p.label ? [p.outline, p.label] : [p.outline])));
  let area;
  if (spec.crop) area = clamp(scaled(spec.crop, look.k), width, height);
  else if (rects.length) {
    const u = union(rects);
    area = clamp({ x: u.x - look.pad, y: u.y - look.pad, width: u.width + look.pad * 2, height: u.height + look.pad * 2 }, width, height);
  } else area = { x: 0, y: 0, width, height };
  if (area.width <= 0 || area.height <= 0) fail(`the area ${JSON.stringify(area)} is outside the images`);
  return area;
}

// ── Drawing ───────────────────────────────────────────────────────────────

// One render per image: the viewBox crops, the overlay draws on top.
function draw(image, area, look, renderOptions) {
  const color = escapeXml(look.color);
  const overlay = image.placed.map(({ outline: o, label: l }) => {
    const box = `<rect x="${o.x}" y="${o.y}" width="${o.width}" height="${o.height}" rx="${2 * look.k}" fill="none" stroke="${color}" stroke-width="${look.stroke}"/>`;
    if (!l) return box;
    return box +
      `<rect x="${l.x}" y="${l.y}" width="${l.width}" height="${l.height}" rx="${3 * look.k}" fill="${color}"/>` +
      l.parts.map((part) => runText(part.run, l.x + look.padX + part.dx, l.y + l.baseline, look)).join("");
  });
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${area.width}" height="${area.height}" viewBox="${area.x} ${area.y} ${area.width} ${area.height}">` +
    `<image href="data:image/png;base64,${image.png.toString("base64")}" x="0" y="0" width="${image.width}" height="${image.height}"/>` +
    overlay.join("") +
    "</svg>";
  return new Resvg(svg, renderOptions).render().asPng();
}

// ── Main ──────────────────────────────────────────────────────────────────

const command = readCommand("annotate");
const { spec, images } = await readSpec(command.specPath);
const k = spec.scale ?? 1;
const look = {
  k,
  color: command.look.color,
  fontSize: Math.round(command.look.fontSize * k),
  stroke: Math.max(1, Math.round(command.look.stroke * k)),
  pad: (spec.pad ?? 24) * k,
  gap: Math.round(4 * k),
  padX: Math.round(6 * k),
  padY: Math.round(3 * k),
};

await initWasm(readFile(resolve(here, "vendor/resvg-wasm/index_bg.wasm")));

const labels = images.flatMap((i) => i.marks.filter((m) => m.label).map((m) => String(m.label)));
const fonts = await pickFonts(labels.join(""), spec.lang ? { lang: spec.lang } : {});
if (fonts.missing.length) console.error(`annotate: no installed font that this script can draw has ${fonts.missing.join(" ")}; those characters will be blank or boxes`);
const renderOptions = {
  font: { loadSystemFonts: false, fontBuffers: await Promise.all(fonts.files.map((f) => readFile(f))) },
  ...(spec.lang ? { languages: [spec.lang] } : {}),
};

const layouts = layOutLabels(labels, fonts, look, renderOptions);
let next = 0;
for (const image of images) image.placed = image.marks.map((mark) => placeMark(mark, mark.label ? layouts[next++] : null, look));
const area = chooseArea(spec, images, look);

for (const image of images) await writeFile(image.output, draw(image, area, look, renderOptions));
console.log(JSON.stringify({ outputs: images.map((i) => i.output), area, fonts: fonts.families }, null, 2));
