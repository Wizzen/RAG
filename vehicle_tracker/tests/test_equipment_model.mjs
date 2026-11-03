import test from 'node:test';
import assert from 'node:assert/strict';
import {FORMAT, normalizeManifest, componentOrder} from '../../static/vehicle_tracker/js/equipment-model.mjs';

const device = {
  format: FORMAT, version: 1, id: 'pump', name: '试验泵组',
  components: [{id: 'motor', parent: 'pump', name: '电机', drawing: 'ab-42'}, {id: 'pump', name: '总成'}],
  entries: [{kind: 'tracking', component: 'motor', at: '2026-01-01T08:00:00Z', text: '完成核对'}],
};
test('nonvehicle entries and forward parent references round trip through the portable format', () => {
  const clean = normalizeManifest(device);
  assert.equal(clean.components[0].drawing, 'AB-42');
  assert.deepEqual(componentOrder(clean.components).map(({part, depth}) => [part.id, depth]), [['pump',0],['motor',1]]);
  assert.deepEqual(normalizeManifest(JSON.parse(JSON.stringify(clean))), clean);
});
test('reject ambiguous or dangling component references before import', () => {
  const same = device.components[0];
  for (const components of [
    [same,same], [same], [{...same,parent:'motor'}],
    [same, {id:'pump',name:'泵组',parent:'motor'}],
  ]) assert.throws(() => normalizeManifest({...device, components}));
  assert.throws(() => normalizeManifest({...device, entries:[{...device.entries[0],component:'other'}]}));
});
