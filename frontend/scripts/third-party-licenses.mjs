// Writes dist/THIRD_PARTY_LICENSES.txt: for every production package of package-lock.json that is installed in node_modules, its name,
// version, licence field and the full text of its licence files. It runs after `vite build` (the `postbuild` script of package.json), so
// the minified bundle that ships in the wheel and the sdist carries the notices of the code it contains.
//
// A production package that is not installed, that has no licence file and no licence field, or that cannot be read fails the build:
// a missing notice is never written as an empty one. Optional packages of the lock file that this machine did not install are skipped
// (they are platform builds that are not part of the bundle).
//
//   node scripts/third-party-licenses.mjs [--lock FILE] [--modules DIR] [--out FILE]
//
// Node built-ins only.
import { existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");

function option(name, fallback) {
  const at = process.argv.indexOf(name);
  return at >= 0 && process.argv[at + 1] ? resolve(process.argv[at + 1]) : fallback;
}

const lockFile = option("--lock", join(root, "package-lock.json"));
const modulesDir = option("--modules", join(root, "node_modules"));
const outFile = option("--out", join(root, "dist", "THIRD_PARTY_LICENSES.txt"));

const LICENCE_FILE = /^(licen[cs]e|copying|notice)([-._].*)?$/i;

function fail(message) {
  console.error(`third-party-licenses: ${message}`);
  process.exit(1);
}

function licenceField(manifest, lockEntry) {
  const field = manifest.license ?? lockEntry.license;
  if (typeof field === "string" && field.trim()) return field.trim();
  if (field && typeof field === "object" && typeof field.type === "string" && field.type.trim()) return field.type.trim();
  if (Array.isArray(manifest.licenses)) {
    const types = manifest.licenses.map((l) => (typeof l === "string" ? l : l && l.type)).filter(Boolean);
    if (types.length) return types.join(" OR ");
  }
  return "";
}

function licenceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && LICENCE_FILE.test(entry.name))
    .map((entry) => entry.name)
    .sort((a, b) => a.localeCompare(b, "en"));
}

let lock;
try {
  lock = JSON.parse(readFileSync(lockFile, "utf8"));
} catch (error) {
  fail(`cannot read ${lockFile}: ${error.message}`);
}
if (!lock.packages || typeof lock.packages !== "object") fail(`${lockFile} has no packages table (lockfileVersion 2 or 3 is needed)`);

const prefix = "node_modules/";
const entries = Object.entries(lock.packages)
  .filter(([key, value]) => key !== "" && !value.dev && !value.link)
  .sort(([a], [b]) => a.localeCompare(b, "en"));
if (entries.length === 0) fail("the lock file lists no production package");

const sections = [];
const problems = [];
let skipped = 0;
for (const [key, lockEntry] of entries) {
  const rel = key;
  const dir = join(modulesDir, key.startsWith(prefix) ? key.slice(prefix.length) : key);
  if (!existsSync(dir) || !lstatSync(dir).isDirectory()) {
    if (lockEntry.optional) {
      skipped += 1;
    } else {
      problems.push(`${rel}: listed in the lock file but not installed (run npm ci)`);
    }
    continue;
  }
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
  } catch (error) {
    problems.push(`${rel}: cannot read its package.json: ${error.message}`);
    continue;
  }
  const name = manifest.name ?? key.slice(key.lastIndexOf(prefix) + prefix.length);
  const version = manifest.version ?? lockEntry.version ?? "unknown";
  const licence = licenceField(manifest, lockEntry);
  const names = licenceFiles(dir);
  if (names.length === 0 && !licence) {
    problems.push(`${rel}: no licence file and no licence field`);
    continue;
  }
  const lines = [`${name} ${version}`, `Licence: ${licence || "not stated in package.json (see the text below)"}`];
  const author = typeof manifest.author === "string" ? manifest.author : manifest.author && manifest.author.name;
  if (author && names.length === 0) lines.push(`Author: ${author.replace(/\s*<[^>]*>/g, "").replace(/\s*\([^)]*\)/g, "").trim()}`);
  if (names.length === 0) {
    lines.push("", "(The package ships no licence file; the licence field above is all it states.)");
  }
  for (const file of names) {
    let text;
    try {
      text = readFileSync(join(dir, file), "utf8").replace(/\r\n?/g, "\n").trimEnd();
    } catch (error) {
      problems.push(`${rel}: cannot read ${file}: ${error.message}`);
      continue;
    }
    lines.push("", `--- ${file} ---`, "", text);
  }
  sections.push(lines.join("\n"));
}

if (problems.length) fail(`${problems.length} problem(s):\n  ${problems.join("\n  ")}`);

const rule = "=".repeat(78);
const header = [
  "Third-party notices for the Reachmap workspace",
  "",
  "The built workspace in this folder bundles code from the packages below, each under its own licence.",
  "This list is generated from frontend/package-lock.json (production packages only) and the licence",
  "files that each installed package ships.",
  "",
  `Packages: ${sections.length}`,
].join("\n");
const body = [header, ...sections].join(`\n\n${rule}\n\n`) + "\n";

mkdirSync(dirname(outFile), { recursive: true });
writeFileSync(outFile, body, "utf8");
console.log(`third-party-licenses: ${sections.length} packages${skipped ? `, ${skipped} optional not installed` : ""}, wrote ${outFile}`);
