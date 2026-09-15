// The loader's own cache key, `powerplan.js?v=<hash>` - the `v` of PowerPlan's Lovelace resource
// (iteration 4, F7; D12 §5.16 R5). Imported first by `index.ts`, so `version-check.ts` reads it through the build's
// `__PP_BUNDLE__` define (`globalThis.__ppKey`). The key hashes the loader itself, so the
// build cannot write it in; the URL the page loaded carries it. Iteration 5: `__ppBundle` belongs to
// `strategy-shim.ts`, which compares it with this bundle's key to tell that an older bundle loaded
// first (D-0625).

const key = new URL(import.meta.url).searchParams.get("v");
if (key) (globalThis as { __ppKey?: string }).__ppKey = key;

export {};
