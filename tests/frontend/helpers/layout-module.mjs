import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';

const require = createRequire(import.meta.url);
export const moduleUrl = (code) =>
  `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
export const compile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;

// Resolve just the layout modules' runtime imports for direct Node source tests.
export function resolveLayoutImports(
  code,
  elkUrl = pathToFileURL(require.resolve('elkjs/lib/elk.bundled.js')).href,
) {
  const lazyElkUrl = moduleUrl(
    compile(
      readFileSync(new URL('../../../frontend/src/lib/lazyElk.ts', import.meta.url), 'utf8'),
    ).replaceAll('elkjs/lib/elk.bundled.js', elkUrl),
  );
  return code
    .replaceAll("'../lib/lazyElk'", JSON.stringify(lazyElkUrl))
    .replaceAll("'./lazyElk'", JSON.stringify(lazyElkUrl))
    .replaceAll('elkjs/lib/elk.bundled.js', elkUrl);
}
