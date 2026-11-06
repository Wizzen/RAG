import assert from 'node:assert/strict';
import fs from 'node:fs';
import {PointerSelection} from '../../static/vehicle_tracker/js/vehicle-tracker-pointer.mjs';
import {PARTS,PART_MAP,CHILDREN,KINDS,VERSION,LINKS,linkable,within,uid,newProject,addRecord,bindRecord,unbindRecord,latestRecord,drawingSummary,guessMapping,importRows,backup,readBackup,restoredCopies,csv} from '../../static/vehicle_tracker/js/vehicle-tracker-core.mjs';
import {REPORT,reportProject} from '../../static/vehicle_tracker/js/vehicle-tracker-report.mjs';

// Structure: 5 coaches x 4 wheel carriers x 6 wheel brackets, per SDLDWARTP0001 Rev L.
assert.equal(PARTS.length,712);
assert.equal(PART_MAP.size,712);
for(const p of PARTS){
  if(p.parent)assert.ok(PART_MAP.has(p.parent),p.id);
  assert.ok(KINDS[p.kind],`${p.id} has a named kind`);
  assert.ok(p.name&&p.position,`${p.id} has an explicit name and position`);
}
const count=kind=>PARTS.filter(p=>p.kind===kind).length;
for(const [kind,n] of Object.entries({carrier:20,carrierPin:20,bearing:20,camRoller:20,cframe:20,yoke:20,loadWheel:40,loadPin:40,loadSpindle:40,guideWheel:40,guidePin:40,guideSpindle:40,upstopWheel:40,upstopPin:40,upstopSpindle:40,towbarSpindle:10,dogAxle:4,frp:5,secondary:20,storage:20}))assert.equal(count(kind),n,kind);
assert.equal(KINDS.loadPin.dwg,'SDLDWARMV0222');
assert.equal(KINDS.carrierPin.dwg,'SDLDWARMV0232');
assert.equal(KINDS.carrier.dwg,'SDLDWARMV0822');
assert.equal(KINDS.storage.shdr,'00906-55-0043','MV0822 item 8');
assert.deepEqual(CHILDREN.get('C1-LF-RF').sort(),['C1-LF-RF-PP','C1-LF-RF-SP','C1-LF-RF-W']);
assert.equal(PART_MAP.get('C1-LF-RF-PP').code,'#1LFRF');
assert.ok(PART_MAP.has('C3-DOG-AX1')&&PART_MAP.has('C4-DOG-AX2'));
assert.ok(!PART_MAP.has('C1-DOG'),'only pick-up coaches carry chain / ARB dogs');
assert.ok(within('C1-LF-RF-PP','C1'));
assert.ok(within('C1-LF-RF-PP','C1-LF'));
assert.ok(!within('C1-LF-RF-PP','C1-LR'));
assert.ok(!within('C10-X','C1'));
assert.ok(!within('','TRAIN'));

const originalUUID=globalThis.crypto.randomUUID;
try {globalThis.crypto.randomUUID=undefined;assert.match(uid(),/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);}finally{globalThis.crypto.randomUUID=originalUUID;}

const gesture=new PointerSelection(),event=(x,y,button=0,pointerId=1)=>({clientX:x,clientY:y,button,pointerId});
gesture.start(event(100,100));assert.equal(gesture.end(event(102,102)),true,'small hand jitter is still a click');
gesture.start(event(100,100));gesture.move(event(160,140));assert.equal(gesture.end(event(100,100)),false,'a drag returning to its origin cannot select');
gesture.start(event(100,100));assert.equal(gesture.end(event(110,100)),false,'release movement is counted without a pointermove event');
gesture.start(event(100,100));gesture.cancel();assert.equal(gesture.end(event(100,100)),false);
gesture.start(event(100,100,2));assert.equal(gesture.end(event(100,100,2)),false,'right-button panning never selects');
gesture.start(event(100,100));assert.equal(gesture.end(event(100,100,0,2)),false,'another pointer cannot finish a selection');

// Final assembly report WO3281713 is the only seeded data.
const report=reportProject();
assert.equal(report.records.length,285);
assert.ok(report.records.every(r=>r.partId&&PART_MAP.has(r.partId)&&r.reportWo===REPORT.wonum&&r.sourceUnit==='页'&&r.sourceRow>0));
assert.equal(new Set(report.records.map(r=>r.partId)).size,285,'one green tag per position');
assert.equal(new Set(report.records.map(r=>r.serial)).size,285,'serials are unique across the train');
assert.ok(report.records.every(r=>linkable(r.wonum)),'every report work order can be linked');
const pin=report.records.find(r=>r.partId==='C1-LF-RF-PP');
assert.deepEqual([pin.serial,pin.wonum,pin.drawing,pin.slot],['BZID009','2103550','SDLDWARMV0222','#1LFRF']);
assert.equal(latestRecord(report,'C1-LF-RF-PP'),pin);
assert.equal(latestRecord(report,'C2-TB-SPF'),null,'site-assembled towbar spindles have no green tag');
assert.ok(report.parts['C2-TB-SPF']?.notes,'and are explained in part notes');
assert.equal(readBackup(JSON.parse(JSON.stringify(backup([report])))).length,1);

// Deep links into the Maximo work-order and QAQC / MRB modules never carry unsafe codes.
assert.equal(LINKS.maximoWo('3023726'),'/tools/vehicle-tracker/external/maximo-workorder?code=3023726');
assert.equal(LINKS.qaqcWo('3023726'),'/tools/vehicle-tracker/external/qaqc-workorder?code=3023726');
assert.equal(LINKS.qaqcPart('SDLDWARMV0222'),'/tools/vehicle-tracker/external/qaqc-part?code=SDLDWARMV0222');
assert.equal(LINKS.jobPlan('QAL4070051'),'/tools/vehicle-tracker/external/job-plan?code=QAL4070051');
assert.ok(LINKS.woPdf('3023726').startsWith('/tools/vehicle-tracker/external/workorder-pdf?code=3023726'));
for(const bad of ['','12','javascript:alert(1)','1&tab=x','"><img>','-123','a/b','x'.repeat(41)])for(const link of Object.values(LINKS))assert.equal(link(bad),'',bad);

const project=newProject('合成验收项目');
assert.equal(project.records.length,0,'new projects start empty');
const manual=addRecord(project,'C1-LF-RF-PP',{wonum:'4000001',serial:'BZID009',inspector:'QA99',date:'2026-11-01'});
assert.equal(manual.drawing,'SDLDWARMV0222','drawing defaults from the part kind');
assert.equal(manual.source,'手工登记');
assert.throws(()=>addRecord(project,'C1-LF-RF-PP',{wonum:'4000001',serial:'BZID009'}),/相同/);
assert.throws(()=>addRecord(project,'C1-LF-RF-PP',{wonum:'4000002'}),/序列号/);
assert.throws(()=>addRecord(project,'C1-LF-RF-PP',{wonum:'a/b',serial:'X'}),/工单号/);
assert.throws(()=>addRecord(project,'NOPE',{wonum:'4000002',serial:'X'}),/不存在/);

const mapping=guessMapping(['Work Order','S/N','图号','检验员','日期','位置码','描述']);
assert.deepEqual(mapping,{wonum:0,serial:1,drawing:2,inspector:3,date:4,slot:5,description:6});
const rows=[['0000123','0000000987','SDLDWARMV0222','QA01','2026-11-01','#1LFRF','销轴'],['0000123','0000000987','SDLDWARMV0222','','','','重复行'],['0000123','0000000987','SDLDWARMV0232','','','','不同图号保留'],['','','','','','',''],['a|b','d','','','','',''],['a','b|c','','','','','']];
assert.deepEqual(importRows(project,rows,mapping,'synthetic.xlsx'),{added:4,skipped:1,empty:1});
const imported=project.records[1];
assert.equal(imported.wonum,'0000123');assert.equal(imported.serial,'0000000987');assert.equal(imported.sourceRow,2);
assert.ok(project.records.slice(1).every(r=>r.partId===''));
assert.throws(()=>importRows(project,rows,{...mapping,serial:0},'x'),/不同/);
assert.throws(()=>importRows(project,rows,{...mapping,serial:-1},'x'),/两列/);
assert.equal(project.records.length,5,'invalid mappings never partially import');

bindRecord(project,imported.id,'C1-LF-RF-PP');
assert.equal(imported.partId,'C1-LF-RF-PP');
assert.throws(()=>bindRecord(project,imported.id,'C1-LF-RF-SP'),/其他部件/);
assert.throws(()=>bindRecord(project,imported.id,'C1-LF-RF-PP'),/已绑定当前/);
assert.throws(()=>bindRecord(project,imported.id,'NOT-A-PART'),/不存在/);
unbindRecord(project,imported.id);bindRecord(project,imported.id,'C1-LF-RF-SP');
assert.equal(imported.partId,'C1-LF-RF-SP','pin and spindle remain independent');
assert.equal(project.history.filter(h=>h.action==='解除追踪绑定').length,1);
assert.equal(importRows(project,rows,mapping,'second.csv').added,0,'duplicate imports preserve original bindings');
assert.equal(imported.partId,'C1-LF-RF-SP');

const image='data:image/png;base64,iVBORw0KGgo=';
project.references.push({id:'image-1',name:'示意图.png',mime:'image/png',data:image,width:1920,height:1080,hotspots:[{id:'hotspot-1',partId:imported.partId,x:.173,y:.763,label:'心轴'}]});
project.attachments.push({id:'attachment-1',partId:imported.partId,name:'检验说明.txt',mime:'text/plain',data:'data:text/plain;base64,dGVzdA==',size:4});
project.parts[imported.partId]={name:'测试心轴',notes:'合成验收记录'};
const envelope=JSON.parse(JSON.stringify(backup([project,newProject('第二项目')]))) ;
assert.equal(envelope.version,VERSION);
assert.equal(readBackup(envelope).length,2);
const copies=restoredCopies(envelope),copy=copies[0];
assert.notEqual(copy.id,project.id);assert.equal(copy.name,'合成验收项目（恢复副本）');
assert.deepEqual(copy.records,project.records);assert.deepEqual(copy.references,project.references);assert.deepEqual(copy.attachments,project.attachments);assert.deepEqual(copy.parts,project.parts);
assert.equal(copy.history.length,project.history.length+1);
copy.records[1].partId='C2';assert.equal(project.records[1].partId,'C1-LF-RF-SP','restore never mutates existing projects');

assert.throws(()=>readBackup({...envelope,version:1}),/版本 1/,'backups of the retired demo model are refused');
for(const mutate of [
  b=>b.version=99,
  b=>b.projects[0].records[0].partId='missing',
  b=>b.projects[0].records[0].serial=12,
  b=>b.projects[0].references[0].hotspots[0].x=1.1,
  b=>b.projects[0].references[0].hotspots[0].partId='C99',
  b=>b.projects[0].attachments[0].partId='C99',
  b=>b.projects[0].attachments[0].data='javascript:alert(1)',
  b=>b.projects[0].attachments[0].mime='text/html',
  b=>b.projects[0].records.push({...b.projects[0].records[0]}),
  b=>b.projects.push(b.projects[0]),
  b=>b.projects[0].parts.C99={name:'bad',notes:''},
  b=>b.projects[0].records[0].id='"><img src=x onerror=alert(1)>',
  b=>b.projects[0].references[0].hotspots[0].id='unsafe"id',
]){const bad=structuredClone(envelope);mutate(bad);assert.throws(()=>readBackup(bad),/备份无效/);}
assert.throws(()=>restoredCopies({...envelope,projects:[...envelope.projects,{id:'broken'}]}),/备份无效/,'validate every project before starting restore');
assert.equal(copy.references[0].hotspots[0].x,.173);

const output=csv([{wonum:'=HYPERLINK("x")',serial:'00012',drawing:'a,b',description:'line\ntext',date:'',partId:'C1-LF-RF-PP',source:'a"b',sourceUnit:'页',sourceRow:3}]);
assert.ok(output.startsWith('\uFEFF'));assert.ok(output.includes('"\'=HYPERLINK(""x"")"'));assert.ok(output.includes('"00012"'));assert.ok(output.includes('"a,b"'));
assert.ok(output.includes('"第 3 页"'));assert.ok(output.includes('左前轮架'));

// Exercise the actual vendored spreadsheet reader, including formatted leading zeroes.
const xlsxSource=fs.readFileSync(new URL('../../static/vehicle_tracker/vendor/xlsx-0.20.3.full.min.js',import.meta.url),'utf8');
const vm=await import('node:vm'),sandbox={};vm.runInNewContext(xlsxSource,sandbox);
const XLSX=sandbox.XLSX,wb=XLSX.utils.book_new();
const sheet=XLSX.utils.aoa_to_sheet([['工单号','序列号','图号'],['000123','0000000000000000987','SDLDWARMV0222']]);
XLSX.utils.book_append_sheet(wb,sheet,'追踪');
const bytes=XLSX.write(wb,{type:'array',bookType:'xlsx'}),loaded=XLSX.read(bytes,{type:'array'});
const parsed=XLSX.utils.sheet_to_json(loaded.Sheets['追踪'],{header:1,raw:false,defval:''});
assert.equal(parsed[1][0],'000123');assert.equal(parsed[1][1],'0000000000000000987');
const csvData=XLSX.read('工单号,序列号,图号\n000123,0000987,SDLDWARMV0222',{type:'string',raw:true});
const csvRows=XLSX.utils.sheet_to_json(csvData.Sheets[csvData.SheetNames[0]],{header:1,raw:false,defval:''});
assert.equal(csvRows[1][0],'000123');assert.equal(csvRows[1][1],'0000987');
{
  const seeded=reportProject(),pins=drawingSummary(seeded,'SDLDWARMV0234');
  assert.deepEqual(pins.kinds,['guidePin','upstopPin']);
  assert.equal(pins.parts.length,80);assert.equal(pins.records.length,80);assert.equal(pins.serials,80);
  assert.ok(pins.parts.every(({part,record})=>record.partId===part.id&&record.drawing==='SDLDWARMV0234'));
  const towbar=drawingSummary(seeded,'SDLDWARMV0514');
  assert.equal(towbar.parts.length,10);assert.equal(towbar.serials,6,'positions without a green tag stay unregistered');
  const unknown=drawingSummary(newProject('x'),'NOT-A-DRAWING');
  assert.deepEqual([unknown.kinds.length,unknown.parts.length,unknown.records.length],[0,0,0]);
}
console.log('Vehicle tracker: 712-part SDMT hierarchy, WO3281713 green tags (285), safe Maximo / QAQC links, drawing-number summaries, manual registration, imports, binding conflicts, full restore, corrupted backups and XLSX/CSV preservation passed.');
