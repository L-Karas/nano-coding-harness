// 通过 Chrome DevTools Protocol 取 arena.ai 页面的最终 DOM。
// 由 fetch_leaderboard.sh 调用，一般不需要单独运行。
//
// 用法: node cdp_dom.mjs <port> <outFile> <waitSec>
//
// 为什么不直接 chrome --dump-dom：
//   在这个 chromium build 上 --dump-dom 返回 0 字节，且无法控制等待时长。
//   走 CDP 的 Runtime.evaluate 拿 document.documentElement.outerHTML 稳定得多，
//   也能在 Cloudflare 挑战跑完后才取值。
import fs from 'node:fs';

const [, , portArg = '9222', outFile = 'out.html', waitArg = '12'] = process.argv;
const PORT = Number(portArg);
const WAIT_MS = Math.max(0, Number(waitArg) || 0) * 1000;
const CDP_TIMEOUT_MS = 30_000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function findPageTarget() {
  for (let i = 0; i < 40; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await res.json();
      const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page;
    } catch {
      /* chrome 还没起来，继续等 */
    }
    await sleep(500);
  }
  throw new Error('no page target on port ' + PORT);
}

const target = await findPageTarget();
console.log('target:', target.url);

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => {
  ws.onopen = res;
  ws.onerror = (e) => rej(new Error('ws error: ' + (e.message || 'unknown')));
});

let id = 0;
const pending = new Map();

ws.onmessage = (ev) => {
  let msg;
  try {
    msg = JSON.parse(ev.data);
  } catch {
    return;
  }
  const entry = msg.id ? pending.get(msg.id) : undefined;
  if (!entry) return;
  pending.delete(msg.id);
  if (msg.error) entry.reject(new Error(JSON.stringify(msg.error)));
  else entry.resolve(msg.result);
};

function send(method, params = {}) {
  const mid = ++id;
  ws.send(JSON.stringify({ id: mid, method, params }));
  return new Promise((resolve, reject) => {
    // 没有超时的话，CDP 一旦不回包就会永远挂着（表现为 unsettled top-level await）
    const timer = setTimeout(() => {
      pending.delete(mid);
      reject(new Error(`CDP timeout ${CDP_TIMEOUT_MS}ms: ${method}`));
    }, CDP_TIMEOUT_MS);
    pending.set(mid, {
      resolve: (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      reject: (e) => {
        clearTimeout(timer);
        reject(e);
      },
    });
  });
}

async function evaluate(expression) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true });
  return r.result.value;
}

await send('Page.enable');
await send('Runtime.enable');

// 先等 document.readyState === 'complete'，再额外等 challenge 跑完
for (let i = 0; i < 60; i++) {
  if ((await evaluate('document.readyState')) === 'complete') break;
  await sleep(500);
}
console.log('waiting', WAIT_MS / 1000, 's for page settle / challenge...');
await sleep(WAIT_MS);

const title = await evaluate('document.title');
const href = await evaluate('location.href');
const html = await evaluate('document.documentElement.outerHTML');

ws.close();

if (!html) {
  // 不要写出空文件：调用方会以为是「抓到了但没数据」
  console.error('empty DOM, 检查 title:', JSON.stringify(title));
  process.exit(1);
}

fs.writeFileSync(outFile, html, 'utf8');
console.log('title:', JSON.stringify(title));
console.log('href :', JSON.stringify(href));
console.log('len  :', html.length, '->', outFile);
