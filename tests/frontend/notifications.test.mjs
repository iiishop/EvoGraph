import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const require=createRequire(import.meta.url);
const data=code=>'data:text/javascript;base64,'+Buffer.from(code).toString('base64');
const compile=path=>ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const summaryUrl=data(compile('../../frontend/src/lib/changeSummary.ts'));
const {changeSummary,truncate}=await import(summaryUrl);
const notificationsCode=compile('../../frontend/src/composables/useNotifications.ts').replace("'vue'",JSON.stringify(pathToFileURL(require.resolve('vue')).href)).replace("'../lib/changeSummary'",JSON.stringify(summaryUrl));
const {useNotifications}=await import(data(notificationsCode));

test('updates describe changed fields and never exceed 86 characters',()=>{
 const before={milestones:[{id:'A',title:'登录',scope:['old']}],architectures:[],uml_diagrams:[],diagrams:[],targets:[]};
 const after={...before,milestones:[{...before.milestones[0],scope:['new']}]};
 assert.equal(changeSummary(before,after),'「登录」更新范围');
 assert.equal(Array.from(truncate('字'.repeat(100))).length,86);
});

test('new notices append below old notices and expire independently at five seconds',t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const n=useNotifications();n.push('第一次更新');t.mock.timers.tick(1000);n.push('第二次更新');
 assert.deepEqual(n.items.map(x=>x.message),['第一次更新','第二次更新']);
 t.mock.timers.tick(3999);assert.equal(n.items.length,2);
 t.mock.timers.tick(1);assert.deepEqual(n.items.map(x=>x.message),['第二次更新']);
 t.mock.timers.tick(1000);assert.equal(n.items.length,0);
});
