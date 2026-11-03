import {PARTS,PART_MAP,KINDS,within} from './vehicle-tracker-core.mjs';

export const SAMPLE_LIMIT=60;
export function fitRequest(value) {
  const payload=structuredClone(value),encoder=new TextEncoder();
  while(encoder.encode(JSON.stringify(payload)).length>92*1024){
    if(payload.history.length){payload.history.splice(0,2);continue;}
    const lists=[payload.snapshot.records,payload.snapshot.current_records].sort((a,b)=>b.length-a.length);
    if(lists[0].length>1){lists[0].splice(Math.max(1,Math.floor(lists[0].length*.75)));continue;}
    if(payload.snapshot.notes.length>1){payload.snapshot.notes.pop();continue;}
    throw Error('本次资料过多，请缩小分析范围。');
  }
  return payload;
}
const fields=['wonum','serial','drawing','date','inspector','slot','description','source','sourceUnit'];
const plain=value=>typeof value==='string'?value.slice(0,280):'';
const record=row=>({...Object.fromEntries(fields.map(key=>[key,plain(row[key])])),partId:row.partId||'',sourceRow:Number.isInteger(row.sourceRow)?row.sourceRow:0});

export function buildSnapshot(project,selected,{scope='selected',mode='explain'}={}) {
  if(!PART_MAP.has(selected))throw Error('请先选择部件');
  const root=scope==='train'?'TRAIN':selected;
  const parts=PARTS.filter(part=>within(part.id,root));
  const rows=project.records.filter(row=>row.partId?within(row.partId,root):root==='TRAIN');
  const latest=new Map();
  for(const row of rows)if(row.partId){const previous=latest.get(row.partId);if(!previous||(row.date||'')>=(previous.date||''))latest.set(row.partId,row);}
  const missing=parts.filter(part=>KINDS[part.kind].req&&!latest.has(part.id));
  const serials=new Map();
  for(const [id,row] of latest)if(row.serial){const list=serials.get(row.serial)||[];list.push(id);serials.set(row.serial,list);}
  const duplicates=[...serials].filter(([,ids])=>ids.length>1).map(([serial,part_ids])=>({serial:plain(serial),part_ids:part_ids.slice(0,12)}));
  const noteIds=[...new Set([selected,...missing.map(part=>part.id),...parts.map(part=>part.id)])];
  const notes=noteIds.filter(id=>project.parts[id]?.notes).slice(0,12).map(id=>({part_id:id,notes:project.parts[id].notes.slice(0,700)}));
  const sample=rows.slice().sort((a,b)=>(b.date||'').localeCompare(a.date||'')).slice(0,SAMPLE_LIMIT);
  return {
    project:{id:project.id,name:project.name.slice(0,200)},selected,scope,
    summary:{total_records:project.records.length,scope_records:rows.length,unbound_records:rows.filter(row=>!row.partId).length,
      attachments:project.attachments.filter(file=>within(file.partId,root)).length,references:project.references.length},
    records:mode==='help'?[]:sample.map(record),
    current_records:mode==='help'?[]:[...latest.values()].slice(0,SAMPLE_LIMIT).map(record),
    notes:mode==='help'?[]:notes,
    audit:{missing_records:missing.length,missing_part_ids:missing.slice(0,12).map(part=>part.id),
      duplicate_positions:[...serials.values()].filter(ids=>ids.length>1).reduce((sum,ids)=>sum+ids.length,0),duplicate_serials:duplicates.slice(0,8),
      invalid_dates:rows.filter(row=>row.date&&!/^\d{4}-\d{2}-\d{2}$/.test(row.date)).length},
  };
}
