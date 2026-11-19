#!/usr/bin/env node
// Draws the marks of a Playwright screen recording over it: the mouse pointer,
// a ring where it pressed, red boxes with labels in the look of annotate.mjs,
// captions, and zooms, from the spec video-marks.mjs saved while recording.
// Needs Node 18.3+ and a Chromium-family browser already on this computer
// (Chrome, Edge, Chromium, Brave): it plays the recording there, draws the
// marks over each frame, and records the result. The browser draws the text,
// so labels may be in any script, right to left too.
//
//   node annotate-video.mjs spec.json [--color #e0242a] [--font-size 13] [--stroke 3] [--browser <path>]
//
// spec.json (paths relative to the spec; times in seconds from the start of
// the recording; positions in the page's CSS pixels):
// {
//   "input": "raw.webm",
//   "output": "marked.webm",       // .webm (VP9) or .mp4 (H.264, when the browser can)
//   "viewport": { "width": 1440, "height": 900 },   // the page's size; scales positions to the video
//   "pointer":  [[0.4, 700, 880], ...],             // where the mouse is from that time on
//   "presses":  [[6.1, 720, 300], ...],             // where it pressed
//   "marks":    [{ "from": 1.5, "to": 4, "x": 40, "y": 120, "width": 160, "height": 28, "label": "① Name" }],
//   "captions": [{ "from": 0, "to": 4, "text": "① Hover a row" }],
//   "zoom":     [{ "from": 5, "to": 8, "x": 600, "y": 200, "width": 480, "height": 300 }],
//   "start": 1.2, "end": 20.5                       // the part of the recording kept; null for its start or end
// }
// "to": null keeps the item to the end. The recording plays once in real
// time, so drawing takes as long as the recording, plus a few seconds.
// Text, lines, and the pointer are sized for the video watched 800 px wide
// (a chat window), or at its own size when narrower: there they look as in
// annotate.mjs's images.

import { spawn } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, extname, join, resolve } from "node:path";
import { failWith, readCommand } from "./cli.mjs";

const fail = failWith("annotate-video");

// ── Input ─────────────────────────────────────────────────────────────────

async function readSpec(specPath) {
  let spec;
  try {
    spec = JSON.parse(await readFile(specPath, "utf8"));
  } catch (error) {
    fail(`cannot read ${specPath}: ${error.message}`);
  }
  if (!spec.input || !spec.output) fail("the spec needs input and output");
  const base = dirname(resolve(specPath));
  const input = resolve(base, spec.input);
  const output = resolve(base, spec.output);
  // Also through a link or a second name: the same file on disk.
  const same = (a, b) => a.ino !== 0 && a.dev === b.dev && a.ino === b.ino;
  const outputFile = statSync(output, { throwIfNoEntry: false });
  const inputFile = statSync(input, { throwIfNoEntry: false });
  if (input === output || (inputFile && outputFile && same(inputFile, outputFile))) {
    fail("output must differ from input, or drawing again would have no clean recording to start from");
  }
  const outputType = { ".webm": "webm", ".mp4": "mp4" }[extname(output).toLowerCase()];
  if (!outputType) fail(`output must end in .webm or .mp4, not ${spec.output}`);
  let video;
  try {
    video = await readFile(input);
  } catch (error) {
    fail(`cannot open ${spec.input}: ${error.message}`);
  }
  const timeline = {};
  for (const key of ["pointer", "presses", "marks", "captions", "zoom"]) timeline[key] = spec[key] ?? [];
  const start = spec.start ?? 0, end = spec.end ?? null;
  if (!(start >= 0) || (end != null && !(end > start))) fail("start must be 0 or more, and end later than start");
  return { video, output, outputType, viewport: spec.viewport ?? null, timeline, start, end };
}

// ── Browser ───────────────────────────────────────────────────────────────

function findBrowser(given) {
  if (given) return statSync(given, { throwIfNoEntry: false })?.isFile() ? given : fail(`no browser program at ${given}`);
  const candidates = {
    darwin: [
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
      "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ],
    win32: [
      ...[process.env.PROGRAMFILES, process.env["PROGRAMFILES(X86)"], process.env.LOCALAPPDATA].filter(Boolean).flatMap((root) => [
        join(root, "Google/Chrome/Application/chrome.exe"),
        join(root, "Microsoft/Edge/Application/msedge.exe"),
        join(root, "BraveSoftware/Brave-Browser/Application/brave.exe"),
      ]),
    ],
  }[process.platform] ?? ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser", "brave"].flatMap((name) =>
    (process.env.PATH ?? "").split(":").map((dir) => join(dir, name)),
  );
  return candidates.find((path) => existsSync(path)) ?? fail("no Chrome, Edge, Chromium, or Brave found; pass --browser <path>");
}

// Talks to the browser over the DevTools pipe (fd 3 in, fd 4 out): JSON
// messages ended by a NUL byte. No WebSocket or package needed.
async function openBrowser(path) {
  const profile = await mkdtemp(join(tmpdir(), "annotate-video-"));
  const child = spawn(path, [
    "--headless=new", "--remote-debugging-pipe", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--mute-audio",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"] });
  // A browser that fails to start closes the pipes; that is reported once, below.
  for (const stream of [child.stdio[3], child.stdio[4]]) stream?.on("error", () => {});
  let errors = "";
  child.stderr.on("data", (chunk) => (errors = (errors + chunk).slice(-4000)));
  const pending = new Map();
  let nextId = 1, buffered = Buffer.alloc(0);
  child.stdio[4].on("data", (chunk) => {
    buffered = Buffer.concat([buffered, chunk]);
    let end;
    while ((end = buffered.indexOf(0)) >= 0) {
      const message = JSON.parse(buffered.subarray(0, end).toString("utf8"));
      buffered = buffered.subarray(end + 1);
      const waiter = pending.get(message.id);
      if (!waiter) continue;
      pending.delete(message.id);
      if (message.error) waiter.reject(new Error(message.error.message));
      else waiter.resolve(message.result);
    }
  });
  let running = true;
  const exited = new Promise((_, reject) => {
    child.on("error", (error) => {
      running = false;
      reject(new Error(`cannot start the browser at ${path}: ${error.message}`));
    });
    child.on("exit", (code) => {
      running = false;
      reject(new Error(`the browser quit (${code}): ${errors.trim().split("\n").pop() ?? ""}`));
    });
  });
  exited.catch(() => {});
  const send = (method, params = {}, sessionId) => {
    const id = nextId++;
    const reply = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    child.stdio[3].write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + "\0");
    return Promise.race([reply, exited]);
  };
  // Asks the browser to quit, so its helper processes are done with the
  // profile before it is removed.
  const close = async () => {
    if (running) {
      send("Browser.close").catch(() => {});
      const timer = setTimeout(() => child.kill(), 5000);
      await exited.catch(() => {});
      clearTimeout(timer);
    }
    await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  };
  return { send, close };
}

// ── In the page ───────────────────────────────────────────────────────────

// Plays the recording, draws each frame with its marks on a canvas, and
// records the canvas. Runs in the browser as one function, so its helpers
// live inside it. Returns the result as JSON with the video in base64.
async function renderInPage({ videoBase64, outputType, viewport, timeline, look, start, end }) {
  const RING_SECONDS = 0.6;
  const ZOOM_SECONDS = 0.8;
  // A pointer arrow 20 px tall with its tip at 0,0.
  const ARROW = new Path2D("M0 0 L0 17 L4.6 13.1 L7.6 20 L10.6 18.7 L7.7 12 L13 12 Z");
  const RTL = /^[^\p{L}]*[\p{Script=Arabic}\p{Script=Hebrew}\p{Script=Syriac}\p{Script=Thaana}\p{Script=Nko}]/u;
  // The system UI font of some computers (SF on macOS) draws circled numbers
  // small and thin; labels lead with them, so take them from a font that
  // draws them at full size when one is installed.
  const NUMBERS = "askmeclearly-numbers";
  const FAMILY = `"${NUMBERS}", system-ui, sans-serif`;
  const faces = await Promise.allSettled(
    ["Hiragino Sans W6", "HiraginoSans-W6", "Yu Gothic UI Bold", "Meiryo UI Bold", "Noto Sans CJK JP Bold", "Noto Sans CJK SC Bold", "Segoe UI Symbol"].map((name) =>
      new FontFace(NUMBERS, `local("${name}")`, { weight: "100 900", unicodeRange: "U+2460-24FF, U+2776-2793, U+3251-325F, U+32B1-32BF" }).load(),
    ),
  );
  const face = faces.find((f) => f.status === "fulfilled");
  if (face) document.fonts.add(face.value);

  const shown = (item, t) => t >= item.from && (item.to == null || t < item.to);
  const ease = (p) => (p <= 0 ? 0 : p >= 1 ? 1 : p * p * (3 - 2 * p));
  const direction = (text) => (RTL.test(text) ? "rtl" : "ltr");
  const lastAt = (samples, t) => {
    let found = null;
    for (const sample of samples) {
      if (sample[0] > t) break;
      found = sample;
    }
    return found;
  };

  const bytes = Uint8Array.from(atob(videoBase64), (c) => c.charCodeAt(0));
  const video = document.createElement("video");
  video.muted = true;
  video.src = URL.createObjectURL(new Blob([bytes]));
  await new Promise((done, failed) => {
    video.onloadeddata = done;
    video.onerror = () => failed(new Error(`the browser cannot play the input (${video.error?.message ?? "unknown format"})`));
  });
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  document.body.append(canvas);
  const ctx = canvas.getContext("2d");
  // CSS px to video px, the same for sizes, as "scale" in annotate.mjs.
  // Playwright shrinks the page to fit a smaller video without stretching it,
  // never enlarges it, and puts it at the top left with gray around it.
  const k = viewport ? Math.min(1, canvas.width / viewport.width, canvas.height / viewport.height) : 1;
  // Video px per px of a view 800 px wide, for text, lines, and the pointer;
  // a smaller video is watched at its own size, so they keep annotate.mjs's.
  const u = Math.max(1, canvas.width / 800);
  const full = { x: 0, y: 0, width: canvas.width, height: canvas.height };

  // The part of the video on screen at t, in video px: all of it, or the
  // part the latest zoom shows, kept in the video's shape. Each change of
  // zoom glides over ZOOM_SECONDS from wherever the view was.
  const zooms = [...timeline.zoom].sort((a, b) => a.from - b.from);
  const areaOf = (z) => {
    let width = z.width * k, height = z.height * k;
    if (width / height > full.width / full.height) height = (width * full.height) / full.width;
    else width = (height * full.width) / full.height;
    width = Math.min(width, full.width);
    height = Math.min(height, full.height);
    const x = Math.min(Math.max((z.x + z.width / 2) * k - width / 2, 0), full.width - width);
    const y = Math.min(Math.max((z.y + z.height / 2) * k - height / 2, 0), full.height - height);
    return { x, y, width, height };
  };
  const targetAt = (t) => {
    let target = full;
    for (const z of zooms) if (shown(z, t)) target = areaOf(z);
    return target;
  };
  const between = (a, b, p) => ({ x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p, width: a.width + (b.width - a.width) * p, height: a.height + (b.height - a.height) * p });
  const changes = [...new Set(zooms.flatMap((z) => (z.to == null ? [z.from] : [z.from, z.to])))].sort((a, b) => a - b);
  const viewAt = (t) => {
    let start = full, target = full, changedAt = -Infinity;
    for (const at of changes) {
      if (at > t) break;
      start = between(start, target, ease((at - changedAt) / ZOOM_SECONDS));
      target = targetAt(at);
      changedAt = at;
    }
    return between(start, target, ease((t - changedAt) / ZOOM_SECONDS));
  };

  // Positions follow the zoom; line widths and text keep their size.
  const drawMarks = (t, view) => {
    const sx = canvas.width / view.width, sy = canvas.height / view.height;
    const px = (x) => (x * k - view.x) * sx;
    const py = (y) => (y * k - view.y) * sy;

    for (const mark of timeline.marks) {
      if (!shown(mark, t)) continue;
      const s = look.stroke * u;
      const x = px(mark.x) - s, y = py(mark.y) - s;
      const width = mark.width * k * sx + s * 2, height = mark.height * k * sy + s * 2;
      ctx.lineWidth = s;
      ctx.strokeStyle = look.color;
      ctx.beginPath();
      ctx.roundRect(x + s / 2, y + s / 2, width - s, height - s, 2 * u);
      ctx.stroke();
      if (!mark.label) continue;
      ctx.font = `700 ${look.fontSize * u}px ${FAMILY}`;
      ctx.direction = direction(mark.label);
      ctx.textAlign = "start";
      ctx.textBaseline = "middle";
      const labelWidth = ctx.measureText(mark.label).width + 12 * u;
      const labelHeight = Math.round(look.fontSize * u * 1.6);
      const above = y - 4 * u - labelHeight;
      const ly = above >= 0 ? above : y + height + 4 * u;
      const lx = Math.max(0, Math.min(x, canvas.width - labelWidth)); // on screen when the box runs past the zoom
      ctx.fillStyle = look.color;
      ctx.beginPath();
      ctx.roundRect(lx, ly, labelWidth, labelHeight, 3 * u);
      ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.fillText(mark.label, ctx.direction === "rtl" ? lx + labelWidth - 6 * u : lx + 6 * u, ly + labelHeight / 2 + 0.5 * u);
    }

    for (const [at, x, y] of timeline.presses) {
      const p = (t - at) / RING_SECONDS;
      if (p < 0 || p >= 1) continue;
      ctx.globalAlpha = 1 - p;
      ctx.lineWidth = 3 * u;
      ctx.strokeStyle = ctx.fillStyle = look.color;
      ctx.beginPath();
      ctx.arc(px(x), py(y), (8 + 22 * ease(p)) * u, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(px(x), py(y), 5 * u, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    const pointer = lastAt(timeline.pointer, t);
    if (pointer) {
      ctx.save();
      ctx.translate(px(pointer[1]), py(pointer[2]));
      ctx.scale(u * 1.2, u * 1.2);
      ctx.lineJoin = "round";
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = "#fff";
      ctx.fillStyle = "#111";
      ctx.fill(ARROW);
      ctx.stroke(ARROW);
      ctx.restore();
    }

    const caption = timeline.captions.findLast((c) => shown(c, t));
    if (caption) {
      const size = Math.round(look.fontSize * u * 1.3);
      ctx.font = `600 ${size}px ${FAMILY}`;
      ctx.direction = direction(caption.text);
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const width = Math.min(ctx.measureText(caption.text).width + size * 1.6, canvas.width * 0.92);
      const height = Math.round(size * 2.2);
      const x = (canvas.width - width) / 2, y = canvas.height - height - 20 * u;
      ctx.fillStyle = "rgba(17, 17, 17, 0.86)";
      ctx.beginPath();
      ctx.roundRect(x, y, width, height, 6 * u);
      ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.fillText(caption.text, canvas.width / 2, y + height / 2 + 0.5 * u, width - size);
    }
  };

  const types = outputType === "mp4"
    ? ["video/mp4;codecs=avc1.640028", "video/mp4;codecs=avc1", "video/mp4"]
    : ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
  const mimeType = types.find((type) => MediaRecorder.isTypeSupported(type));
  if (!mimeType) throw new Error(`this browser cannot record ${outputType}; use .webm`);
  // Screen content compresses well; about 0.04 bit per pixel at 30 frames.
  const recorder = new MediaRecorder(canvas.captureStream(30), { mimeType, videoBitsPerSecond: Math.round(canvas.width * canvas.height * 30 * 0.04) });
  const chunks = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const stopped = new Promise((done) => (recorder.onstop = done));

  // Plays from the beginning (a browser's WebM recording cannot be sought
  // reliably) and records only from start to end.
  let playing = true, reachedEnd;
  const ended = new Promise((done) => {
    reachedEnd = done;
    video.onended = done;
  });
  const frame = () => {
    const t = video.currentTime;
    const view = viewAt(t);
    ctx.drawImage(video, view.x, view.y, view.width, view.height, 0, 0, canvas.width, canvas.height);
    drawMarks(t, view);
    if (recorder.state === "inactive" && t >= start) recorder.start(1000);
    if (end != null && t >= end) reachedEnd();
    if (playing) requestAnimationFrame(frame);
  };
  frame();
  await video.play();
  await ended;
  playing = false;
  video.pause();
  if (recorder.state === "inactive") throw new Error(`start (${start} s) is not before the end of the recording (${video.duration} s)`);
  await new Promise((done) => setTimeout(done, 100));
  recorder.stop();
  await stopped;
  const out = new Uint8Array(await new Blob(chunks, { type: mimeType }).arrayBuffer());
  let binary = "";
  for (let i = 0; i < out.length; i += 0x8000) binary += String.fromCharCode(...out.subarray(i, i + 0x8000));
  const duration = Math.min(end ?? Infinity, video.duration) - start;
  return JSON.stringify({ base64: btoa(binary), mimeType, duration, width: canvas.width, height: canvas.height });
}

// ── WebM duration ─────────────────────────────────────────────────────────

// A browser's WebM recording has no duration, so players cannot show its
// length or seek. Adds Duration (milliseconds at the default timecode scale)
// to the Segment's Info element.
function withDuration(webm, seconds) {
  const INFO = Buffer.from([0x15, 0x49, 0xa9, 0x66]);
  const at = webm.indexOf(INFO);
  if (at < 0 || !Number.isFinite(seconds)) return webm;
  const first = webm[at + 4];
  const sizeLength = Math.clz32(first) - 23;
  if (sizeLength < 1 || sizeLength > 8) return webm;
  let size = first & (0xff >> sizeLength);
  for (let i = 1; i < sizeLength; i++) size = size * 256 + webm[at + 4 + i];
  const bodyStart = at + 4 + sizeLength;
  const body = webm.subarray(bodyStart, bodyStart + size);
  const duration = Buffer.alloc(11);
  duration.set([0x44, 0x89, 0x88]);
  duration.writeDoubleBE(seconds * 1000, 3);
  const newSize = Buffer.alloc(8);
  newSize.writeBigUInt64BE(BigInt(size + duration.length) | (1n << 56n));
  return Buffer.concat([webm.subarray(0, at + 4), newSize, body, duration, webm.subarray(bodyStart + size)]);
}

// ── Main ──────────────────────────────────────────────────────────────────

const command = readCommand("annotate-video", { browser: "<path>" });
const spec = await readSpec(command.specPath);
const browser = await openBrowser(findBrowser(command.values.browser));
let result;
try {
  const { targetId } = await browser.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await browser.send("Target.attachToTarget", { targetId, flatten: true });
  const input = { videoBase64: spec.video.toString("base64"), outputType: spec.outputType, viewport: spec.viewport, timeline: spec.timeline, look: command.look, start: spec.start, end: spec.end };
  const reply = await browser.send("Runtime.evaluate", { expression: `(${renderInPage})(${JSON.stringify(input)})`, awaitPromise: true, returnByValue: true }, sessionId);
  if (reply.exceptionDetails) throw new Error(reply.exceptionDetails.exception?.description ?? reply.exceptionDetails.text);
  result = JSON.parse(reply.result.value);
} catch (error) {
  await browser.close();
  fail(error.message);
}
await browser.close();
let bytes = Buffer.from(result.base64, "base64");
if (spec.outputType === "webm") bytes = withDuration(bytes, result.duration);
await writeFile(spec.output, bytes);
console.log(JSON.stringify({ output: spec.output, type: result.mimeType, width: result.width, height: result.height, seconds: result.duration, bytes: bytes.length }, null, 2));
