// Dependency-free local design preview. The production pages remain static.
import { createServer } from 'node:http';
import { readFile, realpath, stat } from 'node:fs/promises';
import { resolve, join, extname, sep } from 'node:path';
const args = process.argv.slice(2);
const arg = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const root = await realpath(resolve(arg('--root', '.')));
const types = { '.html':'text/html; charset=utf-8', '.css':'text/css; charset=utf-8', '.js':'text/javascript; charset=utf-8', '.mjs':'text/javascript; charset=utf-8', '.json':'application/json; charset=utf-8', '.svg':'image/svg+xml', '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.ico':'image/x-icon', '.md':'text/plain; charset=utf-8' };
createServer(async (request,response) => {
  if (!['GET','HEAD'].includes(request.method)) { response.writeHead(405); response.end(); return; }
  try {
    const path = decodeURIComponent(new URL(request.url, 'http://preview.local').pathname);
    let file = resolve(root, '.' + path);
    if (file !== root && !file.startsWith(root + sep)) throw new Error('outside root');
    if ((await stat(file)).isDirectory()) file = join(file,'index.html');
    file = await realpath(file);
    if (!file.startsWith(root + sep)) throw new Error('outside root');
    const bytes = await readFile(file);
    response.writeHead(200,{'Content-Type':types[extname(file)] || 'application/octet-stream','Content-Length':bytes.length,'Cache-Control':'no-store'});
    response.end(request.method === 'HEAD' ? undefined : bytes);
  } catch { response.writeHead(404,{'Content-Type':'text/plain'}); response.end('Not found'); }
}).listen(Number(arg('--port','4173')), arg('--host','127.0.0.1'), () => console.log('Design preview is ready'));
