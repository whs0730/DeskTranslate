import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const root = new URL('../', import.meta.url);
const output = new URL('src/desktranslate/web/', root);
mkdirSync(output, { recursive: true });
const style = readFileSync(new URL('frontend/style.css', root), 'utf8');
const glassLicense = readFileSync(new URL('node_modules/@librecosta/liquid-glass/LICENSE', root), 'utf8');
const hyaliteLicense = readFileSync(new URL('node_modules/@librecosta/liquid-glass/licenses/Hyalite-MIT.txt', root), 'utf8');
for (const [pageName, entry] of [['index', 'app'], ['settings', 'settings']]) {
  const bundle = await build({
    entryPoints: [fileURLToPath(new URL(`frontend/src/${entry}.ts`, root))],
    bundle: true,
    format: 'iife',
    target: 'es2022',
    write: false,
    minify: true,
  });
  const page = readFileSync(new URL(`frontend/${pageName}.html`, root), 'utf8')
    .replace('<link rel="stylesheet" href="./style.css">', () => `<style nonce="__DESKTRANSLATE_NONCE__">${style}</style>`)
    .replace(`<script type="module" src="./${entry}.js"></script>`, () => `<script type="module" nonce="__DESKTRANSLATE_NONCE__">/*\n${glassLicense}\n${hyaliteLicense}\n*/\n${bundle.outputFiles[0].text}</script>`)
    .replace("script-src 'self' 'unsafe-eval'; style-src 'self'", "script-src 'nonce-__DESKTRANSLATE_NONCE__' 'unsafe-eval'; style-src 'nonce-__DESKTRANSLATE_NONCE__'");
  writeFileSync(new URL(`${pageName}.html`, output), page);
}
console.log(`界面已构建：${fileURLToPath(output)}`);
