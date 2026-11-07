const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../templates/kb/ask.html'), 'utf8');

const start = source.indexOf("} else if (etype === 'citations') {");
const end = source.indexOf("} else if (etype === 'step')", start);
const update = source.slice(start + "} else if (etype === 'citations') {".length, end);
function receive(previous, payload) {
  const context = {turnCitations: previous, payload};
  vm.runInNewContext(update, context);
  return JSON.parse(JSON.stringify(context.turnCitations));
}

test('final citation snapshot removes discarded chunks from the same document and other documents', () => {
  const discarded = [{doc_id:'a', selected_chunk_ids:['old']}, {doc_id:'b', selected_chunk_ids:['other']}];
  const selected = [{doc_id:'a', selected_chunk_ids:['chosen']}];
  assert.deepEqual(receive(discarded, {replace:true, citations:selected}), selected);
  assert.deepEqual(receive(selected, {replace:true, citations:[]}), []);
});

test('legacy additive citation events preserve graph, tracker and document identities', () => {
  const graph = {kind:'graph',relation_id:1,fact_digest:'g'};
  const tracker = {kind:'tracker',result_digest:'t'};
  assert.deepEqual(receive([graph,tracker], {citations:[{doc_id:'d'}]}), [graph,tracker,{doc_id:'d'}]);
});

test('citation fallback link passes only the explicitly selected chunk IDs', () => {
  const first = source.indexOf('function renderCitations(');
  const last = source.indexOf('window.renderCitations =', first);
  const element = tag => ({tag,children:[],appendChild(child){this.children.push(child);},querySelector(){return null;}});
  const context = {document:{createElement:element}};
  vm.runInNewContext(source.slice(first,last), context);
  const bubble = element('div');
  context.renderCitations({querySelector:()=>bubble}, [{doc_id:'doc', source:'Manual.pdf',
    highlights:['a phrase shared by many chunks'], selected_chunk_ids:['chosen-1','chosen-2']}]);
  const link = bubble.children[0].children.find(node=>node.tag==='a');
  assert.deepEqual(new URL(link.href,'https://example.test').searchParams.getAll('chunk'), ['chosen-1','chosen-2']);
});
