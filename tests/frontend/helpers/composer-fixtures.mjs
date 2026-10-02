import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const url = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const source = readFileSync(
  new URL('../../../frontend/src/lib/composerDocument.ts', import.meta.url),
  'utf8',
);
export const composerDocumentUrl = url(
  ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText,
);
const vue = pathToFileURL(require.resolve('vue')).href;
export const composerEditorStubUrl =
  url(`import { h, ref, withDirectives, vModelText } from ${JSON.stringify(vue)};
import { textDocument, renderComposerDocument } from ${JSON.stringify(composerDocumentUrl)};
export default { props: ['modelValue', 'disabled', 'inputLabel', 'placeholder'], emits: ['update:modelValue','submit','requestCatalog','error'], setup(props, { emit, expose }) {
 const element = ref(); expose({ focus: () => element.value?.focus(), closeSuggestions() {} });
 return () => withDirectives(h('textarea', { ref: element, id:'agent-message', disabled:props.disabled, placeholder:props.placeholder, 'aria-label':props.inputLabel,
 'onUpdate:modelValue':text => emit('update:modelValue',textDocument(text)),
 onKeydown:event => { if(event.key==='Enter' && !event.shiftKey && !event.isComposing && event.keyCode!==229){event.preventDefault();emit('submit');} }
 }), [[vModelText, renderComposerDocument(props.modelValue)]]);
} };`);
export const messageContentStubUrl = url(
  `import { h } from ${JSON.stringify(vue)}; export default { props:['message'], setup(props){return()=>h('p',{},props.message.content);} };`,
);
