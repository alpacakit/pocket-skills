// Picks, from the fonts installed on this computer, a small set that has a
// glyph for every character of the labels, so labels in any script render
// without bundling fonts. Reads only each font's table directory, cmap,
// name, and OS/2 tables, not whole files.

import { readdirSync } from "node:fs";
import { open, readdir } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

function fontDirs() {
  const home = homedir();
  if (process.platform === "darwin") {
    // Fonts macOS downloads on demand (PingFang and others) live under AssetsV2.
    const assets = "/System/Library/AssetsV2";
    let downloaded = [];
    try {
      downloaded = readdirSync(assets).filter((name) => name.startsWith("com_apple_MobileAsset_Font")).map((name) => join(assets, name));
    } catch {
      // No downloaded fonts on this Mac.
    }
    return ["/System/Library/Fonts", "/Library/Fonts", join(home, "Library/Fonts"), ...downloaded];
  }
  if (process.platform === "win32") {
    const windows = process.env.WINDIR ?? "C:\\Windows";
    const local = process.env.LOCALAPPDATA ?? join(home, "AppData", "Local");
    return [join(windows, "Fonts"), join(local, "Microsoft", "Windows", "Fonts")];
  }
  return ["/usr/share/fonts", "/usr/local/share/fonts", join(home, ".local/share/fonts"), join(home, ".fonts")];
}

async function fontFiles(dir, found = []) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch {
    return found; // A missing or unreadable font folder only means fewer candidates.
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) await fontFiles(path, found);
    else if (/\.(ttf|otf|ttc|otc)$/i.test(entry.name)) found.push(path);
  }
  return found;
}

async function read(file, offset, length) {
  const buffer = Buffer.alloc(length);
  const { bytesRead } = await file.read(buffer, 0, length, offset);
  return buffer.subarray(0, bytesRead);
}

async function tables(file, offset) {
  const head = await read(file, offset, 12);
  const count = head.readUInt16BE(4);
  const dir = await read(file, offset + 12, count * 16);
  const found = {};
  for (let i = 0; i < count; i++) {
    const at = i * 16;
    found[dir.toString("latin1", at, at + 4)] = { offset: dir.readUInt32BE(at + 8), length: dir.readUInt32BE(at + 12) };
  }
  return found;
}

// Code point ranges from the best Unicode subtable (format 12, else 4).
function cmapRanges(cmap) {
  const count = cmap.readUInt16BE(2);
  let best = null;
  for (let i = 0; i < count; i++) {
    const platform = cmap.readUInt16BE(4 + i * 8);
    const encoding = cmap.readUInt16BE(6 + i * 8);
    const offset = cmap.readUInt32BE(8 + i * 8);
    if (offset + 2 > cmap.length) continue;
    const format = cmap.readUInt16BE(offset);
    const unicode = platform === 0 || (platform === 3 && (encoding === 1 || encoding === 10));
    if (!unicode || (format !== 4 && format !== 12)) continue;
    if (!best || format > best.format) best = { format, offset };
  }
  if (!best) return [];
  const ranges = [];
  if (best.format === 12) {
    const groups = cmap.readUInt32BE(best.offset + 12);
    for (let g = 0; g < groups; g++) {
      const at = best.offset + 16 + g * 12;
      if (at + 12 > cmap.length) break;
      ranges.push([cmap.readUInt32BE(at), cmap.readUInt32BE(at + 4)]);
    }
  } else {
    const segments = cmap.readUInt16BE(best.offset + 6) / 2;
    const ends = best.offset + 14;
    const starts = ends + segments * 2 + 2;
    for (let s = 0; s < segments; s++) {
      const end = cmap.readUInt16BE(ends + s * 2);
      const start = cmap.readUInt16BE(starts + s * 2);
      if (start !== 0xffff) ranges.push([start, end]);
    }
  }
  return ranges;
}

// The family (typographic family when present), English first.
function familyName(name) {
  const count = name.readUInt16BE(2);
  const strings = name.readUInt16BE(4);
  let best = null;
  for (let i = 0; i < count; i++) {
    const at = 6 + i * 12;
    const [platform, encoding, language, id, length, offset] = [0, 2, 4, 6, 8, 10].map((o) => name.readUInt16BE(at + o));
    if (id !== 1 && id !== 16) continue;
    const utf16 = platform === 0 || (platform === 3 && (encoding === 1 || encoding === 10));
    const roman = platform === 1 && encoding === 0 && language === 0;
    if (!utf16 && !roman) continue;
    const score = (id === 16 ? 4 : 0) + (utf16 ? 2 : 0) + (language === 0x409 || platform === 0 || roman ? 1 : 0);
    if (best && best.score >= score) continue;
    const bytes = Buffer.from(name.subarray(strings + offset, strings + offset + length));
    if (bytes.length !== length) continue;
    best = { score, text: utf16 ? bytes.swap16().toString("utf16le") : bytes.toString("latin1") };
  }
  return best?.text ?? null;
}

async function faces(path) {
  const file = await open(path, "r");
  try {
    const head = await read(file, 0, 12);
    const offsets = [];
    if (head.toString("latin1", 0, 4) === "ttcf") {
      const count = head.readUInt32BE(8);
      const list = await read(file, 12, count * 4);
      for (let i = 0; i < count; i++) offsets.push(list.readUInt32BE(i * 4));
    } else offsets.push(0);
    const result = [];
    for (const offset of offsets) {
      const t = await tables(file, offset);
      // The renderer cannot draw sbix bitmaps (Apple Color Emoji); skip such fonts.
      if (!t.cmap || !t.name || t.sbix) continue;
      const family = familyName(await read(file, t.name.offset, t.name.length));
      if (!family) continue;
      const ranges = cmapRanges(await read(file, t.cmap.offset, t.cmap.length));
      const os2 = t["OS/2"] ? await read(file, t["OS/2"].offset, 64) : null;
      const weight = os2 ? os2.readUInt16BE(4) : 400;
      const italic = os2 && os2.length >= 64 ? (os2.readUInt16BE(62) & 1) === 1 : false;
      result.push({ path, family, weight, italic, layout: Boolean(t.GSUB), ranges });
    }
    return result;
  } finally {
    await file.close();
  }
}

function covers(face, cp) {
  let lo = 0;
  let hi = face.ranges.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const [start, end] = face.ranges[mid];
    if (cp < start) hi = mid - 1;
    else if (cp > end) lo = mid + 1;
    else return true;
  }
  return false;
}

// Families tried first, so labels look like the system's UI text. Han
// characters take the forms of the label's language.
const PREFERRED = {
  base: ["SF Pro", "Helvetica Neue", "Segoe UI", "Noto Sans", "Inter", "DejaVu Sans", "Arial"],
  ja: ["Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic UI", "Yu Gothic", "Meiryo", "Noto Sans CJK JP", "Noto Sans JP"],
  "zh-Hans": ["PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC", "Heiti SC"],
  "zh-Hant": ["PingFang TC", "Microsoft JhengHei", "Noto Sans CJK TC", "Noto Sans TC", "Heiti TC"],
  ko: ["Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans CJK KR", "Noto Sans KR"],
  emoji: ["Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji"],
};
// Serif faces come after sans ones among the families not listed above.
const SERIF = /serif|song|ming|mincho|kai|batang|myungjo|times|georgia/i;
// Fonts that claim every character but draw placeholders.
const SKIP = /^(LastResort|Adobe Blank)/i;

// The writing system of a letter, with Chinese characters, kana, and Hangul
// as one (they mix within one phrase); null for spaces, digits, punctuation,
// symbols, and marks, which join the segment they are in.
const SCRIPTS = ["Latin", "Cyrillic", "Greek", "Arabic", "Hebrew", "Devanagari", "Bengali", "Tamil", "Thai", "Lao", "Khmer", "Myanmar", "Georgian", "Armenian", "Ethiopic"].map((name) => [name, new RegExp(`\\p{Script=${name}}`, "u")]);
const CJK = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}\p{Script=Bopomofo}\u30fc]/u;
function scriptOf(char) {
  if (CJK.test(char)) return "CJK";
  if (!/\p{L}/u.test(char)) return null;
  return SCRIPTS.find(([, test]) => test.test(char))?.[0] ?? "other";
}

function hanGroup(lang) {
  const tag = lang.toLowerCase();
  if (tag.startsWith("ja")) return "ja";
  if (tag.startsWith("ko")) return "ko";
  if (/^zh[-_](tw|hk|mo|hant)/.test(tag)) return "zh-Hant";
  if (tag.startsWith("zh")) return "zh-Hans";
  return null;
}

// Every usable face of every installed font file.
async function installedFaces() {
  const files = (await Promise.all(fontDirs().map((dir) => fontFiles(dir)))).flat();
  const all = await Promise.all(files.map((path) => faces(path).catch(() => []))); // an unreadable font is just not a candidate
  return all.flat().filter((f) => !f.italic && !SKIP.test(f.family));
}

// Faces in the order to try them: preferred families, then OpenType layout
// (GSUB) before Apple's AAT (the renderer shapes only with OpenType), then the
// weight closest to the labels' bold.
function byPreference(lang, weight) {
  const order = [...PREFERRED.base, ...(PREFERRED[hanGroup(lang)] ?? []), ...PREFERRED.ja, ...PREFERRED["zh-Hans"], ...PREFERRED.ko, ...PREFERRED.emoji];
  const rank = (family) => {
    const i = order.indexOf(family);
    return i >= 0 ? i : order.length + (SERIF.test(family) ? 1 : 0);
  };
  return (a, b) => rank(a.family) - rank(b.family) || Number(b.layout) - Number(a.layout) || Math.abs(a.weight - weight) - Math.abs(b.weight - weight);
}

// Cuts a label into runs of one font each. The label is first cut into script
// segments (a word or phrase of one writing system, with the spaces,
// punctuation, and marks inside it); each segment takes the first picked face
// that has all of it, so a phrase is never drawn in a mix of fonts.
function runsWith(picked) {
  return (line) => {
    const segments = [];
    for (const char of line) {
      const kind = scriptOf(char);
      const current = segments.at(-1);
      if (current && (kind === null || current.kind === kind)) current.text += char;
      else segments.push({ kind, text: char });
    }
    const out = [];
    const push = (face, text) => {
      if (out.length && out.at(-1).face === face) out.at(-1).text += text;
      else out.push({ face, text });
    };
    for (const { text } of segments) {
      const cps = [...text].map((c) => c.codePointAt(0));
      const whole = picked.find((p) => cps.every((cp) => cp <= 0x20 || covers(p, cp)));
      if (whole) push(whole, text);
      else for (const cp of cps) push(picked.find((p) => covers(p, cp)) ?? null, String.fromCodePoint(cp));
    }
    return out.map(({ face, text }) => ({ family: face?.family ?? "sans-serif", text }));
  };
}

/**
 * The fewest installed fonts that together have every character of `text`:
 * their files to load, their families, the characters none has, and `runs`,
 * which cuts a label into { family, text } runs.
 */
export async function pickFonts(text, { lang = Intl.DateTimeFormat().resolvedOptions().locale, weight = 700 } = {}) {
  const needed = new Set([...text].map((c) => c.codePointAt(0)).filter((cp) => cp > 0x20));
  const candidates = (await installedFaces()).sort(byPreference(lang, weight));
  const picked = [];
  for (const face of candidates) {
    if (needed.size === 0) break;
    const hits = [...needed].filter((cp) => covers(face, cp));
    if (hits.length === 0) continue;
    hits.forEach((cp) => needed.delete(cp));
    picked.push(face);
  }
  return {
    files: [...new Set(picked.map((p) => p.path))],
    families: [...new Set(picked.map((p) => p.family))],
    missing: [...needed].map((cp) => String.fromCodePoint(cp)),
    runs: runsWith(picked),
  };
}
