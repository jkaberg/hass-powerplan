// Build the module HA loads on every page, and the chunks it loads on demand
// (D12 §3, §5.5, §5.10). Deterministic: CI rebuilds it and fails when the
// committed bundle differs (D12 §9 7).
//
// Two passes. The cards and ECharts first, split, every file named by its
// content hash, so a browser never holds a stale one. Then `powerplan.js`,
// which defines the strategy with no static import at all - HA gives the
// strategy element 5 s from the first paint (B1) - and loads the cards'
// entry by the name the first pass gave it. The entry's own cache key is its
// hash too (`dashboard/__init__.py`).
import { rmSync } from "node:fs";
import { build } from "esbuild";

const outdir = "../custom_components/powerplan/frontend/dist";
const common = { bundle: true, format: "esm", target: "es2022", minify: true, legalComments: "none", logLevel: "info" };
rmSync(outdir, { recursive: true, force: true });
const cards = await build({
  ...common,
  entryPoints: { cards: "src/cards.ts" },
  outdir: `${outdir}/chunks`,
  splitting: true,
  entryNames: "[name]-[hash]",
  chunkNames: "[name]-[hash]",
  metafile: true,
});
const entry = Object.entries(cards.metafile.outputs).find(([, out]) => out.entryPoint === "src/cards.ts")[0];
await build({
  ...common,
  entryPoints: { powerplan: "src/index.ts" },
  outdir,
  define: {
    CARDS_MODULE: JSON.stringify(`./chunks/${entry.split("/").pop()}`),
    // The cards' content hash names the build in the console (G1).
    BUILD_HASH: JSON.stringify(entry.split("/").pop().replace(/^cards-|\.js$/g, "")),
  },
});
