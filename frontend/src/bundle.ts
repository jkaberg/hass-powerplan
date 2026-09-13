// The loader's own cache key, `powerplan.js?v=<hash>` - the `v` of PowerPlan's Lovelace resource
// (iteration 4, F7; D12 §5.16 R5). Imported first by `index.ts`, so `version-check.ts` reads it through the build's
// `__PP_BUNDLE__` define (`globalThis.__ppBundle`). The key hashes the loader itself, so the
// build cannot write it in; the URL the page loaded carries it.

const key = new URL(import.meta.url).searchParams.get("v");
if (key) (globalThis as { __ppBundle?: string }).__ppBundle = key;

export {};
