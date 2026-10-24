#!/usr/bin/env node
/*
 * Print the roles & CRUD map as a PDF that any phone or computer can open.
 *
 * A downloaded .html shows as source code in many file viewers, and the n8n
 * .json is code by design (it is meant to be imported into n8n). A PDF is
 * drawn the same everywhere. This script opens the generated page
 * (docs/diagrams/n8n/reep-roles-features-crud.html) in headless Chromium,
 * opens every tab and every feature, adds a contents list and the n8n preview
 * renders, and prints it to A4.
 *
 *   node tools/diagrams/print_roles_crud_pdf.cjs [--fonts inline-fonts.css]
 *
 * Needs Playwright (the root package's @playwright/test, after `npm ci`) and a
 * Chromium it can launch; set CHROMIUM_PATH to use a browser already on the
 * machine. --fonts takes a stylesheet of @font-face rules with data: URIs, for
 * a machine where Google Fonts cannot be fetched; without it the page loads
 * them itself and falls back to system fonts if that fails.
 */
const fs = require('fs');
const path = require('path');

let chromium;
try { ({ chromium } = require('@playwright/test')); } catch (e) { ({ chromium } = require('playwright-core')); }

const ROOT = path.resolve(__dirname, '..', '..');
const DIR = path.join(ROOT, 'docs', 'diagrams', 'n8n');
const PAGE = path.join(DIR, 'reep-roles-features-crud.html');
const OUT = path.join(DIR, 'reep-roles-features-crud.pdf');

const PRINT_CSS = `
@page { size: A4; margin: 14mm 12mm 16mm; }
html, body { background: #ffffff !important; background-image: none !important; }
body { font-size: 12.5px; }
.wrap { max-width: none; padding: 0; }
.bar, .dl, .empty, .search { display: none !important; }
.panel { break-before: page; margin-top: 0; }
.feat { break-inside: avoid; box-shadow: none; }
.feat > summary { padding: 9px 12px; }
.fbody { padding: 8px 12px 12px; }
.area > h3 { break-after: avoid; }
.rolehead { break-after: avoid; }
.toc { margin: 18px 0 0; display: grid; gap: 4px; padding: 0; list-style: none; font-size: 0.9rem; }
.toc li { display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px dotted var(--line);
  padding: 4px 0; font-variant-numeric: tabular-nums; }
.shots figure { margin: 0; break-inside: avoid; display: grid; gap: 6px; }
.shots img { width: 100%; border: 1px solid var(--line); border-radius: 8px; }
.shots figcaption { color: var(--faint); font-size: 0.8rem; }
.legend { break-before: avoid; }
`;

(async () => {
  const fontsArg = process.argv.indexOf('--fonts');
  const fontsCss = fontsArg !== -1 ? fs.readFileSync(process.argv[fontsArg + 1], 'utf8') : '';
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  // Lay the page out at A4's printable width (210 mm less 2 x 12 mm margins, at 96 px/in), or Chromium
  // lays it out at the window width and shrinks the result onto the page.
  const page = await (await browser.newContext({ colorScheme: 'light', viewport: { width: 703, height: 1000 } }))
    .newPage();
  if (fontsCss) await page.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.abort());
  await page.goto('file://' + PAGE, { waitUntil: 'load' });
  await page.addStyleTag({ content: fontsCss + PRINT_CSS });
  await page.evaluate(() => {
    const panels = Array.from(document.querySelectorAll('.panel'));
    // Contents, after the header: each tab with its feature count.
    const toc = document.createElement('ol');
    toc.className = 'toc';
    panels.forEach((p) => {
      const li = document.createElement('li');
      const name = document.createElement('span');
      name.textContent = p.querySelector('h2').textContent;
      const n = document.createElement('span');
      const feats = p.querySelectorAll('.feat').length;
      n.textContent = feats ? feats + (p.dataset.slug === 'stack' ? ' table groups' : ' features') : '';
      li.append(name, n);
      toc.append(li);
    });
    document.querySelector('.wrap > header').append(toc);
    const lede = document.querySelector('.lede');
    if (lede) lede.textContent = lede.textContent.replace('Tap a feature to open it.', 'Every feature is shown open.');
    panels.forEach((p) => { p.hidden = false; });
    document.querySelectorAll('details').forEach((d) => { d.open = true; });
    // The n8n canvas itself, as rendered by n8n.
    const shots = document.createElement('section');
    shots.className = 'panel p-stack shots';
    shots.innerHTML = '<header class="rolehead"><h2>The n8n diagram</h2><p>The same map as rendered by n8n 2.41.6 '
      + 'from reep-roles-features-crud.n8n.json: the whole canvas, the request path and AWS estate, and the top '
      + 'of the Student lane. Import the .json into n8n (Workflows, Import from File) to explore it.</p></header>';
    [['preview-overview.png', 'The whole canvas, zoomed to fit: the request path and AWS estate on the left, '
      + 'one lane per role to the right.'],
     ['preview-request-path-and-aws.png', 'One request from phone to role decision, and the AWS services the '
      + 'API task talks to.'],
     ['preview-student-lane.png', 'The top of the Student lane: role, feature area, feature switch with its '
      + 'CREATE / READ / UPDATE / DELETE outputs, API call, FastAPI handler, tables and AWS services, and the '
      + 'feature card.']].forEach(([src, cap]) => {
      const f = document.createElement('figure');
      const img = document.createElement('img');
      img.src = src; img.alt = cap;
      const c = document.createElement('figcaption');
      c.textContent = cap;
      f.append(img, c);
      shots.append(f);
    });
    panels[panels.length - 1].after(shots);
  });
  await page.waitForFunction(() => Array.from(document.images).every((i) => i.complete));
  await page.evaluate(async () => {
    // A face added after first layout is only fetched once something asks for it.
    await Promise.all(['400 12px Inter', '500 12px Inter', '600 12px Inter', '600 12px "JetBrains Mono"',
      '650 12px "Plus Jakarta Sans"', '700 12px "Plus Jakarta Sans"', '800 12px "Plus Jakarta Sans"']
      .map((f) => document.fonts.load(f).catch(() => null)));
    await document.fonts.ready;
  });
  await page.pdf({
    path: OUT, format: 'A4', printBackground: true, displayHeaderFooter: true,
    headerTemplate: '<span></span>',
    footerTemplate: '<div style="font:9px system-ui,sans-serif;color:#67637c;width:100%;text-align:center">'
      + 'REEP roles &amp; CRUD map · <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
    margin: { top: '14mm', bottom: '16mm', left: '12mm', right: '12mm' },
  });
  await browser.close();
  console.log('wrote ' + path.relative(ROOT, OUT) + ' (' + Math.round(fs.statSync(OUT).size / 1024) + ' KB)');
})().catch((e) => { console.error(e); process.exit(1); });
