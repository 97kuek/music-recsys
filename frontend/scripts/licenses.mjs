import { readFile, writeFile, mkdir } from 'node:fs/promises';
const root = new URL('../', import.meta.url);
const publicDir = new URL('public/', root);
await mkdir(publicDir, {recursive:true});
for (const [source,target] of [['LICENSE','license.txt'],['NOTICE','notice.txt']]) {
  await writeFile(new URL(target,publicDir),await readFile(new URL(`../${source}`,root)));
}
const notices=[];
for (const name of ['react','react-dom','scheduler','vite']) {
  const base=new URL(`node_modules/${name}/`,root);
  const meta=JSON.parse(await readFile(new URL('package.json',base),'utf8'));
  notices.push(`${name} ${meta.version}\n\n${await readFile(new URL(name==='vite'?'LICENSE.md':'LICENSE',base),'utf8')}`);
}
await writeFile(new URL('third-party-licenses.txt',publicDir),notices.join('\n\n--------------------\n\n'));
