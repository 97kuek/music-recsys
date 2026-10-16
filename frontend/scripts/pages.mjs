import { readFile, copyFile, writeFile } from 'node:fs/promises';
const assets = new URL('../../src/music_recsys/webapp/static/', import.meta.url);
const demo = JSON.parse(await readFile(new URL('demo/demo.json', assets), 'utf8'));
if (!demo.tracks?.length || !demo.tracks.every(t => t.sample === true && t.item_id.startsWith('sample-'))) throw new Error('Pages deployment requires the synthetic public demo');
await copyFile(new URL('../worker/index.mjs', import.meta.url), new URL('_worker.js', assets));
await writeFile(new URL('_routes.json', assets), JSON.stringify({version:1,include:['/api','/api/*','/availability'],exclude:[]}));
