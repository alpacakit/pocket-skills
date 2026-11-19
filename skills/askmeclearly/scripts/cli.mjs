// The command line shared by annotate.mjs (screenshots) and annotate-video.mjs
// (recordings): one spec file and the same look options with the same
// defaults, so pictures and recordings carry the same marks.

import { parseArgs } from "node:util";

export function failWith(name) {
  return (message) => {
    console.error(`${name}: ${message}`);
    process.exit(1);
  };
}

// extra: more string options, as { name: "placeholder for the usage line" }.
export function readCommand(name, extra = {}) {
  const fail = failWith(name);
  const usage =
    `usage: node ${name}.mjs spec.json [--color #e0242a] [--font-size 13] [--stroke 3]` +
    Object.entries(extra).map(([key, placeholder]) => ` [--${key} ${placeholder}]`).join("");
  const options = { color: { type: "string" }, "font-size": { type: "string" }, stroke: { type: "string" } };
  for (const key of Object.keys(extra)) options[key] = { type: "string" };
  let parsed;
  try {
    parsed = parseArgs({ allowPositionals: true, options });
  } catch (error) {
    fail(error.message);
  }
  const { values, positionals } = parsed;
  if (positionals.length !== 1) fail(usage);
  const positive = (key, value) => {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) fail(`--${key} must be a positive number, not ${value}`);
    return number;
  };
  return {
    specPath: positionals[0],
    values,
    look: {
      color: values.color ?? "#e0242a",
      fontSize: positive("font-size", values["font-size"] ?? 13),
      stroke: positive("stroke", values.stroke ?? 3),
    },
  };
}
