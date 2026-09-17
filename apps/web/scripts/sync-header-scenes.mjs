import { copyFileSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "../..");
const sourceDir = resolve(repoRoot, "docs/06-ui/assets/packages/header-scenes-v1");
const targetDir = resolve(webRoot, "public/assets/ui/header-scenes-v1");

const expectedDimensions = {
  desk: [1600, 400],
  mob: [800, 500],
};

function readPngMetadata(path) {
  const buffer = readFileSync(path);
  if (buffer.length < 26 || !buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) {
    throw new Error(`Invalid PNG: ${path}`);
  }
  if (buffer.readUInt32BE(12) !== 0x49484452) throw new Error(`Missing PNG IHDR: ${path}`);
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
    colorType: buffer[25],
  };
}

function parseAsset(name) {
  const match = /^(desk|mob)-([a-z0-9]+)-(\d{2})\.png$/.exec(name);
  if (!match) return null;
  return { viewport: match[1], theme: match[2], sequence: Number(match[3]) };
}

const files = readdirSync(sourceDir);
const assets = files.map(parseAsset).filter(Boolean);
if (assets.length === 0) throw new Error(`No Header scene assets found in ${sourceDir}`);

mkdirSync(targetDir, { recursive: true });

for (const asset of assets) {
  const name = `${asset.viewport}-${asset.theme}-${String(asset.sequence).padStart(2, "0")}.png`;
  const source = resolve(sourceDir, name);
  const metadata = readPngMetadata(source);
  const [width, height] = expectedDimensions[asset.viewport];
  if (metadata.width !== width || metadata.height !== height || metadata.colorType !== 6) {
    throw new Error(`Header scene must be RGBA ${width}x${height}: ${name}`);
  }
  copyFileSync(source, resolve(targetDir, name));
}

console.log(`Synced ${assets.length} Header scene PNG assets to ${targetDir}`);
