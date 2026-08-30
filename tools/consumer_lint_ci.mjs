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
//   fail   CANDIDATE a generic-looking rule with no recorded decision
//
// That last one is the loop's ratchet. Every enhancement that adds a rule with
// no page identity and only semantic tokens has to answer one question before
// it merges: does this belong upstream? Answer it either way — promote it, or
// record a verdict in tools/promotion-verdicts.json saying why not and what
// would reopen it. What you cannot do is not decide, because that is how a
// design system quietly forks into a pile of page CSS nobody folds back.
//
// The bar is deliberately low (a five-line JSON entry) and the question is
// asked once: a recorded selector never blocks again, and deleting its entry
// puts it back on the agenda. The weekly digest catches anything that drifts
// open afterwards.
//
// It also warns when the pinned release is behind the newest design-system tag.
// A stale pin is never a build failure: it is not a correctness bug, and failing
// here would block PRs that have nothing to do with the design system.
//
// It also fails if the pages disagree about WHICH design-system ref they pin.
// CI checks out the design system at that ref before running this, so the
// pages are always linted against the sheet they actually load.
import { readFileSync, appendFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const [dsRoot, ...pages] = process.argv.slice(2);
if (!dsRoot || !pages.length) {
  console.error('usage: node tools/consumer_lint_ci.mjs <design-system-checkout> <page.html> [...]');
  process.exit(2);
}

const { analyse } = await import(pathToFileURL(resolve(dsRoot, 'build/consumer-lint.mjs')).href);

// --- decided candidates ----------------------------------------------------
const DECIDED = new Map();   // selector -> { key, verdict, why }
try {
  const led = JSON.parse(readFileSync(new URL('./promotion-verdicts.json', import.meta.url), 'utf8'));
  for (const [key, v] of Object.entries(led.verdicts || {}))
    for (const sel of v.selectors || []) DECIDED.set(sel, { key, verdict: v.verdict, why: v.why });
} catch (e) {
  console.log(`  note  no promotion-verdicts.json (${e.code || 'unreadable'}) — every candidate counts as open`);
}

// --- every page must pin the same design-system ref ------------------------
const refs = new Set();
const bodies = new Map();
for (const p of pages) {
  const html = readFileSync(p, 'utf8');
  bodies.set(p, html);
  for (const m of html.matchAll(/design-system@([^/"'\s]+)\//g)) refs.add(m[1]);
}
let hard = 0;
const openCandidates = [];
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
  const cand = [...new Set(promote)];
  const open = cand.filter((sel) => !DECIDED.has(sel));
  const decided = cand.filter((sel) => DECIDED.has(sel));
  openCandidates.push(...open.map((sel) => ({ page: name, sel })));
  if (decided.length) {
    const byVerdict = {};
    for (const sel of decided) (byVerdict[DECIDED.get(sel).verdict] ||= []).push(sel);
    console.log(`\n  note  ${name} — ${decided.length} candidate(s) already decided: `
      + Object.entries(byVerdict).map(([v, l]) => `${l.length} ${v}`).join(', '));
  }
  if (open.length) {
    hard += open.length;
    console.log(`\n  FAIL  CANDIDATE  ${name} — ${open.length} undecided`);
    console.log('        Generic-looking rules with no recorded decision. The test is not');
    console.log('        "could another project use it" — almost anything passes that — but');
    console.log('        "would another project be WRONG to write it differently".');
    for (const sel of open) console.log('        - ' + sel);
    console.log('        Resolve by promoting them upstream, or add to tools/promotion-verdicts.json:');
    console.log(`          "${open[0].replace(/^\./, '').replace(/[^a-z0-9-]/gi, '-')}": {`);
    console.log(`            "selectors": [${open.map((s2) => JSON.stringify(s2)).join(', ')}],`);
    console.log(`            "page": "${name}", "verdict": "not promoted",`);
    console.log('            "why": "…", "reopen_when": "…" }');
  }
  if (!squat.length && !unknown.length && !hygiene.length)
    console.log(`  ok    ${name} — no squats, no unknown classes, no raw colour`);
}

// --- is the pinned release still the newest one? ---------------------------
// Warn only. Both previous pin bumps happened because a person noticed; this is
// the check that notices instead. The design system is public, so listing its
// tags needs no credential.
const pinned = [...refs][0];
if (pinned && /^v\d+\.\d+\.\d+$/.test(pinned)) {
  try {
    const out = execFileSync('git', ['ls-remote', '--tags', '--refs',
      'https://github.com/spicyChicken59/design-system'], { encoding: 'utf8', timeout: 20000 });
    const key = (t) => t.slice(1).split('.').map(Number);
    const newer = (a, b) => {           // a > b ?
      const [x, y] = [key(a), key(b)];
      for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i];
      return false;
    };
    const tags = [...out.matchAll(/refs\/tags\/(v\d+\.\d+\.\d+)$/gm)].map((m) => m[1]);
    const latest = tags.reduce((a, t) => (!a || newer(t, a) ? t : a), null);
    if (latest && newer(latest, pinned)) {
      console.log(`\n  note  the pages pin ${pinned}, but ${latest} is released.`);
      console.log('        Bump every pin in ONE commit (this linter fails a split pin),');
      console.log('        and delete any bridge whose comment says the new release carries it.');
    } else if (latest) {
      console.log(`\n  ok    pin ${pinned} is the newest design-system release`);
    }
  } catch (e) {
    console.log(`  note  could not list design-system tags (${e.code || 'offline'}) — pin freshness unchecked`);
  }
}

if (openCandidates.length) {
  console.log(`\n  ${openCandidates.length} undecided promotion candidate(s) across all pages.`);
  console.log('  Every one is a question the design system is owed an answer to.');
}
console.log(hard ? `\nconsumer-lint policy: ${hard} blocking finding(s)` : '\nconsumer-lint policy: clean');
if (process.env.GITHUB_OUTPUT) {   // the weekly digest reads these
  const kv = `open_candidates=${openCandidates.length}\n`
    + `open_list=${openCandidates.map((c) => c.page + ' ' + c.sel).join('; ')}\n`;
  try { appendFileSync(process.env.GITHUB_OUTPUT, kv); } catch (e) { /* best effort */ }
}
process.exit(hard ? 1 : 0);
