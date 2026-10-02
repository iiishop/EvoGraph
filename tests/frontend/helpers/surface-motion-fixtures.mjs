import { readFileSync } from 'node:fs';
import ts from 'typescript';

const code = readFileSync(
  new URL('../../../frontend/src/composables/useSurfaceMotion.ts', import.meta.url),
  'utf8',
);
const compiled = ts
  .transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  })
  .outputText.replace(/from 'vue'/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`);
export const surfaceMotionUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`;
