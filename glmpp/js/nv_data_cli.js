#!/usr/bin/env node
/**
 * Pure-HTTP data field builder using extracted pe.059 nv().
 * Usage: nv_data_cli.js '{"TrackList":...}' [cookie]
 * or: echo json | nv_data_cli.js
 */
const fs = require('fs');
const vm = require('vm');
const crypto = require('crypto');
const path = require('path');

const PE_PATH = process.env.PE_EXPORT ||
  (require('fs').existsSync('/tmp/zai_js/pe.059.export.js') ? '/tmp/zai_js/pe.059.export.js' :
   require('path').join(__dirname, 'pe.059.export.js'));
const CJS_PATH = process.env.CRYPTOJS ||
  (require('fs').existsSync('/tmp/crypto-js.min.js') ? '/tmp/crypto-js.min.js' :
   require('path').join(__dirname, 'crypto-js.min.js'));

function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(), nodeType: 1, style: {},
    children: [], childNodes: [], attributes: {},
    classList: { add(){}, remove(){}, toggle(){}, contains:()=>false },
    dataset: {}, innerHTML: '', textContent: '', innerText: '',
    parentNode: null,
    appendChild(c){ this.children.push(c); if(c) c.parentNode=this; return c; },
    removeChild(c){ return c; }, insertBefore(c){ return c; }, remove(){},
    setAttribute(k,v){ this.attributes[k]=v; }, getAttribute(k){ return this.attributes[k]??null; },
    removeAttribute(k){ delete this.attributes[k]; }, hasAttribute(k){ return k in this.attributes; },
    addEventListener(){}, removeEventListener(){}, dispatchEvent(){ return true; },
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    getBoundingClientRect(){ return {top:0,left:0,right:0,bottom:0,width:0,height:0,x:0,y:0}; },
    focus(){}, blur(){}, click(){}, cloneNode(){ return makeEl(tag); },
    contains(){ return false; }, getElementsByTagName(){ return []; },
    getElementsByClassName(){ return []; }, matches(){ return false; },
    closest(){ return null; }, insertAdjacentHTML(){},
    clientWidth:1920, clientHeight:1080, offsetWidth:0, offsetHeight:0,
    value:'', checked:false, disabled:false, type:'', href:'', src:'',
  };
  if (String(tag).toLowerCase()==='style') el.sheet = { insertRule(){}, cssRules:[], deleteRule(){} };
  if (String(tag).toLowerCase()==='head') {
    el.appendChild = (c) => c || makeEl('style');
    el.insertBefore = (c) => c || makeEl('style');
  }
  return el;
}

function buildSandbox(cookie) {
  const sandbox = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    TextEncoder, TextDecoder, Buffer, process,
    atob: s => Buffer.from(s,'base64').toString('binary'),
    btoa: s => Buffer.from(s,'binary').toString('base64'),
    encodeURIComponent, decodeURIComponent, escape, unescape,
    parseInt, parseFloat, isNaN, isFinite, JSON, Math, Date,
    Object, Array, String, Number, Boolean, RegExp, Error, TypeError, RangeError,
    Promise, Symbol, Map, Set, WeakMap, WeakSet, Proxy, Reflect,
    Uint8Array, Uint32Array, Int32Array, ArrayBuffer, DataView,
    performance: { now: () => Date.now() },
    crypto: {
      getRandomValues(arr){ crypto.randomFillSync(arr); return arr; },
      subtle: undefined,
      randomUUID: () => crypto.randomUUID(),
    },
    navigator: {
      userAgent: 'Mozilla/5.0 (X11; Linux x86_64; rv:136.0) Gecko/20100101 Firefox/136.0',
      language:'en-US', languages:['en-US'], platform:'Linux x86_64',
      hardwareConcurrency:8, deviceMemory:8, maxTouchPoints:0,
      webdriver:false, cookieEnabled:true,
      plugins:{ length:0, item:()=>null, namedItem:()=>null, refresh(){} },
      mimeTypes:{ length:0 },
      connection:{ effectiveType:'4g', rtt:50, downlink:10, addEventListener(){}, removeEventListener(){} },
      sendBeacon:()=>true, vibrate:()=>true,
    },
    screen: { width:1920, height:1080, availWidth:1920, availHeight:1040, colorDepth:24, pixelDepth:24, orientation:{type:'landscape-primary',angle:0} },
    location: { href:'https://chat.z.ai/', origin:'https://chat.z.ai', protocol:'https:', host:'chat.z.ai', hostname:'chat.z.ai', pathname:'/', search:'', hash:'', assign(){}, replace(){}, reload(){} },
    history: { pushState(){}, replaceState(){}, go(){}, back(){}, forward(){} },
    localStorage: { getItem:()=>null, setItem(){}, removeItem(){}, clear(){}, key:()=>null, length:0 },
    sessionStorage: { getItem:()=>null, setItem(){}, removeItem(){}, clear(){}, key:()=>null, length:0 },
    Image: function(){ this.width=0; this.height=0; this.complete=false; },
    XMLHttpRequest: function(){ this.open=()=>{}; this.send=()=>{}; this.setRequestHeader=()=>{}; this.abort=()=>{}; this.getAllResponseHeaders=()=>''; this.getResponseHeader=()=>null; },
    fetch: () => Promise.reject(new Error('no fetch')),
    MutationObserver: function(){ this.observe=()=>{}; this.disconnect=()=>{}; this.takeRecords=()=>[]; },
    ResizeObserver: function(){ this.observe=()=>{}; this.disconnect=()=>{}; },
    IntersectionObserver: function(){ this.observe=()=>{}; this.disconnect=()=>{}; },
    requestAnimationFrame: cb => setTimeout(()=>cb(Date.now()), 16),
    cancelAnimationFrame: id => clearTimeout(id),
    getComputedStyle: () => new Proxy({}, { get:(t,p)=> p==='getPropertyValue'?()=>'' : (p==='length'?0:'') }),
    matchMedia: () => ({ matches:false, media:'', addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){}, dispatchEvent:()=>true }),
    CSS: { supports:()=>false, escape:s=>s },
    Node: { ELEMENT_NODE:1, TEXT_NODE:3, COMMENT_NODE:8, DOCUMENT_NODE:9, prototype: {} },
    NodeList: { prototype: { forEach: Array.prototype.forEach } },
    HTMLElement: function(){}, Element: function(){},
    NodeFilter: { SHOW_ELEMENT:1, SHOW_TEXT:4, acceptNode:()=>1 },
    CustomEvent: function(){}, Event: function(){},
    WheelEvent: function(){}, MouseEvent: function(){}, KeyboardEvent: function(){},
    FocusEvent: function(){}, PointerEvent: function(){},
    EventSource: function(){ this.close=()=>{}; },
    WebSocket: function(){ this.close=()=>{}; this.send=()=>{}; },
    Blob: function(){}, File: function(){},
    FileReader: function(){ this.readAsDataURL=()=>{}; this.readAsText=()=>{}; },
    FormData: function(){ this.append=()=>{}; this.get=()=>null; this.has=()=>false; },
    URL: { createObjectURL:()=>'blob:x', revokeObjectURL(){}, URL: function(){ this.toString=()=>'https://chat.z.ai/'; } },
    DOMParser: function(){ this.parseFromString=()=>({ body:makeEl('body'), documentElement:makeEl('html'), head:makeEl('head') }); },
    getSelection: () => ({ rangeCount:0, toString:()=>'', removeAllRanges(){}, addRange(){} }),
    innerWidth:1920, innerHeight:1080, outerWidth:1920, outerHeight:1080, devicePixelRatio:1,
    pageXOffset:0, pageYOffset:0, scrollX:0, scrollY:0,
    isSecureContext:true, crossOriginIsolated:false,
    addEventListener(){}, removeEventListener(){}, dispatchEvent(){ return true; },
    focus(){}, blur(){}, open(){}, close(){}, print(){}, stop(){},
    origin:'https://chat.z.ai', name:'', status:0, closed:false,
    Attr: function(){}, Audio: function(){}, Screen: function(){},
    blur: ()=>{}, moveBy: ()=>{},
  };

  sandbox.document = {
    cookie: cookie || '_c_WBKFRo=placeholder',
    referrer: 'https://chat.z.ai/', title: 'Z.ai',
    readyState: 'complete', hidden: false, visibilityState: 'visible',
    documentElement: Object.assign(makeEl('html'), { clientWidth:1920, clientHeight:1080 }),
    head: makeEl('head'),
    body: Object.assign(makeEl('body'), { clientWidth:1920, clientHeight:1080 }),
    createElement: (tag) => makeEl(tag),
    createTextNode: t => ({ nodeType:3, textContent:t }),
    createDocumentFragment: () => makeEl('#fragment'),
    createComment: t => ({ nodeType:8, textContent:t }),
    getElementById: () => null,
    getElementsByTagName: () => [],
    getElementsByClassName: () => [],
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener(){}, removeEventListener(){},
    dispatchEvent(){ return true; },
    write(){}, writeln(){},
    fonts: { ready: Promise.resolve(), addEventListener(){}, removeEventListener(){} },
    styleSheets: [],
    createRange: () => ({ setStart(){}, setEnd(){}, commonAncestorContainer:null, getBoundingClientRect:()=>({width:0,height:0,top:0,left:0}) }),
    defaultView: null,
    location: null,
    activeElement: null,
    implementation: { createHTMLDocument: () => sandbox.document },
  };
  sandbox.document.defaultView = sandbox;
  sandbox.document.location = sandbox.location;
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.top = sandbox;
  sandbox.parent = sandbox;
  sandbox.frames = sandbox;

  try {
    const C = require(CJS_PATH);
    const CJS = C.CryptoJS || C;
    sandbox.__ALIYUN_CRYPT = CJS;
    sandbox.CryptoJS = CJS;
  } catch(e) {
    // non-fatal
  }

  sandbox.document.head.appendChild = function(c) { return c || makeEl('style'); };
  sandbox.document.head.insertBefore = function(c) { return c || makeEl('style'); };
  sandbox.document.body.appendChild = function(c) { return c || makeEl('div'); };
  return sandbox;
}

let cached = null;
function loadPe(cookie) {
  if (cached && cached.__nv) {
    // update cookie if changed
    try { cached.document.cookie = cookie || cached.document.cookie; } catch(e) {}
    return cached;
  }
  const src = fs.readFileSync(PE_PATH, 'utf8');
  const sandbox = buildSandbox(cookie);
  const context = vm.createContext(sandbox);
  const origErr = console.error;
  console.error = () => {};
  try {
    vm.runInContext(src, context, { filename: path.basename(PE_PATH), timeout: 20000 });
  } catch(e) {
    if (typeof sandbox.__nv !== 'function') {
      console.error = origErr;
      throw new Error('PE load failed: ' + e.message);
    }
  } finally {
    console.error = origErr;
  }
  if (typeof sandbox.__nv !== 'function') {
    throw new Error('__nv not exported');
  }
  cached = sandbox;
  return sandbox;
}

function buildData(payload, cookie) {
  const sb = loadPe(cookie);
  // Warm up pipeline helpers that live capture calls before nv
  try {
    if (typeof sb.__nR === 'function') sb.__nR({mc:[],tc:[],mu:[],te:[],mp:[],tmv:[],ks:[],fi:[],startTime: Date.now()});
    if (typeof sb.__nF === 'function') sb.__nF({mc:[],tc:[],mu:[],te:[],mp:[],tmv:[],ks:[],fi:[],startTime: Date.now()});
    if (typeof sb.__t0 === 'function') sb.__t0('');
    if (typeof sb.__t7 === 'function') sb.__t7('_c_WBKFRo');
    if (typeof sb.__tK === 'function') sb.__tK(payload && payload.certifyId || 'aBEWfVU8v0');
    if (typeof sb.__t_ === 'function') sb.__t_('3795d28242a11619bc25f786f84e53d4');
  } catch(e) { /* warmup best-effort */ }

  const out = sb.__nv.call(sb, payload);
  if (typeof out !== 'string' || out.length < 20) {
    throw new Error('nv returned ' + typeof out + ' len=' + (out && out.length));
  }
  return out;
}

function main() {
  let arg = process.argv[2];
  let cookie = process.env.ZAI_COOKIE || '';
  if (!arg) {
    arg = fs.readFileSync(0, 'utf8').trim();
  }
  // allow cookie as second arg
  if (process.argv[3]) cookie = process.argv[3];
  if (arg.startsWith('{') && arg.includes('"payload"')) {
    const o = JSON.parse(arg);
    if (o.cookie) cookie = o.cookie;
    arg = JSON.stringify(o.payload);
  }
  const payload = JSON.parse(arg);
  const data = buildData(payload, cookie);
  const raw = Buffer.from(data, 'base64');
  const result = {
    data,
    rawLen: raw.length,
    magic: raw.slice(0, 5).toString('hex'),
    arg: payload.arg || null,
  };
  process.stdout.write(JSON.stringify(result));
  process.exit(0);
}

if (require.main === module) {
  try { main(); }
  catch(e) {
    process.stderr.write(String(e && e.stack || e));
    process.exit(1);
  }
}

module.exports = { buildData, loadPe };
