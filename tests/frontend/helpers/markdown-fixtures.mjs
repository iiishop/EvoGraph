import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = readFileSync(
  new URL('../../../frontend/src/components/agent/MarkdownContent.ts', import.meta.url),
  'utf8',
);
export const markdownContentUrl = `data:text/javascript;base64,${Buffer.from(
  ts
    .transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    })
    .outputText.replace("from 'vue'", `from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace("from 'markdown-it'", `from ${JSON.stringify(import.meta.resolve('markdown-it'))}`),
).toString('base64')}`;
