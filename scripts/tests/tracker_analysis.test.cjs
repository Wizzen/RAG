const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const helpers=import('data:text/javascript;base64,'+fs.readFileSync(path.join(__dirname,'../../static/js/tracker_analysis.js')).toString('base64'));

test('zero, missing and large numbers stay distinct in metrics',async()=>{
  const {formatNumber,formatValue}=await helpers;
  assert.equal(formatNumber(null),'—');assert.equal(formatNumber(0),'0');
  assert.equal(formatNumber('1.25'),'1.25');assert.equal(formatValue(3,'number',true),'+3');
  assert.equal(formatValue(-3,'number',true),'-3');assert.equal(formatNumber(1e100),'1.000e+100');
});
test('clock parameters display time and deltas display elapsed differences',async()=>{
  const {formatValue}=await helpers;
  assert.equal(formatValue(570,'clock'),'09:30');assert.equal(formatValue(0,'clock'),'00:00');
  assert.equal(formatValue(585.5,'clock'),'09:45:30');assert.equal(formatValue(-30,'clock',true),'−30分');
  assert.equal(formatValue(90,'clock',true),'+1小时 30分');assert.equal(formatValue(null,'clock'),'—');
});
test('period labels use the task timezone rather than the browser timezone',async()=>{
  const {periodLabel}=await helpers;
  assert.equal(periodLabel('2026-01-01T16:00:00Z','day','Asia/Shanghai'),'2026-01-02');
  assert.equal(periodLabel('2026-01-01T16:00:00Z','hour','Asia/Shanghai'),'2026-01-02 00:00');
  assert.equal(periodLabel('2026-01-01T16:00:00Z','month','Asia/Shanghai'),'2026-01');
  assert.equal(periodLabel('2026-01-01T16:00:00Z','year','Asia/Shanghai'),'2026');
});
