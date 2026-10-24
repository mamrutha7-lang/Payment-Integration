#!/usr/bin/env node
/*
 * Photograph regions of the n8n workflow canvas, as n8n itself draws it.
 *
 * The browser half of tools/diagrams/render_n8n_canvas.py, which plans the
 * regions and assembles what this writes. It signs in to a running n8n, opens
 * the workflow, sets the zoom to 100%, and for every job pans the canvas so the
 * job's region sits in the canvas's top-left corner. Then it writes:
 *
 *   <out>/<name>.png         a screenshot of exactly that region
 *   <out>/<name>.raw.pdf     (pdf jobs) the whole window printed as vector PDF
 *   <out>/<name>.crop.json   (pdf jobs) where the region sits on that page
 *
 *   N8N_EMAIL=... N8N_PASSWORD=... node tools/diagrams/render_n8n_canvas.cjs \
 *     --url http://127.0.0.1:5678 (--workflow-id ID | --import workflow.json) \
 *     --jobs jobs.json --out DIR
 *
 * The canvas is panned with wheel events on the pane, which is what n8n listens
 * to, rather than by writing the transform: n8n redraws the transform from its
 * own state, so a transform written from outside is undone on the next redraw.
 * At 100% one wheel pixel pans half a canvas unit, and the loop below checks
 * the transform after every step rather than trusting that ratio.
 */
const fs = require('fs');
const path = require('path');

let chromium;
try { ({ chromium } = require('@playwright/test')); } catch (e) { ({ chromium } = require('playwright-core')); }

function arg(name, fallback) {
  const i = process.argv.indexOf('--' + name);
  return i === -1 ? fallback : process.argv[i + 1];
}

const BASE = arg('url', 'http://127.0.0.1:5678');
const JOBS = JSON.parse(fs.readFileSync(arg('jobs'), 'utf8'));
const OUT = arg('out');

// Everything but the canvas is hidden, so the printed page is the canvas alone:
// no sidebar, header, zoom buttons, execute button or logs bar. The dotted grid
// is hidden too, which leaves the canvas one flat colour.
const CSS = `
  .vue-flow__background, .vue-flow__panel, .vue-flow__minimap { display: none !important; }
  body * { visibility: hidden !important; }
  .vue-flow, .vue-flow * { visibility: visible !important; }
  .vue-flow__panel, .vue-flow__panel * { visibility: hidden !important; }
`;

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const START = { width: 1600, height: 1200 };
  const page = await (await browser.newContext({ viewport: START, deviceScaleFactor: 1 })).newPage();
  page.setDefaultTimeout(300000);

  await page.goto(BASE + '/signin', { waitUntil: 'networkidle' });
  const browserId = await page.evaluate(() => localStorage.getItem('n8n-browserId'));
  const headers = { 'browser-id': browserId || 'render', 'content-type': 'application/json' };
  let r = await page.request.post(BASE + '/rest/login', { headers,
    data: { emailOrLdapLoginId: process.env.N8N_EMAIL, password: process.env.N8N_PASSWORD } });
  if (r.status() !== 200) throw new Error(`n8n sign-in answered ${r.status()}: set N8N_EMAIL and N8N_PASSWORD`);

  let id = arg('workflow-id');
  if (!id) {
    const wf = JSON.parse(fs.readFileSync(arg('import'), 'utf8'));
    r = await page.request.post(BASE + '/rest/workflows', { headers,
      data: { name: wf.name, nodes: wf.nodes, connections: wf.connections, settings: wf.settings || {}, pinData: {}, active: false } });
    if (r.status() !== 200) throw new Error(`import answered ${r.status()}: ${(await r.text()).slice(0, 300)}`);
    id = (await r.json()).data.id;
    console.log('imported as workflow ' + id);
  }

  await page.goto(`${BASE}/workflow/${id}`, { waitUntil: 'networkidle' });
  await page.waitForSelector('.vue-flow__node');
  await page.waitForTimeout(8000); // n8n fits the view once every node has measured itself
  await page.keyboard.press('Escape');
  await page.mouse.move(START.width / 2, START.height / 2);
  await page.keyboard.press('0'); // n8n's shortcut for 100%
  await page.waitForTimeout(1500);
  await page.mouse.move(2, 2); // off the canvas, so no node draws its hover toolbar
  await page.addStyleTag({ content: CSS });
  await page.emulateMedia({ media: 'screen' });

  const state = () => page.evaluate(() => {
    const t = document.querySelector('.vue-flow__transformationpane').style.transform;
    const m = /translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+)\)/.exec(t);
    const b = document.querySelector('.vue-flow').getBoundingClientRect();
    return { tx: +m[1], ty: +m[2], k: +m[3], cx: b.x, cy: b.y, cw: b.width, ch: b.height };
  });
  let s = await state();
  if (s.k !== 1) throw new Error('the canvas did not go to 100%: zoom ' + s.k);
  const below = START.height - (s.cy + s.ch); // the logs bar under the canvas

  for (const job of JOBS) {
    const W = Math.ceil(job.w + s.cx), H = Math.ceil(job.h + s.cy + below);
    await page.setViewportSize({ width: W, height: H });
    await page.waitForTimeout(300);
    if (job.fit) {
      // The whole canvas, fitted to the window by n8n itself ("1"); the transform is
      // written beside it so the planner can draw each page's box on it.
      await page.mouse.move(s.cx + job.w / 2, s.cy + job.h / 2);
      await page.keyboard.press('1');
      await page.waitForTimeout(2500);
      await page.mouse.move(2, 2);
      s = await state();
      await page.screenshot({ path: path.join(OUT, job.name + '.png'), clip: { x: s.cx, y: s.cy, width: job.w, height: job.h } });
      fs.writeFileSync(path.join(OUT, job.name + '.json'), JSON.stringify({ tx: s.tx, ty: s.ty, k: s.k }));
      console.log('rendered ' + job.name);
      continue;
    }
    for (let step = 0; step < 10; step++) {
      s = await state();
      const dx = 2 * (s.tx + job.x), dy = 2 * (s.ty + job.y);
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) break;
      await page.evaluate(([deltaX, deltaY]) => {
        document.querySelector('.vue-flow__pane').dispatchEvent(new WheelEvent('wheel',
          { deltaX, deltaY, deltaMode: 0, bubbles: true, cancelable: true, clientX: 60, clientY: 90 }));
      }, [dx, dy]);
      await page.waitForTimeout(200);
    }
    s = await state();
    if (Math.abs(s.tx + job.x) > 0.5 || Math.abs(s.ty + job.y) > 0.5 || s.k !== 1 || s.cw < job.w || s.ch < job.h) {
      throw new Error(`could not place ${job.name}: ${JSON.stringify(s)}`);
    }
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, job.name + '.png'), clip: { x: s.cx, y: s.cy, width: job.w, height: job.h } });
    if (job.pdf) {
      await page.pdf({ path: path.join(OUT, job.name + '.raw.pdf'), width: W + 'px', height: H + 'px',
        printBackground: true, pageRanges: '1', margin: { top: 0, bottom: 0, left: 0, right: 0 } });
      fs.writeFileSync(path.join(OUT, job.name + '.crop.json'), JSON.stringify({ W, H, left: s.cx, top: s.cy, w: job.w, h: job.h }));
    }
    console.log('rendered ' + job.name);
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
