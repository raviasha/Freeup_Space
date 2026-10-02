// Exercise the real renderer in a small DOM; no live cleanup session is involved.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.attributes={}; this.style={}; this.text=''; }
  set textContent(value) { this.text=String(value); }
  get textContent() { return this.text + this.children.map(c=>c.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; this.text=''; }
  setAttribute(name,value) { this.attributes[name]=value; }
}
const app=new Element('main');
const context=vm.createContext({console, Date, Map, Set, Math, Number, String,
  document:{querySelector:()=>app, createElement:tag=>new Element(tag), addEventListener(){}},
  window:{addEventListener(){}}, parent:{postMessage(){}}, setTimeout(){}, clearTimeout(){}});
const html=fs.readFileSync(path.join(__dirname,'../assets/widget.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
vm.runInContext(script.slice(0,script.indexOf('(async()=>{try{await rpc("ui/initialize"')),context);
const now=Date.now()/1000;
function render(progress) {
  context.fixture={status:'scanning',mode:'deep',started_at:now-60,progress};
  vm.runInContext('state=fixture; app.replaceChildren(); renderProgress()',context);
  return app.textContent;
}
const text=render({phase:'Categorizing files',processed:25,total:100,unit:'files',
  files_scanned:100,bytes_scanned:2048,errors:2,phase_started_at:now-10,updated_at:now-20});
assert.match(text,/25\s*\/\s*100/);
assert.match(text,/Inventory complete/);
assert.match(text,/Last progress update/);
assert.doesNotMatch(text,/files checked/);
const flatten=n=>[n,...n.children.flatMap(flatten)];
const bar=flatten(app).find(n=>n.tagName==='progress');
assert.equal(bar.value,25); assert.equal(bar.max,100);
const saving=render({phase:'Saving results',processed:2048,total:null,unit:'bytes',
  files_scanned:100,bytes_scanned:2048,phase_started_at:now,updated_at:now});
assert.match(saving,/2\.0 KiB/);
assert.match(saving,/data processed/);
assert.equal(flatten(app).find(n=>n.tagName==='progress').value,undefined);
const inventory=render({phase:'Inventory',files_scanned:10,bytes_scanned:1024});
assert.match(inventory,/files checked/);
assert.doesNotMatch(inventory,/Inventory complete/);
console.log('Widget stage progress checks passed');
