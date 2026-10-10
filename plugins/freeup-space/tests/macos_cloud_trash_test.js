// Run the real JXA logic against a controlled Foundation boundary.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const script = require('node:fs').readFileSync(0, 'utf8');

function scenario(files, action = 'prepare', options = {}) {
  let now = 0;
  const downloaded = [], trashed = [];
  const $ = value => value;
  for (const key of ['NSURLIsUbiquitousItemKey', 'NSURLIsDirectoryKey',
    'NSURLIsSymbolicLinkKey', 'NSURLUbiquitousItemDownloadingStatusKey',
    'NSURLUbiquitousItemDownloadingErrorKey', 'NSURLFileSizeKey',
    'NSURLUbiquitousItemDownloadingStatusNotDownloaded',
    'NSURLUbiquitousItemDownloadingStatusDownloaded',
    'NSURLUbiquitousItemDownloadingStatusCurrent', 'NSFileSystemFreeSize']) $[key] = key;
  const dictionary = values => ({objectForKey: key => values[key]});
  function url(path) {
    return {path, resourceValuesForKeysError() {
      const f = files[path];
      if (!f) throw Error('missing');
      return dictionary({
        NSURLIsUbiquitousItemKey: !!f.cloud,
        NSURLIsDirectoryKey: !!f.children,
        NSURLIsSymbolicLinkKey: !!f.link,
        NSURLFileSizeKey: f.size || 10,
        NSURLUbiquitousItemDownloadingStatusKey: f.cloud ?
          (f.local ? $.NSURLUbiquitousItemDownloadingStatusCurrent :
            $.NSURLUbiquitousItemDownloadingStatusNotDownloaded) : undefined,
        NSURLUbiquitousItemDownloadingErrorKey: f.error ? {localizedDescription:f.error} : undefined,
      });
    }};
  }
  $.NSURL = {fileURLWithPath:url};
  $.NSProcessInfo = {processInfo:{get systemUptime() {return now;}}};
  $.NSThread = {sleepForTimeInterval(seconds) {
    now += seconds;
    if (!options.stall) for (const path of downloaded) files[path].local = true;
  }};
  $.NSFileManager = {defaultManager:{
    startDownloadingUbiquitousItemAtURLError(u, error) {
      if (options.offline) return false;
      downloaded.push(u.path); return true;
    },
    attributesOfFileSystemForPathError() {
      return dictionary({NSFileSystemFreeSize:options.freeBytes ?? 1e12});
    },
    contentsOfDirectoryAtURLIncludingPropertiesForKeysOptionsError(u) {
      const children = (files[u.path].children || []).map(url);
      return {count:children.length, objectAtIndex:i=>children[i]};
    },
    trashItemAtURLResultingItemURLError(u, result, error) {
      if (options.trashError) return false;
      trashed.push(u.path); return true;
    },
  }};
  const context = { $, ObjC:{import(){}, unwrap:v=>v, deepUnwrap:v=>v}, Ref:()=>[], JSON, Number, String, Error };
  vm.createContext(context);
  vm.runInContext(script, context);
  const result = JSON.parse(context.run([action, '/fixture', '2']));
  return {result, downloaded, trashed};
}

let r = scenario({'/fixture':{cloud:true}});
assert.equal(r.result.ok, true);
assert.deepEqual(r.downloaded, ['/fixture']);
assert.deepEqual(r.trashed, []);
r = scenario({'/fixture':{cloud:true}}, 'prepare', {stall:true});
assert.equal(r.result.ok, false);
assert.match(r.result.error, /timed out/i);
assert.deepEqual(r.trashed, []);
r = scenario({'/fixture':{cloud:true}}, 'prepare', {offline:true});
assert.equal(r.result.ok, false);
assert.match(r.result.error, /Could not start iCloud download/);
r = scenario({'/fixture':{cloud:true}}, 'prepare', {freeBytes:1});
assert.equal(r.result.ok, false);
assert.deepEqual(r.downloaded, []);
r = scenario({'/fixture':{cloud:true,local:true}});
assert.equal(r.result.ok, true);
assert.deepEqual(r.downloaded, []);
r = scenario({'/fixture':{}});
assert.equal(r.result.ok, true);
assert.deepEqual(r.downloaded, []);
r = scenario({'/fixture':{children:['/fixture/child']}, '/fixture/child':{cloud:true}});
assert.equal(r.result.ok, true);
assert.deepEqual(r.downloaded, ['/fixture/child']);
r = scenario({'/fixture':{link:true}});
assert.equal(r.result.ok, false);
r = scenario({'/fixture':{}}, 'trash');
assert.equal(r.result.ok, true);
assert.deepEqual(r.trashed, ['/fixture']);
r = scenario({'/fixture':{}}, 'trash', {trashError:true});
assert.equal(r.result.ok, false);
assert.match(r.result.error, /could not move item to Trash/);
console.log('10 native cloud/trash scenarios passed');
