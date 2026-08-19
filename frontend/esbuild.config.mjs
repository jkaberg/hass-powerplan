// Build the module HA loads on every page, and the chunks it loads on demand
// (D12 §3, §5.5). Deterministic: CI rebuilds it and fails when the committed
// bundle differs (D12 §9 7). Chunk names carry their content hash, so a
// browser never holds a stale one; the entry's own cache key is its hash too.
import { rmSync } from "node:fs";
import { build } from "esbuild";

const outdir = "../custom_components/powerplan/frontend/dist";
rmSync(outdir, { recursive: true, force: true });
await build({
  entryPoints: { powerplan: "src/index.ts" },
  outdir,
  bundle: true,
  splitting: true,
  format: "esm",
  target: "es2022",
  minify: true,
  chunkNames: "chunks/[name]-[hash]",
  legalComments: "none",
  logLevel: "info",
});
