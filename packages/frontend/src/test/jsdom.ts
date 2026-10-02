// Gives a test file a jsdom document, for @testing-library/react.
//
// The stock `environment: 'jsdom'` cannot start in this workspace: the pnpm
// override undici>=7.24.0 (root package.json) installs undici 8, which no
// longer ships lib/handler/wrap-handler.js and unwrap-handler.js, and jsdom 29
// requires both when it loads (hence the static-markup tests elsewhere).
// While those two files are missing, each require of them gets a stub. jsdom
// uses them only to fetch network resources, which tests never do; the stub
// throws if one ever tries. Then vitest's own jsdom environment is set up.
//
// Use: keep `// @vitest-environment node` and import this module first,
// before react-dom and @testing-library/react (they look for `document` when
// they load).
import Module, { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { afterAll } from 'vitest';
import { builtinEnvironments } from 'vitest/runtime';

type ResolveFilename = ((
  this: unknown,
  request: string,
  ...rest: unknown[]
) => string) & { stubsUndiciHandlers?: boolean };

const MISSING = new Set([
  'undici/lib/handler/wrap-handler.js',
  'undici/lib/handler/unwrap-handler.js',
]);
// Never read from disk: the require cache answers for this path.
const STUB_PATH = fileURLToPath(
  new URL('./undici-handler-stub.cjs', import.meta.url),
);

class UndiciHandlerStub {
  constructor() {
    throw new Error(
      'jsdom cannot fetch network resources in tests (undici 8 lacks its wrap/unwrap handlers)',
    );
  }
}

function stubMissingUndiciHandlers() {
  // Node's CommonJS resolver: internal, so not in @types/node.
  const loader = Module as unknown as { _resolveFilename: ResolveFilename };
  if (loader._resolveFilename.stubsUndiciHandlers) return;

  const stub = new Module(STUB_PATH);
  stub.filename = STUB_PATH;
  stub.exports = UndiciHandlerStub;
  stub.loaded = true;
  createRequire(import.meta.url).cache[STUB_PATH] = stub;

  const resolve = loader._resolveFilename;
  const resolveFilename: ResolveFilename = function (request, ...rest) {
    try {
      return resolve.call(this, request, ...rest);
    } catch (error) {
      const code = (error as { code?: unknown } | null)?.code;
      if (code === 'MODULE_NOT_FOUND' && MISSING.has(request)) return STUB_PATH;
      throw error;
    }
  };
  resolveFilename.stubsUndiciHandlers = true;
  loader._resolveFilename = resolveFilename;
}

stubMissingUndiciHandlers();
const { teardown } = await builtinEnvironments.jsdom.setup(globalThis, {});
// jsdom has no scrolling; the router scrolls to the top on navigation.
window.scrollTo = () => undefined;
afterAll(async () => {
  // React runs passive effects of the last unmount on a later macrotask;
  // let them run while `window` still exists.
  await new Promise((resolve) => setTimeout(resolve, 20));
  teardown(globalThis);
});
