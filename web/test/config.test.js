/**
 * The configuration diagnosis.
 *
 * These are the two mistakes that actually happen when somebody sets the
 * site up and shares the link: the backend address still points at the
 * machine it was built on, or it is http on an https page. Both look to a
 * phone exactly like a server that is slow to start, so the page has to be
 * able to tell them apart.
 */
import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';

/** Load config.js fresh with a given page location and runtime config. */
async function load({ page, backend }) {
  const url = new URL(page);
  globalThis.location = {
    hostname: url.hostname,
    protocol: url.protocol,
    href: page,
  };
  globalThis.window = { POKER_CONFIG: { BACKEND_URL: backend } };
  // A cache-buster, because the module reads its config at import time.
  const mod = await import(`../src/config.js?t=${Math.random()}`);
  return mod;
}

afterEach(() => {
  delete globalThis.location;
  delete globalThis.window;
});

test('a localhost backend is fine on the machine running it', async () => {
  const { configProblem } = await load({
    page: 'http://localhost:5173/',
    backend: 'http://localhost:8000',
  });
  assert.equal(configProblem(), null);
});

test('a localhost backend is called out on any other device', async () => {
  const { configProblem } = await load({
    page: 'https://someone.github.io/poker/',
    backend: 'http://localhost:8000',
  });
  const problem = configProblem();
  assert.ok(problem, 'it has to say something');
  assert.match(problem.title, /cannot reach/i);
  assert.match(problem.detail, /localhost/);
  assert.match(problem.detail, /BACKEND_URL/);
});

test('127.0.0.1 is caught the same way', async () => {
  const { configProblem } = await load({
    page: 'https://someone.github.io/',
    backend: 'http://127.0.0.1:8000',
  });
  assert.ok(configProblem());
});

test('an http backend on an https page is caught', async () => {
  const { configProblem } = await load({
    page: 'https://someone.github.io/',
    backend: 'http://poker.onrender.com',
  });
  const problem = configProblem();
  assert.ok(problem);
  assert.match(problem.title, /not secure/i);
});

test('a properly deployed pair has nothing to report', async () => {
  const { configProblem } = await load({
    page: 'https://someone.github.io/',
    backend: 'https://poker.onrender.com',
  });
  assert.equal(configProblem(), null);
});

test('nonsense in BACKEND_URL is reported as such', async () => {
  const { configProblem } = await load({
    page: 'https://someone.github.io/',
    backend: 'not a url',
  });
  assert.match(configProblem().title, /not a valid URL/i);
});

test('the websocket address follows the backend scheme', async () => {
  const { wsUrl } = await load({
    page: 'https://someone.github.io/',
    backend: 'https://poker.onrender.com',
  });
  assert.equal(
    wsUrl('abc'),
    'wss://poker.onrender.com/ws?table=abc',
  );
});
