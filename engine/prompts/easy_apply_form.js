// Job Hunter — Easy Apply step helper (paste once per job page with javascript_tool; it stays on window).
// window.__jh.read()        -> compact list of the fields on the current step: "k|type|req|label|options|value"
// window.__jh.fill({k: v})  -> fills them all in one go; returns "k:ok" / "k:miss(...)" per field
// window.__jh.next()        -> clicks Next / Continue / Review (NEVER Submit); returns what it clicked
// window.__jh.page(n)       -> next chunk of read() when the list was cut ("more" in the result)
(() => {
  function* roots(r) { yield r; for (const el of r.querySelectorAll('*')) { if (el.shadowRoot) yield* roots(el.shadowRoot);
    if (el.tagName === 'IFRAME') { try { if (el.contentDocument) yield* roots(el.contentDocument); } catch (e) {} } } }
  const T = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  function modal() {
    for (const r of roots(document)) {
      const d = [...r.querySelectorAll('[role="dialog"], .jobs-easy-apply-modal, .artdeco-modal')]
        .find(x => vis(x) && /easy apply|apply to|submit application|review your application|contact info|resume|additional questions|work authorization/i.test(x.innerText || ''));
      if (d) return d;
    }
    return null;
  }
  function labelOf(el, root) {
    const id = el.id;
    let t = '';
    if (id) { const l = root.querySelector('label[for="' + CSS.escape(id) + '"]'); if (l) t = T((l.querySelector('[aria-hidden="true"]') || l).innerText); }
    if (!t && el.getAttribute('aria-label')) t = T(el.getAttribute('aria-label'));
    if (!t && el.getAttribute('aria-labelledby')) t = T(el.getAttribute('aria-labelledby').split(/\s+/).map(i => (root.getElementById ? root.getElementById(i) : document.getElementById(i))?.innerText || '').join(' '));
    if (!t) { const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend'); if (lg) t = T((lg.querySelector('[aria-hidden="true"]') || lg).innerText); }
    if (!t) { const g = el.closest('[class*="form-section__grouping"], [class*="form-component"], .fb-dash-form-element, div'); if (g) t = T((g.querySelector('label, legend, span') || {}).innerText); }
    return t.replace(/\*$/, '').trim();
  }
  const H = window.__jh = window.__jh || {};
  H.items = []; H.lines = [];
  H.read = function () {
    const m = modal();
    if (!m) return 'NO_MODAL';
    const root = m.getRootNode();
    H.items = []; H.lines = [];
    const seenGroups = new Set();
    let n = 0;
    for (const el of m.querySelectorAll('input, select, textarea')) {
      const tp = (el.type || el.tagName).toLowerCase();
      if (['hidden', 'file', 'submit', 'button', 'search'].includes(tp) || el.disabled || (!vis(el) && tp !== 'radio' && tp !== 'checkbox')) continue;
      let item;
      if (tp === 'radio' || (tp === 'checkbox' && el.closest('fieldset'))) {
        const fs = el.closest('fieldset') || el.parentElement;
        if (seenGroups.has(fs)) continue;
        seenGroups.add(fs);
        const opts = [...fs.querySelectorAll('input[type="' + tp + '"]')];
        item = { el: fs, kind: tp === 'radio' ? 'radio' : 'multi', opts: opts.map(o => ({ el: o, t: labelOf(o, root) || T(o.value) })) };
        item.label = (() => { const lg = fs.querySelector('legend'); return lg ? T((lg.querySelector('[aria-hidden="true"]') || lg).innerText).replace(/\*$/, '') : labelOf(opts[0], root); })();
        item.req = !!(fs.querySelector('[required],[aria-required="true"]') || /\*\s*$/.test(T(fs.querySelector('legend')?.innerText)));
        item.val = item.opts.filter(o => o.el.checked).map(o => o.t).join('; ');
      } else if (tp === 'checkbox') {
        item = { el, kind: 'checkbox', label: labelOf(el, root), req: el.required || el.getAttribute('aria-required') === 'true', val: el.checked ? 'checked' : '' };
      } else if (el.tagName === 'SELECT') {
        item = { el, kind: 'choice', label: labelOf(el, root), req: el.required || el.getAttribute('aria-required') === 'true',
          opts: [...el.options].filter(o => o.value && !/^select an option$/i.test(T(o.text))).map(o => ({ el: o, t: T(o.text) })),
          val: el.selectedIndex > 0 ? T(el.options[el.selectedIndex].text) : '' };
        if (/^select an option$/i.test(item.val)) item.val = '';
      } else {
        const combo = el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete');
        item = { el, kind: combo ? 'typeahead' : (el.tagName === 'TEXTAREA' ? 'textarea' : (tp === 'number' || el.inputMode === 'numeric' || /numeric/.test(el.id) ? 'number' : (tp === 'date' ? 'date' : 'text'))),
          label: labelOf(el, root), req: el.required || el.getAttribute('aria-required') === 'true', val: T(el.value) };
      }
      item.k = 'f' + (++n);
      H.items.push(item);
      const o = item.opts ? item.opts.map(x => x.t).join('/') : '';
      H.lines.push([item.k, item.kind, item.req ? 'req' : '', item.label.slice(0, 90), o.slice(0, 90), (item.val || '').slice(0, 30)].join('|'));
    }
    const step = T((m.querySelector('h3, h2') || {}).innerText).slice(0, 40);
    const pct = T((m.querySelector('[role="progressbar"], progress') || {}).getAttribute?.('aria-valuenow') || '');
    const btn = [...m.querySelectorAll('button')].filter(vis).map(b => T(b.getAttribute('aria-label') || b.innerText)).filter(t => /next|continue|review|submit/i.test(t)).slice(-1)[0] || '';
    H.head = 'step:' + step + (pct ? ' ' + pct + '%' : '') + ' | button:' + btn + ' | fields:' + H.lines.length;
    return H.page(0);
  };
  H.page = function (i) {
    const out = [H.head]; let len = H.head.length, j = i;
    for (; j < H.lines.length; j++) { if (len + H.lines[j].length > 900) break; out.push(H.lines[j]); len += H.lines[j].length + 1; }
    if (j < H.lines.length) out.push('more: __jh.page(' + j + ')');
    return out.join('\n');
  };
  const nativeSet = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : (el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype);
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    for (const e of ['input', 'change', 'blur']) el.dispatchEvent(new Event(e, { bubbles: true }));
  };
  const pick = (opts, v) => {
    const s = T(String(v)).toLowerCase();
    return opts.find(o => o.t.toLowerCase() === s) || opts.find(o => o.t.toLowerCase().startsWith(s)) ||
      opts.find(o => s && o.t.toLowerCase().includes(s)) || null;
  };
  H.fill = function (map) {
    const res = [];
    for (const [k, v] of Object.entries(map || {})) {
      const it = H.items.find(x => x.k === k);
      if (!it) { res.push(k + ':miss(no field, run read)'); continue; }
      try {
        if (it.kind === 'radio') {
          const o = pick(it.opts, v); if (!o) { res.push(k + ':miss(no option ' + String(v).slice(0, 20) + ')'); continue; }
          (o.el.labels && o.el.labels[0] ? o.el.labels[0] : o.el).click();
        } else if (it.kind === 'multi') {
          const want = String(v).split(/;\s*/);
          for (const w of want) { const o = pick(it.opts, w); if (o && !o.el.checked) (o.el.labels && o.el.labels[0] ? o.el.labels[0] : o.el).click(); }
        } else if (it.kind === 'checkbox') {
          const on = /^(true|yes|checked|1)$/i.test(String(v));
          if (on !== it.el.checked) (it.el.labels && it.el.labels[0] ? it.el.labels[0] : it.el).click();
        } else if (it.kind === 'choice') {
          const o = pick(it.opts, v); if (!o) { res.push(k + ':miss(no option ' + String(v).slice(0, 20) + ')'); continue; }
          nativeSet(it.el, o.el.value);
        } else if (it.kind === 'typeahead') {
          it.el.focus(); nativeSet(it.el, String(v)); res.push(k + ':typed(pick a suggestion)'); continue;
        } else {
          nativeSet(it.el, String(v));
        }
        res.push(k + ':ok');
      } catch (e) { res.push(k + ':miss(' + String(e).slice(0, 40) + ')'); }
    }
    const errs = [...(modal() || document).querySelectorAll('[role="alert"], .artdeco-inline-feedback--error')].filter(vis).map(e => T(e.innerText)).filter(Boolean);
    return res.join(' ') + (errs.length ? ' | errors: ' + errs.join(' / ').slice(0, 200) : '');
  };
  H.next = function () {
    const m = modal(); if (!m) return 'NO_MODAL';
    const btns = [...m.querySelectorAll('button')].filter(vis);
    const b = btns.find(x => /continue to next step|^next$|^continue$/i.test(T(x.getAttribute('aria-label') || x.innerText))) ||
              btns.find(x => /review your application|^review$/i.test(T(x.getAttribute('aria-label') || x.innerText)));
    if (!b) return 'NO_NEXT (on the final step: ' + btns.map(x => T(x.innerText)).filter(Boolean).join('/').slice(0, 80) + ')';
    const t = T(b.getAttribute('aria-label') || b.innerText);
    if (/submit|send|apply$/i.test(t)) return 'REFUSED: ' + t;      // never submit from the prefill lane
    b.click();
    return 'clicked: ' + t;
  };
  return 'ready';
})();
