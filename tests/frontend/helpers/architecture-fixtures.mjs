import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';
const require = createRequire(import.meta.url);
export const moduleUrl = (code) =>
  `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
export const source = (path) =>
  readFileSync(new URL(`../../../frontend/src/${path}`, import.meta.url), 'utf8');
export const compile = (code) =>
  ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
export const vueUrl = pathToFileURL(require.resolve('vue')).href;
export const rolesUrl = moduleUrl(compile(source('lib/architectureRoles.ts')));
export const browseModelUrl = moduleUrl(
  compile(source('lib/architectureBrowse.ts')).replace(
    "from './architectureRoles'",
    `from ${JSON.stringify(rolesUrl)}`,
  ),
);
export const browseStoreUrl = moduleUrl(
  compile(source('composables/useArchitectureBrowse.ts'))
    .replace("from 'vue'", `from ${JSON.stringify(vueUrl)}`)
    .replace("from '../lib/architectureBrowse'", `from ${JSON.stringify(browseModelUrl)}`),
);
export const browseImports = {
  '../../lib/architectureBrowse': browseModelUrl,
  '../../composables/useArchitectureBrowse': browseStoreUrl,
};
