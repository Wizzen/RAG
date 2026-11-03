// Stable component IDs are the only keys used for all project associations.
export const FORMAT = 'shdr-vehicle-tracker';
export const VERSION = 2;
export const MAX_BACKUP_BYTES = 250*1024*1024;
export const MAX_PROJECT_BYTES = 200*1024*1024;
export function assertCapacity(p) {
  if(new Blob([JSON.stringify(p)]).size>MAX_PROJECT_BYTES-2*1024*1024)throw Error('项目接近 200 MB 容量上限，请新建项目保存更多资料');
}
export const uid = () => {
  if(globalThis.crypto.randomUUID)return globalThis.crypto.randomUUID();
  // getRandomValues also works on an intranet HTTP origin. randomUUID requires
  // HTTPS in desktop browsers, while local projects should work in either case.
  const b=globalThis.crypto.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;
  const s=Array.from(b,n=>n.toString(16).padStart(2,'0')).join('');return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`;
};
export const now = () => new Date().toISOString();

export const SPEC_DOC = 'SDLDWARTP0001 Rev L';
export const TYPES = {train:'列车',coach:'车厢',body:'座舱',chassis:'底盘',towbar:'牵引杆',dog:'卡件',carrier:'轮架',frame:'结构件',bracket:'轮支架',wheel:'车轮',bearing:'轴承',pin:'销轴'};
// SDLDWARTP0001 Rev L, Section 4 (China Regulatory Requirements): every tracked pin and spindle.
const MT_UT = '磁粉检测（MT）+ 超声检测（UT）+ 尺寸检查';
const MT_ONLY = '磁粉检测（MT）+ 尺寸检查';
const req = method => ({method,frequency:'每 240,000 次运行循环（每年至少 2 列车）',jobPlan:'QAL4070051'});
export const KINDS = {
  train:{type:'train',zh:'列车总成',en:'Train Assembly',dwg:'SDLDWARMV0001',wdc:'7028-933002'},
  coachFront:{type:'coach',zh:'前车',en:'Front Coach',dwg:'SDLDWARMV0004',wdc:'7028-933005'},
  coachInter:{type:'coach',zh:'中间车',en:'Intermediate Coach',dwg:'',wdc:''},
  coachPickup:{type:'coach',zh:'提升车',en:'Pick Up Coach',dwg:'SDLDWARMV0002',wdc:'7028-933003'},
  coachRear:{type:'coach',zh:'后车（TAV 无障碍）',en:'Rear Coach (TAV)',dwg:'SDLDWARMV0005',wdc:'7028-933006'},
  scu:{type:'body',zh:'座舱单元 SCU',en:'Seat Compartment Unit',dwg:'SDLDWARMV0007',wdc:'7028-933008'},
  scuTav:{type:'body',zh:'座舱单元 SCU（TAV）',en:'Seat Compartment Unit, TAV',dwg:'SDLDWARMV0008',wdc:'7028-933009'},
  frp:{type:'body',zh:'下部车身（FRP，含装饰车轮）',en:'FRP Lower Section Vehicle',dwg:'SDLDWARMV0053',wdc:'7028-933013'},
  scuSpindle:{type:'pin',zh:'SCU 摆动心轴',en:'SCU Pivoting Spindle',dwg:'SDLDWARMV0112',wdc:'3040-932756',shdr:'60350-66-0118',req:req(MT_UT)},
  chassis:{type:'chassis',zh:'底盘',en:'Chassis',dwg:'SDLDWARMV0248',wdc:'7028-932283'},
  frontAxle:{type:'chassis',zh:'前轴',en:'Front Axle',dwg:'SDLDWARMV0250',wdc:'7028-932285'},
  mainAxle:{type:'chassis',zh:'主轴总成',en:'Main Axle Assembly',dwg:'SDLDWARMV0258',wdc:'7028-932293'},
  bankSpindle:{type:'pin',zh:'倾斜心轴（主轴心轴）',en:'Main Axle Spindle (Banking Spindle)',dwg:'SDLDWARMV0259',wdc:'7028-932294',shdr:'60350-52-0010',req:req(MT_ONLY)},
  fin:{type:'chassis',zh:'制动鳍板',en:'Brake Fin Assembly',dwg:'SDLDWARMV0354',wdc:'5995-932445'},
  towbar:{type:'towbar',zh:'牵引杆总成',en:'Towbar Assembly',dwg:'SDLDWARMV0510',wdc:'7028-932624'},
  towbarSpindle:{type:'pin',zh:'牵引杆心轴',en:'Towbar Spindle',dwg:'SDLDWARMV0514',wdc:'7028-932628',shdr:'60350-56-0011',req:req(MT_UT)},
  dog3:{type:'dog',zh:'链条/防倒滑卡件总成',en:'Chain / ARB Dog Assembly (Coach 3)',dwg:'SDLDWARMV0828',wdc:'7028-932997'},
  dog4:{type:'dog',zh:'链条/防倒滑卡件总成',en:'Chain / ARB Dog Assembly (Coach 4)',dwg:'SDLDWARMV0827',wdc:'7028-932996'},
  dogAxle:{type:'pin',zh:'链条/防倒滑卡件轴',en:'Chain / ARB Dog Axle',dwg:'SDLDWARMV0829',wdc:'7028-932998',shdr:'60428-58-0016',req:req(MT_UT)},
  carrier:{type:'carrier',zh:'轮架总成',en:'Wheelcarrier Assembly',dwg:'SDLDWARMV0822',wdc:'2630-932991'},
  cframe:{type:'frame',zh:'C 型架',en:'C-Frame',dwg:'SDLDWARMV0217',wdc:'7028-932251',shdr:'00906-55-0011'},
  yoke:{type:'frame',zh:'轭架',en:'Yoke Wheel Carrier',dwg:'SDLDWARMV0221',wdc:'7028-932255',shdr:'00906-55-0028'},
  carrierPin:{type:'pin',zh:'轮架枢轴销',en:'Pivot Pin, Wheel Carrier',dwg:'SDLDWARMV0232',wdc:'5315-932267',shdr:'00906-55-0042',req:req(MT_UT)},
  bearing:{type:'bearing',zh:'回转支承',en:'Slewing Bearing (INA F-552573.01.VU)',dwg:'SDLDWARMV0822 Item 50',wdc:'2630-932991'},
  secondary:{type:'frame',zh:'二次支撑',en:'Secondary Support',dwg:'SDLDWARMV0822 Item 7',wdc:'2630-932991',shdr:'00906-55-0024'},
  storage:{type:'frame',zh:'存放轮及二次支撑',en:'Storage Wheel and Secondary Support',dwg:'SDLDWARMV0822 Item 8',wdc:'2630-932991',shdr:'00906-55-0043'},
  camRoller:{type:'wheel',zh:'维修轮（凸轮滚轮）',en:'Cam Roller (Maintenance Wheel)',dwg:'SDLDWARMV0498',wdc:'5340-932610',shdr:'60350-55-0014'},
  loadBracket:{type:'bracket',zh:'行走轮支架',en:'Load Wheel (RW) Bracket',dwg:'SDLDWARMV0503',wdc:'5340-932615',shdr:'60350-55-0033'},
  loadWheel:{type:'wheel',zh:'行走轮',en:'Load Wheel Ø230 (Urethane MV0098 / Hub MV0509)',dwg:'SDLDWARMV0098',wdc:'5340-932620',shdr:'60350-55-0500'},
  loadSpindle:{type:'pin',zh:'行走轮心轴',en:'Load Wheel Spindle',dwg:'SDLDWARMV0239',wdc:'2630-932274',shdr:'00906-55-0057',req:req(MT_ONLY)},
  loadPin:{type:'pin',zh:'行走轮枢轴销',en:'Pivot Pin, Load Wheel Bracket',dwg:'SDLDWARMV0222',wdc:'5315-932256',shdr:'00906-55-0030',req:req(MT_UT)},
  guideBracket:{type:'bracket',zh:'导向轮支架',en:'GW and UW Bracket (Guide)',dwg:'SDLDWARMV0504',wdc:'5340-932616',shdr:'60350-55-0034'},
  guideWheel:{type:'wheel',zh:'导向轮',en:'Guide Wheel Ø150 (Urethane MV0099)',dwg:'SDLDWARMV0099',wdc:'5340-932621',shdr:'60350-55-0503'},
  guideSpindle:{type:'pin',zh:'导向轮心轴',en:'Guide/Upstop Wheel Spindle (Guide)',dwg:'SDLDWARMV0238',wdc:'2630-932273',shdr:'00906-55-0056',req:req(MT_UT)},
  guidePin:{type:'pin',zh:'导向轮枢轴销',en:'Pivot Pin, Guide/Upstop Wheel Bracket (Guide)',dwg:'SDLDWARMV0234',wdc:'5315-932269',shdr:'00906-55-0044',req:req(MT_UT)},
  upstopBracket:{type:'bracket',zh:'防脱轮支架',en:'GW and UW Bracket (Upstop)',dwg:'SDLDWARMV0504',wdc:'5340-932616',shdr:'60350-55-0034'},
  upstopWheel:{type:'wheel',zh:'防脱轮',en:'Upstop Wheel Ø150 (Urethane MV0099)',dwg:'SDLDWARMV0099',wdc:'5340-932621',shdr:'60350-55-0503'},
  upstopSpindle:{type:'pin',zh:'防脱轮心轴',en:'Guide/Upstop Wheel Spindle (Upstop)',dwg:'SDLDWARMV0238',wdc:'2630-932273',shdr:'00906-55-0056',req:req(MT_UT)},
  upstopPin:{type:'pin',zh:'防脱轮枢轴销',en:'Pivot Pin, Guide/Upstop Wheel Bracket (Upstop)',dwg:'SDLDWARMV0234',wdc:'5315-932269',shdr:'00906-55-0044',req:req(MT_UT)},
};
export const COACHES = [
  {no:1,kind:'coachFront',dog:null},
  {no:2,kind:'coachInter',dog:null},
  {no:3,kind:'coachPickup',dog:'dog3'},
  {no:4,kind:'coachPickup',dog:'dog4'},
  {no:5,kind:'coachRear',dog:null},
];
// Left/right as seen facing the direction of travel; F = front end of the coach.
export const CARRIERS = {LF:'左前轮架',LR:'左后轮架',RF:'右前轮架',RR:'右后轮架'};
// Wheel bracket codes follow the assembly report: R = load (行走), G = guide, S = upstop; F/R = front/rear.
export const BRACKETS = {
  RF:{group:'load',end:'前'},RR:{group:'load',end:'后'},
  GF:{group:'guide',end:'前'},GR:{group:'guide',end:'后'},
  SF:{group:'upstop',end:'前'},SR:{group:'upstop',end:'后'},
};
export function structure() {
  const parts=[];
  const add=(id,parent,kind,name,position,extra={})=>parts.push({id,parent,kind,type:KINDS[kind].type,name,position,...extra});
  add('TRAIN',null,'train','列车总成（5 节车厢）','全车');
  for(const coach of COACHES) {
    const c=`C${coach.no}`,cname=`C${coach.no} ${KINDS[coach.kind].zh}`;
    add(c,'TRAIN',coach.kind,cname,cname,{car:coach.no});
    add(`${c}-SCU`,c,coach.no===5?'scuTav':'scu',KINDS[coach.no===5?'scuTav':'scu'].zh,`${cname} · 座舱`,{car:coach.no});
    for(const [end,zh] of [['F','前'],['R','后']]) add(`${c}-SCU-SP${end}`,`${c}-SCU`,'scuSpindle',`SCU 摆动心轴（${zh}）`,`${cname} · 座舱 · ${zh}`,{car:coach.no,code:`C${coach.no} 轴 ${zh}${end}`});
    add(`${c}-FRP`,c,'frp',KINDS.frp.zh,`${cname} · 下部车身`,{car:coach.no});
    add(`${c}-CH`,c,'chassis','底盘',`${cname} · 底盘`,{car:coach.no});
    add(`${c}-CH-FA`,`${c}-CH`,'frontAxle','前轴',`${cname} · 底盘 · 前端`,{car:coach.no});
    add(`${c}-CH-MA`,`${c}-CH`,'mainAxle','主轴总成',`${cname} · 底盘 · 后端`,{car:coach.no});
    add(`${c}-CH-MA-BS`,`${c}-CH-MA`,'bankSpindle','倾斜心轴（主轴心轴）',`${cname} · 底盘 · 主轴`,{car:coach.no,code:`C${coach.no} 倾斜心轴`});
    add(`${c}-CH-FIN`,`${c}-CH`,'fin','制动鳍板',`${cname} · 底盘 · 车底中线`,{car:coach.no});
    add(`${c}-TB`,c,'towbar','牵引杆总成',`${cname} · 前端连接`,{car:coach.no});
    for(const [end,zh,where] of [['F','前','靠前车端'],['R','后','本车端']]) add(`${c}-TB-SP${end}`,`${c}-TB`,'towbarSpindle',`牵引杆心轴（${zh}）`,`${cname} · 牵引杆 · ${where}`,{car:coach.no,code:`C${coach.no} 牵引杆 ${zh}${end}`});
    if(coach.dog) {
      add(`${c}-DOG`,c,coach.dog,KINDS[coach.dog].zh,`${cname} · 车底`,{car:coach.no});
      for(const n of [1,2]) add(`${c}-DOG-AX${n}`,`${c}-DOG`,'dogAxle',`链条/防倒滑卡件轴 #${n}`,`${cname} · 车底 · 第 ${n} 件`,{car:coach.no,code:`C${coach.no} 卡件轴 #${n}`});
    }
    for(const [wc,wname] of Object.entries(CARRIERS)) {
      const w=`${c}-${wc}`,code=`#${coach.no}${wc}`,wpos=`${cname} · ${wname}`,extra={car:coach.no,carrier:wc};
      add(w,c,'carrier',wname,wpos,{...extra,code});
      add(`${w}-CF`,w,'cframe','C 型架',wpos,{...extra,code:`${code} C型架`});
      add(`${w}-YK`,w,'yoke','轭架',wpos,extra);
      add(`${w}-PP`,w,'carrierPin','轮架枢轴销',wpos,{...extra,code:`${code} 轮架枢轴销`});
      add(`${w}-SB`,w,'bearing','回转支承',wpos,extra);
      add(`${w}-SS`,w,'secondary',KINDS.secondary.zh,wpos,extra);
      add(`${w}-STW`,w,'storage',KINDS.storage.zh,`${wpos} · 内侧`,extra);
      add(`${w}-CR`,w,'camRoller','维修轮（凸轮滚轮）',`${wpos} · 外侧`,extra);
      for(const [b,{group,end}] of Object.entries(BRACKETS)) {
        const id=`${w}-${b}`,bcode=`${code}${b}`,zh=KINDS[`${group}Wheel`].zh,bpos=`${wpos} · ${zh}（${end}）`,more={...extra,bracket:b};
        add(id,w,`${group}Bracket`,`${KINDS[`${group}Bracket`].zh}（${end}）`,bpos,{...more,code:bcode});
        add(`${id}-W`,id,`${group}Wheel`,`${zh}（${end}）`,bpos,more);
        add(`${id}-SP`,id,`${group}Spindle`,`${KINDS[`${group}Spindle`].zh}（${end}）`,bpos,{...more,code:bcode});
        add(`${id}-PP`,id,`${group}Pin`,`${KINDS[`${group}Pin`].zh}（${end}）`,bpos,{...more,code:bcode});
      }
    }
  }
  return parts;
}
export const PARTS=structure();
export const PART_MAP=new Map(PARTS.map(p=>[p.id,p]));
export const CHILDREN=new Map(PARTS.map(p=>[p.id,[]]));
for(const p of PARTS) if(p.parent) CHILDREN.get(p.parent).push(p.id);
export const kindOf=id=>KINDS[PART_MAP.get(id)?.kind]||null;
export const within=(child,parent)=>child===parent || (parent==='TRAIN' && PART_MAP.has(child)) || child.startsWith(parent+'-');

// Deep links to the toolbox's Maximo modules; codes are validated so a record cannot inject a URL.
const CODE=/^[A-Za-z0-9][A-Za-z0-9-]{2,39}$/;
export const linkable=value=>CODE.test(String(value??''));
export const LINKS = {
  maximoWo:wonum=>linkable(wonum)?`/tools/vehicle-tracker/external/maximo-workorder?code=${encodeURIComponent(wonum)}`:'',
  qaqcWo:wonum=>linkable(wonum)?`/tools/vehicle-tracker/external/qaqc-workorder?code=${encodeURIComponent(wonum)}`:'',
  woPdf:wonum=>linkable(wonum)?`/tools/vehicle-tracker/external/workorder-pdf?code=${encodeURIComponent(wonum)}`:'',
  qaqcPart:part=>linkable(part)?`/tools/vehicle-tracker/external/qaqc-part?code=${encodeURIComponent(part)}`:'',
  jobPlan:jpnum=>linkable(jpnum)?`/tools/vehicle-tracker/external/job-plan?code=${encodeURIComponent(jpnum)}`:'',
};

export function log(project,action,partId='',detail='') {
  project.history.push({id:uid(),at:now(),action,partId,detail:String(detail).slice(0,10000)});
  project.updatedAt=now();
}
export const RECORD_FIELDS=['wonum','serial','drawing','facility','inspector','date','slot','reportWo','description','source','sourceUnit'];
export function newProject(name='未命名项目',origin='空白项目') {
  const project={id:uid(),name,description:'',createdAt:now(),updatedAt:now(),parts:{},records:[],references:[],attachments:[],history:[]};
  log(project,'创建项目','',`${origin} · ${PARTS.length} 个部件`);
  return project;
}
const recordKey=r=>JSON.stringify([r.wonum,r.serial,r.drawing]);
export function addRecord(project,partId,fields) {
  if(!PART_MAP.has(partId))throw Error('部件不存在');
  const r=Object.fromEntries(RECORD_FIELDS.map(k=>[k,String(fields[k]??'').trim()]));
  if(!r.wonum||!r.serial)throw Error('请填写工单号和序列号');
  if(Object.values(r).some(v=>v.length>10000))throw Error('字段超过 10,000 字');
  if(!linkable(r.wonum))throw Error('工单号只能包含字母、数字和短横线（3–40 位）');
  r.drawing||=kindOf(partId)?.dwg||'';
  if(project.records.some(x=>recordKey(x)===recordKey(r)&&x.partId===partId))throw Error('此部件已有相同工单号和序列号的记录');
  if(project.records.length>=20000)throw Error('每个项目最多保存 20,000 条记录');
  const record={...r,id:uid(),partId,source:r.source||'手工登记',sourceUnit:'',sourceRow:0};
  project.records.push(record);
  log(project,'登记工单',partId,`${record.wonum} / 序列号 ${record.serial}`);
  return record;
}
export function bindRecord(project,recordId,partId) {
  const record=project.records.find(r=>r.id===recordId);
  if(!record || !PART_MAP.has(partId)) throw Error('记录或部件不存在');
  if(record.partId && record.partId!==partId) throw Error('此记录已绑定其他部件，请先解除原绑定');
  if(record.partId===partId) throw Error('此记录已绑定当前部件');
  record.partId=partId;
  log(project,'绑定追踪记录',partId,`${record.wonum} / ${record.serial}`);
}
export function unbindRecord(project,recordId) {
  const record=project.records.find(r=>r.id===recordId);
  if(!record?.partId) throw Error('记录未绑定');
  log(project,'解除追踪绑定',record.partId,`${record.wonum} / ${record.serial}`);
  record.partId='';
}
/** Newest record per part: the serial currently fitted at that position. */
/** Everything the tracker knows about one drawing / part number: kinds, positions, current serials and records. */
export function drawingSummary(project,code) {
  const kinds=Object.keys(KINDS).filter(k=>KINDS[k].dwg===code||KINDS[k].shdr===code),set=new Set(kinds);
  const records=project.records.filter(r=>r.drawing===code),ids=new Set(PARTS.filter(p=>set.has(p.kind)).map(p=>p.id));
  for(const r of records)if(r.partId)ids.add(r.partId);
  const parts=PARTS.filter(p=>ids.has(p.id)).map(part=>({part,record:latestRecord(project,part.id)}));
  return {code,kinds,records,parts,serials:parts.filter(x=>x.record?.serial).length};
}
export function latestRecord(project,partId) {
  let best=null;
  for(const r of project.records) if(r.partId===partId && (!best || (r.date||'')>=(best.date||''))) best=r;
  return best;
}
export const HEADERS={
  wonum:['工单号','工单','wonum','workorder','workordernumber','wo','wo no','wono'],
  serial:['序列号','serial','serialnumber','sn','s/n'],
  drawing:['图号','图纸号','drawing','drawingno','dwg','partno','零件号'],
  inspector:['检验员','inspector','qa'],
  date:['日期','检验日期','date'],
  slot:['位置码','位置','slot','position'],
  description:['描述','部件名称','description','name'],
};
const norm=value=>String(value??'').normalize('NFKC').toLowerCase().replace(/[\s_\-（）()./]/g,'');
export function guessMapping(headers) {
  return Object.fromEntries(Object.entries(HEADERS).map(([key,names])=>[key,headers.findIndex(h=>names.some(n=>norm(n)===norm(h)))]));
}
export function importRows(project,rows,mapping,source) {
  for(const key of ['wonum','serial']) if(!Number.isInteger(mapping[key]) || mapping[key]<0) throw Error('请指定工单号和序列号两列');
  if(mapping.wonum===mapping.serial) throw Error('工单号和序列号必须对应不同的列');
  const existing=new Set(project.records.map(recordKey)), records=[];
  let skipped=0,empty=0;
  rows.forEach((row,i)=>{
    const r=Object.fromEntries(RECORD_FIELDS.map(key=>[key,Number.isInteger(mapping[key])&&mapping[key]>=0?String(row[mapping[key]]??'').trim():'']));
    if(Object.values(r).some(v=>v.length>10000))throw Error(`第 ${i+2} 行字段超过 10,000 字，请缩短后导入`);
    if(!r.wonum && !r.serial) {empty++;return;}
    const key=recordKey(r);
    if(existing.has(key)) {skipped++;return;}
    existing.add(key);records.push({...r,id:uid(),partId:'',source,sourceUnit:'行',sourceRow:i+2});
  });
  if(project.records.length+records.length>20000) throw Error('每个项目最多保存 20,000 条记录，请分项目导入');
  assertCapacity({...project,records:[...project.records,...records]});
  project.records.push(...records);
  log(project,'导入追踪表','',`${source}：新增 ${records.length}，重复 ${skipped}，空行 ${empty}`);
  return {added:records.length,skipped,empty};
}
const string=(v,max=10000)=>typeof v==='string' && v.length<=max;
function check(ok,message) {if(!ok) throw Error(`备份无效：${message}`);}
function dataFile(file) {
  check(string(file.id,80)&&string(file.name,500)&&string(file.mime,160)&&string(file.data,40*1024*1024),'文件字段');
  check(/^data:[a-z0-9.+/-]+;base64,[A-Za-z0-9+/]*={0,2}$/.test(file.data),'附件编码');
  check(file.data.slice(5,file.data.indexOf(';'))===file.mime,'文件类型不匹配');
}
const safeId=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/.test(value);
function unique(items,label) {check(items.every(x=>x&&safeId(x.id)),`${label} ID 格式`);check(new Set(items.map(x=>x.id)).size===items.length,`${label} ID 重复`);}
export function validateProject(p) {
  check(p && safeId(p.id)&&string(p.name,200)&&p.name.trim()&&string(p.description)&&string(p.createdAt,60)&&string(p.updatedAt,60),'项目字段');
  check(p.parts && typeof p.parts==='object'&&!Array.isArray(p.parts),'部件资料');
  for(const [id,m] of Object.entries(p.parts)) check(PART_MAP.has(id)&&m&&string(m.name,200)&&string(m.notes),'部件资料关联');
  for(const key of ['records','references','attachments','history']) {check(Array.isArray(p[key]),`${key} 列表`);unique(p[key],key);}
  check(p.records.length<=20000&&p.references.length<=100&&p.attachments.length<=500&&p.history.length<=100000,'项目容量');
  for(const r of p.records) {
    check(r&&string(r.id,80)&&RECORD_FIELDS.every(k=>string(r[k])),'追踪字段');
    check(r.partId===''||PART_MAP.has(r.partId),'追踪部件不存在');
    check(Number.isInteger(r.sourceRow)&&r.sourceRow>=0,'原始页码 / 行号');
  }
  for(const ref of p.references) {
    dataFile(ref);check(['image/png','image/jpeg','image/webp'].includes(ref.mime),'参考图类型');
    check(Number.isFinite(ref.width)&&ref.width>0&&Number.isFinite(ref.height)&&ref.height>0,'图片尺寸');
    check(Array.isArray(ref.hotspots)&&ref.hotspots.length<=2000,'热点列表');unique(ref.hotspots,'热点');
    for(const h of ref.hotspots) check(string(h.id,80)&&PART_MAP.has(h.partId)&&string(h.label,200)&&Number.isFinite(h.x)&&h.x>=0&&h.x<=1&&Number.isFinite(h.y)&&h.y>=0&&h.y<=1,'热点位置或部件关联');
  }
  for(const file of p.attachments) {dataFile(file);check(PART_MAP.has(file.partId)&&Number.isFinite(file.size)&&file.size>=0,'附件部件关联');}
  for(const h of p.history) check(h&&string(h.id,80)&&string(h.at,60)&&string(h.action,200)&&string(h.detail)&&(!h.partId||PART_MAP.has(h.partId)),'历史记录');
  check(new Blob([JSON.stringify(p)]).size<=MAX_PROJECT_BYTES,'项目超过 200 MB');
  return p;
}
export function readBackup(value) {
  check(value?.format===FORMAT,'格式不支持');
  check(value.version!==1,'版本 1 来自旧版五车厢演示模型，部件编号已全部更换，不能恢复到当前结构');
  check(value.version===VERSION,'版本不支持');
  check(Array.isArray(value.projects)&&value.projects.length>0&&value.projects.length<=100,'项目列表');
  unique(value.projects,'项目');
  return value.projects.map(validateProject);
}
export function backup(projects) {projects.forEach(validateProject);return {format:FORMAT,version:VERSION,exportedAt:now(),projects};}
export function restoredCopies(value) {
  return readBackup(value).map(p=>{
    const copy=structuredClone(p);copy.id=uid();copy.name=(copy.name+'（恢复副本）').slice(0,200);log(copy,'恢复完整备份','',`原项目 ${p.id}`);return copy;
  });
}
export function csv(records) {
  const cell=v=>'"'+String(v??'').replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')+'"';
  const where=r=>r.partId?PART_MAP.get(r.partId).position+' / '+PART_MAP.get(r.partId).name:'';
  return '\uFEFF'+[['工单号','序列号','图号','游乐设施','检验员','检验日期','报告位置码','总装报告工单','描述','部件 ID','部件位置','原始文件','页码 / 行号'],
    ...records.map(r=>[r.wonum,r.serial,r.drawing,r.facility,r.inspector,r.date,r.slot,r.reportWo,r.description,r.partId,where(r),r.source,r.sourceRow?`${r.sourceUnit==='页'?'第 '+r.sourceRow+' 页':'行 '+r.sourceRow}`:''])].map(row=>row.map(cell).join(',')).join('\r\n');
}
export class ProjectStore {
  async open() {
    this.db=await new Promise((resolve,reject)=>{
      const r=indexedDB.open('shdr-vehicle-tracker',2);
      r.onupgradeneeded=e=>{
        // Version 1 held the synthetic five-coach demo; its part IDs no longer exist.
        if(e.oldVersion>=1)r.result.deleteObjectStore('projects');
        r.result.createObjectStore('projects',{keyPath:'id'});
      };
      r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);
      r.onblocked=()=>{const e=Error('其他浏览器标签页仍打开着旧版「车辆部件追踪」，本地存储无法升级。请关闭这些标签页后刷新本页。');e.blocked=true;reject(e);};
    });
    this.db.onversionchange=()=>this.db.close();
    return this;
  }
  async transaction(mode,operation) {
    return new Promise((resolve,reject)=>{
      const tx=this.db.transaction('projects',mode),store=tx.objectStore('projects');let result;
      try {result=operation(store);} catch(e) {tx.abort();reject(e);return;}
      tx.oncomplete=()=>resolve(result?.result);tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||Error('保存事务已取消'));
    });
  }
  all() {return this.transaction('readonly',s=>s.getAll());}
  put(p) {return this.transaction('readwrite',s=>s.put(structuredClone(p)));}
  putAll(projects) {return this.transaction('readwrite',s=>{projects.forEach(p=>s.put(structuredClone(p)));});}
}
