// Bundle only the pinned official CLI and validators, not the Astro application.
// Install esbuild/zod separately in a disposable tools directory; no scripts run here.
const path = require('node:path');
const [suite, tools, output] = process.argv.slice(2);
if (!suite || !tools || !output) throw Error('Usage: node build_openresponses_runner.cjs SUITE TOOLS_DIRECTORY OUTPUT.mjs');
const modules = path.resolve(tools, 'node_modules');
if (require(path.join(modules, 'esbuild/package.json')).version !== '0.25.12' || require(path.join(modules, 'zod/package.json')).version !== '3.25.76') throw Error('Expected esbuild 0.25.12 and zod 3.25.76');
require(path.join(modules, 'esbuild')).buildSync({
  entryPoints: [path.resolve(suite, 'bin/compliance-test.ts')],
  bundle: true, platform: 'node', format: 'esm', nodePaths: [modules],
  tsconfigRaw: {compilerOptions: {}}, outfile: path.resolve(output)
});
