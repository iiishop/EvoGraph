import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

const require = createRequire(import.meta.url);
async function load(path) {
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText.replaceAll('elkjs/lib/elk.bundled.js', pathToFileURL(require.resolve('elkjs/lib/elk.bundled.js')).href);
  return import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
}
const { separateBoxes, routeAroundBoxes } = await load('../../frontend/src/lib/graphGeometry.ts');
const { layout } = await load('../../frontend/src/composables/useGraphLayout.ts');
const overlaps = (a,b) => a.x < b.x+b.width && a.x+a.width>b.x && a.y<b.y+b.height && a.y+a.height>b.y;

test('dragging onto another node produces non-overlapping positions',()=>{
  const boxes=separateBoxes(['a','b','c'].map(id=>({id,x:50,y:50,width:236,height:150})));
  for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++)assert.equal(overlaps(boxes[i],boxes[j]),false);
});

test('manual long edge routes around intervening milestone',()=>{
 const boxes=[{id:'a',x:0,y:0,width:236,height:150},{id:'b',x:320,y:0,width:236,height:150},{id:'c',x:640,y:0,width:236,height:150}];
 const route=routeAroundBoxes({x:236,y:75},{x:640,y:75},boxes);
 assert.ok(route.length>2);
 for(let i=1;i<route.length;i++){
   const a=route[i-1],b=route[i];
   for(const r of boxes){
     const crossing=a.x===b.x ? a.x>r.x && a.x<r.x+r.width && Math.max(a.y,b.y)>r.y && Math.min(a.y,b.y)<r.y+r.height : a.y>r.y && a.y<r.y+r.height && Math.max(a.x,b.x)>r.x && Math.min(a.x,b.x)<r.x+r.width;
     assert.equal(crossing,false);
   }
 }
});

test('branch and merge layout has unique positions and forward prerequisites',async()=>{
 const graph=[{id:'A',dependencies:[]},{id:'B',dependencies:['A']},{id:'C',dependencies:['A']},{id:'D',dependencies:['B','C']},{id:'E',dependencies:['A','D']}];
 const result=await layout(graph);
 const boxes=[...result.positions].map(([id,p])=>({id,...p,width:236,height:150}));
 for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++)assert.equal(overlaps(boxes[i],boxes[j]),false);
 for(const node of graph)for(const dep of node.dependencies)assert.ok(result.positions.get(node.id).x>result.positions.get(dep).x);
 assert.equal(result.routes.size,6);
});

const {splinePath,smoothWaypoints}=await load('../../frontend/src/lib/curves.ts');
test('automatic and manual routes render cubic curves, never orthogonal line commands',async()=>{
 const result=await layout([{id:'A',dependencies:[]},{id:'B',dependencies:['A']},{id:'C',dependencies:['A']},{id:'D',dependencies:['B','C']}]);
 for(const points of result.routes.values()){
  const path=splinePath(points);
  assert.ok(path.includes(' C '));
  assert.equal(/[LHQV]/.test(path),false);
 }
 const manual=smoothWaypoints([{x:0,y:0},{x:30,y:0},{x:30,y:100},{x:100,y:100}]);
 assert.ok(manual.includes(' C '));assert.equal(/[LHQV]/.test(manual),false);
});
