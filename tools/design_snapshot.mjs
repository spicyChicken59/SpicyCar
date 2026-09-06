// Verify the atomic design-system snapshot before using its upstream commit
// for linting. This checks display assets only; it never touches tracker data.
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';

export const snapshotRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../docs/design-system');

export function verifySnapshot() {
  const manifest = JSON.parse(readFileSync(join(snapshotRoot, 'provenance.json'), 'utf8'));
  if (manifest.repository !== 'spicyChicken59/design-system' || !/^[a-f0-9]{40}$/.test(manifest.commit))
    throw new Error('The design snapshot must name its source repository and exact commit.');
  if (!/^\d+\.\d+\.\d+$/.test(manifest.version) || !manifest.files || !Object.keys(manifest.files).length)
    throw new Error('The design snapshot needs a version and file digests.');
  for (const name of ['sc.css', 'sc-theme.js', 'sc-motion.js', 'sc-charts.js', 'sc-map.js'])
    if (!manifest.files[name]) throw new Error(`The design snapshot is missing ${name}.`);
  for (const [name, digest] of Object.entries(manifest.files)) {
    const file = resolve(snapshotRoot, name), rel = relative(snapshotRoot, file);
    if (isAbsolute(name) || rel.startsWith('..') || !/^[a-f0-9]{64}$/.test(digest))
      throw new Error(`Invalid design snapshot entry: ${name}`);
    const actual = createHash('sha256').update(readFileSync(file)).digest('hex');
    if (actual !== digest) throw new Error(`Design asset changed outside the atomic snapshot: ${name}`);
  }
  const visit = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const file = join(dir, entry.name);
      if (entry.isDirectory()) visit(file);
      else {
        const name = relative(snapshotRoot, file).split('\\').join('/');
        if (name !== 'provenance.json' && !manifest.files[name])
          throw new Error(`Design snapshot contains an unrecorded file: ${name}`);
      }
    }
  };
  visit(snapshotRoot);
  return manifest;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const manifest = verifySnapshot();
    console.log(process.argv.includes('--commit') ? manifest.commit
      : `Design snapshot ${manifest.version}: ${Object.keys(manifest.files).length} verified assets from ${manifest.commit}.`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
