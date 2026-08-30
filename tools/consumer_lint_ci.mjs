// CI policy for the design system's consumer linter.
//
//   node tools/consumer_lint_ci.mjs <design-system-checkout> <page.html> [...]
//
// The linter itself (design-system/build/consumer-lint.mjs) warns and never
// blocks — a consumer is a conversation, not a build step. The BLOCKING
// policy is this consumer's own, and it draws the line where the findings
// stop being judgement calls:
//
//   fail   SQUAT     a name squatted in the sc- namespace: it will collide
//   fail   UNKNOWN   markup using an sc- class the sheet does not define
//   fail   HYGIENE   raw colour or a primitive where a token belongs
//   warn   OVERRIDE  sometimes right — but each one deserves eyes, not a gate
//   info   CANDIDATE the loop's raw material: rules worth promoting upstream
//
// It also fails if the pages disagree about WHICH design-system ref they pin.
// CI checks out the design system at that ref before running this, so the
// pages are always linted against the sheet they actually load.
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const [dsRoot, ...pages] = process.argv.slice(2);
if (!dsRoot || !pages.length) {
  console.error('usage: node tools/consumer_lint_ci.mjs <design-system-checkout> <page.html> [...]');
  process.exit(2);
}

const { analyse } = await import(pathToFileURL(resolve(dsRoot, 'build/consumer-lint.mjs')).href);

// --- every page must pin the same design-system ref ------------------------
const refs = new Set();
const bodies = new Map();
for (const p of pages) {
  const html = readFileSync(p, 'utf8');
  bodies.set(p, html);
  for (const m of html.matchAll(/design-system@([^/"'\s]+)\//g)) refs.add(m[1]);
}
let hard = 0;
if (refs.size > 1) {
  console.error(`FAIL  the pages pin different design-system refs: ${[...refs].join(' vs ')}`);
  console.error('      (updated one page and forgot the other — they must move together)');
  hard++;
} else {
  console.log(`pages pin design-system@${[...refs][0] ?? '(no pin found)'}`);
}

// --- lint each page against the checked-out sheet --------------------------
const show = (name, label, list, blocking) => {
  const uniq = [...new Set(list)];
  if (!uniq.length) return 0;
  console.log(`\n  ${blocking ? 'FAIL' : 'note'}  ${label}  ${name} — ${uniq.length}`);
  for (const l of uniq.slice(0, 20)) console.log('        - ' + l);
  if (uniq.length > 20) console.log(`        … and ${uniq.length - 20} more`);
  return blocking ? uniq.length : 0;
};

for (const p of pages) {
  const { squat, unknown, override, hygiene, promote } = analyse(bodies.get(p));
  const name = p.split('/').pop();
  hard += show(name, 'SQUAT   ', squat, true);
  hard += show(name, 'UNKNOWN ', unknown, true);
  hard += show(name, 'HYGIENE ', hygiene, true);
  show(name, 'OVERRIDE', override, false);
  if (promote.length) console.log(`\n  info  ${name} — ${new Set(promote).size} promotion candidate(s): rules with no page identity, tokens only. Worth a look next release.`);
  if (!squat.length && !unknown.length && !hygiene.length)
    console.log(`  ok    ${name} — no squats, no unknown classes, no raw colour`);
}

console.log(hard ? `\nconsumer-lint policy: ${hard} blocking finding(s)` : '\nconsumer-lint policy: clean');
process.exit(hard ? 1 : 0);
