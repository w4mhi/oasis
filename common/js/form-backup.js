/*
 * form-backup.js — "save to server" client helper for OASIS forms + net log.
 *
 * Backs the server-side backup store (POST /api/forms/save, GET /api/forms/list,
 * restore by fetching /static/<kind>/saved/<name>). ICS forms and the net-check-in
 * log otherwise live only in browser localStorage; this lets an operator persist a
 * snapshot onto the device so a cleared cache or a swapped tablet doesn't lose
 * operational records.
 *
 * Self-contained: the restore picker and the toast inject their own inline-styled
 * DOM, so any page can wire save/restore with a few lines and no extra CSS. Classic
 * <script src="/common/js/form-backup.js">; pure helpers (stamp/slug/filenameFor)
 * are also requireable by node --test.
 */
(function (root, factory) {
  var api = factory();
  root.OasisFormBackup = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // ── Pure helpers (node-testable) ───────────────────────────────────────────
  function stamp(d) {
    d = d || new Date();
    var z = function (n) { return String(n).padStart(2, '0'); };
    return '' + d.getFullYear() + z(d.getMonth() + 1) + z(d.getDate()) +
      '-' + z(d.getHours()) + z(d.getMinutes());
  }
  function slug(s, fallback) {
    s = (s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
    return s || (fallback || 'snapshot');
  }
  function filenameFor(kind, label) {
    return kind + '-' + slug(label, 'snapshot') + '-' + stamp() + '.json';
  }
  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function _kb(bytes) {
    if (bytes == null) return '';
    return bytes < 1024 ? bytes + ' B' : (bytes / 1024).toFixed(1) + ' KB';
  }
  function _when(mtime) {
    if (!mtime) return '';
    var d = new Date(mtime * 1000);
    if (isNaN(d)) return '';
    var z = function (n) { return String(n).padStart(2, '0'); };
    return d.getFullYear() + '-' + z(d.getMonth() + 1) + '-' + z(d.getDate()) +
      ' ' + z(d.getHours()) + ':' + z(d.getMinutes());
  }

  // ── Server calls ───────────────────────────────────────────────────────────
  async function save(kind, filename, contentStr) {
    var res = await fetch('/api/forms/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: kind, filename: filename, content: contentStr })
    });
    var j = await res.json().catch(function () { return {}; });
    if (!res.ok || !j.ok) throw new Error(j.error || ('HTTP ' + res.status));
    return j; // { ok, saved }
  }
  async function list(kind) {
    var res = await fetch('/api/forms/list?kind=' + encodeURIComponent(kind), { cache: 'no-store' });
    var j = await res.json().catch(function () { return {}; });
    if (!res.ok || !j.ok) throw new Error(j.error || ('HTTP ' + res.status));
    return j.files || [];
  }
  async function load(kind, name) {
    var res = await fetch('/static/' + encodeURIComponent(kind) + '/saved/' +
      encodeURIComponent(name), { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  }

  // ── Self-contained toast ───────────────────────────────────────────────────
  function toast(msg, isErr) {
    var el = document.createElement('div');
    el.textContent = msg;
    el.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);' +
      'z-index:100000;padding:9px 16px;border-radius:6px;font:14px system-ui,sans-serif;' +
      'color:#fff;box-shadow:0 4px 16px rgba(0,0,0,.4);max-width:80vw;' +
      'background:' + (isErr ? 'var(--red,#c0392b)' : 'var(--green,#2f9e44)') + ';';
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, isErr ? 6000 : 3500);
  }

  // ── Restore picker (injected overlay) ──────────────────────────────────────
  function openPicker(kind, onLoad, opts) {
    opts = opts || {};
    var ov = document.createElement('div');
    ov.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.6);' +
      'display:flex;align-items:center;justify-content:center;';
    var box = document.createElement('div');
    box.style.cssText = 'background:var(--panel,#161b22);color:var(--text,#e6edf3);' +
      'border:1px solid var(--border,#30363d);border-radius:8px;width:min(520px,92vw);' +
      'max-height:80vh;display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,.5);' +
      'font:14px system-ui,sans-serif;';
    box.innerHTML =
      '<div style="padding:12px 16px;border-bottom:1px solid var(--border,#30363d);' +
      'display:flex;align-items:center;gap:8px;"><strong style="flex:1;">' +
      _esc(opts.title || 'Restore from server') + '</strong>' +
      '<button data-fb-close style="background:none;border:1px solid var(--border,#30363d);' +
      'color:inherit;border-radius:5px;padding:2px 9px;cursor:pointer;">✕</button></div>' +
      '<div data-fb-list style="overflow-y:auto;padding:8px;">Loading…</div>';
    ov.appendChild(box);
    document.body.appendChild(ov);
    var close = function () { ov.remove(); };
    ov.addEventListener('click', function (e) { if (e.target === ov) close(); });
    box.querySelector('[data-fb-close]').addEventListener('click', close);
    var listEl = box.querySelector('[data-fb-list]');
    list(kind).then(function (files) {
      if (!files.length) {
        listEl.innerHTML = '<div style="padding:14px;color:var(--text-dim,#8b949e);">' +
          'No saved snapshots yet. Use “Save to server” first.</div>';
        return;
      }
      listEl.innerHTML = '';
      files.forEach(function (f) {
        var row = document.createElement('button');
        row.style.cssText = 'display:flex;width:100%;text-align:left;gap:10px;align-items:baseline;' +
          'background:none;border:0;border-bottom:1px solid var(--border-dim,rgba(255,255,255,.06));' +
          'color:inherit;padding:9px 8px;cursor:pointer;font:inherit;';
        row.onmouseenter = function () { row.style.background = 'var(--panel-2,rgba(255,255,255,.05))'; };
        row.onmouseleave = function () { row.style.background = 'none'; };
        row.innerHTML = '<span style="flex:1;word-break:break-all;">' + _esc(f.name) + '</span>' +
          '<span style="color:var(--text-dim,#8b949e);white-space:nowrap;">' +
          _esc([_when(f.mtime), _kb(f.size)].filter(Boolean).join(' · ')) + '</span>';
        row.onclick = function () { close(); onLoad(f.name); };
        listEl.appendChild(row);
      });
    }).catch(function (err) {
      listEl.innerHTML = '<div style="padding:14px;color:var(--red,#f85149);">' +
        'Could not list snapshots: ' + _esc(err.message) + '</div>';
    });
  }

  // ── One-call wiring for a page ─────────────────────────────────────────────
  // opts: { kind, saveButton, restoreButton, getData()->obj|null, applyData(obj),
  //         label?:string|fn, hasData?:()->bool, noun?:string }
  function attach(opts) {
    var kind = opts.kind;
    var noun = opts.noun || 'form';
    if (opts.saveButton) {
      opts.saveButton.addEventListener('click', async function () {
        var data;
        try { data = opts.getData(); } catch (e) { data = null; }
        if (data == null) { toast('Nothing to save yet', true); return; }
        var label = typeof opts.label === 'function' ? opts.label() : opts.label;
        var filename = filenameFor(kind, label);
        try {
          var j = await save(kind, filename, JSON.stringify(data, null, 2));
          toast('✓ Saved to ' + (j.saved || filename));
        } catch (err) { toast('Save failed: ' + err.message, true); }
      });
    }
    if (opts.restoreButton) {
      opts.restoreButton.addEventListener('click', function () {
        openPicker(kind, async function (name) {
          var obj;
          try { obj = await load(kind, name); }
          catch (err) { toast('Load failed: ' + err.message, true); return; }
          if (!obj || typeof obj !== 'object') { toast('That file is not a valid ' + noun, true); return; }
          if (opts.hasData && opts.hasData() &&
              !confirm('Restore "' + name + '"? This replaces the current ' + noun + '.')) return;
          try { opts.applyData(obj); toast('✓ Restored ' + name); }
          catch (err) { toast('Restore failed: ' + err.message, true); }
        }, { title: 'Restore ' + noun + ' from server' });
      });
    }
  }

  return {
    stamp: stamp, slug: slug, filenameFor: filenameFor,
    save: save, list: list, load: load,
    toast: toast, openPicker: openPicker, attach: attach
  };
});
