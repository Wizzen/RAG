const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const helpers=import('data:text/javascript;base64,'+fs.readFileSync(path.join(__dirname,'../../static/js/kb_connections.js')).toString('base64'));
const canvasHelpers=import('data:text/javascript;base64,'+fs.readFileSync(path.join(__dirname,'../../static/js/kb_connection_canvas.js')).toString('base64'));

test('many-to-many connections form clusters across categories, retaining isolated KBs',async()=>{
  const {connectedComponents}=await helpers;
  const nodes=['manual','data','reports','other'].map(slug=>({slug}));
  assert.deepEqual(connectedComponents(nodes,[{left:'manual',right:'data'},{left:'reports',right:'manual'}]),[['manual','data','reports'],['other']]);
});

test('the bubble canvas includes every KB and leaves space for connection handles',async()=>{
  const {initialBubblePositions}=await canvasHelpers;
  const nodes=Array.from({length:32},(_,index)=>({slug:'kb-'+index}));
  const positions=initialBubblePositions(nodes,[nodes.slice(0,25).map(n=>n.slug),...nodes.slice(25).map(n=>[n.slug])]);
  assert.equal(positions.size,32);
  for(const p of positions.values()){assert.ok(p.x>=25&&p.y>=25);assert.ok(Number.isFinite(p.x)&&Number.isFinite(p.y));}
  const points=[...positions.values()];
  for(let a=0;a<points.length;a++)for(let b=a+1;b<points.length;b++)assert.ok(Math.hypot(points[a].x-points[b].x,points[a].y-points[b].y)>=190);
});

test('the less connected KB moves toward the hub, independent of drag direction',async()=>{
  const {connectionAttraction}=await canvasHelpers;
  const positions=new Map([['hub',{x:400,y:300}],['light',{x:1200,y:700}],['b',{x:100,y:100}],['c',{x:100,y:500}]]);
  const connection={left:'hub',right:'light'},links=[{left:'hub',right:'b'},{left:'hub',right:'c'},connection];
  const result=connectionAttraction(positions,links,connection,'hub');
  assert.equal(result.lighter,'light');assert.equal(result.heavier,'hub');
  const hub=positions.get('hub');
  assert.ok(Math.hypot(result.target.x-hub.x,result.target.y-hub.y)<400);
  assert.deepEqual(positions.get('hub'),{x:400,y:300});
});

test('bubble size follows the right volume metric, stays bounded, and wires touch the actual sizes',async()=>{
  const {bubbleDiameter,bubbleWire}=await canvasHelpers;
  const small=bubbleDiameter({category:'manual',document_count:1,record_count:90000});
  const medium=bubbleDiameter({category:'manual',document_count:50});
  const large=bubbleDiameter({category:'business',record_count:5000,document_count:1});
  assert.ok(small<medium&&medium<large);assert.ok(large<=232);
  assert.equal(bubbleDiameter({category:'business',record_count:0}),136);
  const a={x:50,y:100,size:small},b={x:450,y:250,size:large};
  const path=bubbleWire(a,b),coordinates=path.match(/[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?/gi).map(Number);
  assert.ok(Math.abs(Math.hypot(coordinates[0]-a.x-small/2,coordinates[1]-a.y-small/2)-(small/2+2))<.01);
  assert.ok(Math.abs(Math.hypot(coordinates.at(-2)-b.x-large/2,coordinates.at(-1)-b.y-large/2)-(large/2+2))<.01);
  assert.notEqual(path,bubbleWire({...a,x:100},b));
});

test('an equal-degree pair moves the dragged KB and avoids a third bubble',async()=>{
  const {connectionAttraction,bubbleWire}=await canvasHelpers;
  const positions=new Map([['a',{x:100,y:300}],['b',{x:1000,y:300}],['obstacle',{x:800,y:300}]]);
  const link={left:'a',right:'b'},result=connectionAttraction(positions,[link],link,'a');
  assert.equal(result.lighter,'a');assert.equal(result.heavier,'b');
  assert.ok(Math.hypot(result.target.x-800,result.target.y-300)>=185);
  assert.ok(!/NaN|Infinity/.test(bubbleWire(result.target,positions.get('b'))));
});
test('cycles, reversed edges and missing endpoints do not duplicate or invent KBs',async()=>{
  const {connectedComponents}=await helpers;
  const nodes=['a','b','c','d'].map(slug=>({slug}));
  const links=[{left:'a',right:'b'},{left:'b',right:'c'},{left:'c',right:'a'},
    {left:'b',right:'a'},{left:'unknown',right:'d'},{left:'d',right:'d'}];
  assert.deepEqual(connectedComponents(nodes,links),[['a','b','c'],['d']]);
  assert.deepEqual(connectedComponents([],links),[]);
});
