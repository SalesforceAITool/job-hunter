/* Job Hunter — single-page ops console. Vanilla JS, no dependencies, no external requests.
   Contract: SPEC.md §5 (events), §6 (API), §11 (UI), §14 (portal queue).
   Everything is defensive: missing fields render as "—", failed requests show a calm toast. */
(function () {
  'use strict';

  /* ═════════════════════════ Constants ═════════════════════════ */

  const LANES = [
    { id: 'followups', name: 'Follow-ups & replies', group: 'alongside',
      counters: [['nudges', 'nudges'], ['replies', 'replies'], ['bounces', 'bounces'], ['nudges_skipped', 'nudges skipped']],
      idle: 'Checks Gmail for replies and bounces, then sends due nudges' },
    { id: 'outreach', name: 'Outreach', group: 'alongside',
      counters: [['sent', 'sent'], ['dry_run', 'dry run'], ['failed', 'failed']],
      idle: 'Sends a tailored resume as soon as a job qualifies' },
    { id: 'posts', name: 'LinkedIn posts', group: 'seq',
      counters: [['found', 'found'], ['qualified', 'qualified'], ['sent', 'sent'], ['dry_run', 'dry run'], ['skipped', 'skipped']],
      idle: 'Searches hiring posts for emails' },
    { id: 'jobright', name: 'JobRight', group: 'seq',
      counters: [['found', 'found'], ['qualified', 'qualified'], ['sent', 'sent'], ['dry_run', 'dry run'], ['skipped', 'skipped']],
      idle: 'US remote Salesforce roles via insider connections' },
    { id: 'linkedin_jobs', name: 'LinkedIn Jobs', group: 'seq', sub: 'US + Worldwide',
      counters: [['found', 'found'], ['qualified', 'qualified'], ['sent', 'sent'], ['dry_run', 'dry run'], ['skipped', 'skipped']],
      idle: 'Job lists, hiring team, SalesQL email' },
    { id: 'himalayas', name: 'Remote boards', group: 'seq', sub: 'Himalayas · Jobicy · HN · careers',
      counters: [['found', 'found'], ['qualified', 'qualified'], ['sent', 'sent'], ['dry_run', 'dry run'], ['skipped', 'skipped']],
      idle: 'Remote Salesforce jobs open to India from public job APIs (no AI to find them), then recruiter emails' },
    { id: 'agencies', name: 'Agencies', group: 'seq', sub: 'C2C pitch',
      counters: [['found', 'picked'], ['sent', 'sent'], ['dry_run', 'dry run'], ['skipped', 'no contact']],
      idle: 'Pitches staffing agencies that place Salesforce contractors with US clients (only when ticked)' },
    { id: 'easy_apply', name: 'LinkedIn Easy Apply', group: 'seq', sub: 'prefill',
      counters: [['ready', 'ready'], ['skipped', 'skipped']],
      idle: 'Fills Easy Apply forms up to Review, then waits for your approval' },
    { id: 'portal', name: 'Portal prefill', group: 'seq',
      counters: [['ready', 'ready'], ['skipped', 'skipped']],
      idle: 'Fills Greenhouse, Lever and Ashby forms, stops before Submit' },
    { id: 'board_apply', name: 'Job board apply', group: 'seq', sub: 'prefill',
      counters: [['ready', 'ready'], ['sent', 'emailed'], ['skipped', 'skipped']],
      idle: 'Fills the applications you pick on the Job board (portals, company sites, email), stops before Submit' },
    { id: 'submit', name: 'Submit', group: 'seq', sub: 'automatic',
      counters: [['submitted', 'submitted'], ['failed', 'not sent']],
      idle: 'Submits every fully filled application by itself, right after it is filled' },
  ];
  const LANE_BY_ID = Object.fromEntries(LANES.map(l => [l.id, l]));
  const SOURCE_LANES = ['posts', 'jobright', 'linkedin_jobs', 'himalayas'];
  const LANE_SHORT = { followups: 'Follow-ups', posts: 'Posts', jobright: 'JobRight', linkedin_jobs: 'LinkedIn Jobs', himalayas: 'Remote boards', agencies: 'Agencies', board_apply: 'Job board', jobboard: 'Job board', easy_apply: 'Easy Apply', outreach: 'Outreach', portal: 'Portal', submit: 'Submit', watch: 'Watch', run: 'Run' };
  const LANE_ALIAS = { watch: 'run', linkedin_us: 'linkedin_jobs', linkedin_world: 'linkedin_jobs', linkedin_india: 'linkedin_jobs', linkedin: 'linkedin_jobs', followup: 'followups' };

  const SOURCES = {
    posts: 'LinkedIn post', jobright: 'JobRight', linkedin_us: 'LinkedIn US',
    linkedin_world: 'LinkedIn Worldwide', linkedin_india: 'LinkedIn India', himalayas: 'Himalayas', jobicy: 'Jobicy', hn: 'Hacker News',
    careers: 'Career page', board: 'Job board', agency: 'Agency', import: 'History', manual: 'Manual',
  };
  const CHART_SOURCES = ['posts', 'jobright', 'linkedin_us', 'linkedin_world', 'linkedin_india', 'himalayas', 'jobicy', 'hn', 'careers', 'board', 'agency', 'manual'];
  // Stack order bottom → top: imported history sits underneath, quiet gray, so the live sources read on top.
  const CHART_SERIES = ['import'].concat(CHART_SOURCES);
  const LEGEND = { posts: 'Posts', jobright: 'JobRight', linkedin_us: 'LinkedIn US', linkedin_world: 'LinkedIn Worldwide', linkedin_india: 'LinkedIn India', himalayas: 'Himalayas', jobicy: 'Jobicy', hn: 'Hacker News', careers: 'Career page', board: 'Job board', agency: 'Agency', manual: 'Manual', import: 'History' };

  const STATUS = {
    sent: 'Sent', nudge1: 'Nudged 1', nudge2: 'Nudged 2', replied: 'Replied', bounced: 'Bounced',
    skipped: 'Skipped', dry_run: 'Dry run', failed: 'Failed', closed: 'Closed',
    pending: 'Pending', prefilled: 'Prefilled', needs_input: 'Needs your input', submitted: 'Submitted', abandoned: 'Abandoned',
  };

  const TABS = [
    { id: 'all', label: 'All' },
    { id: 'awaiting', label: 'Awaiting' },
    { id: 'replied', label: 'Replied' },
    { id: 'bounced', label: 'Bounced' },
    { id: 'skipped', label: 'Skipped' },
    { id: 'portal', label: 'Ready to submit' },
    { id: 'questions', label: 'Questions for you' },
  ];

  const REFRESH_ON = {
    email_sent: ['apps', 'charts'], email_failed: ['apps'], nudge_sent: ['apps'],
    reply_found: ['apps', 'charts'], bounce_found: ['apps'], job_skipped: ['skipped', 'charts'],
    job_qualified: ['charts'], job_found: ['charts'], portal_ready: ['portal'], portal_submitted: ['portal', 'watch'], watch_found: ['watch'],
    run_finished: ['summary', 'apps', 'charts', 'portal', 'skipped', 'questions'], lane_finished: ['summary'],
    run_started: ['summary'], contact_found: [],
  };

  const PAGE = 150;
  const FEED_MAX = 1500;

  /* ═════════════════════════ State ═════════════════════════ */

  const S = {
    summary: null,
    runId: null,
    running: false,
    stopping: false,
    runStatus: null,
    runStartedAt: null,
    lanes: {},
    lastEventId: 0,
    seen: new Set(),
    feedFilter: 'all',
    feedDebug: false,
    feedHover: false,
    feedStick: true,
    feedPending: 0,
    feedCounts: {},
    sendTimes: [],

    apps: null, skipped: null, portal: null, charts: null, questions: null, qCounts: null, qFilter: 'open', qEditing: null, qAdding: false,
    knownApps: null,
    tab: 'all', q: '', source: '', shown: PAGE,
    activeRow: null,

    settings: null, env: null,
    live: 'connecting',
  };

  /* ═════════════════════════ Utilities ═════════════════════════ */

  const $ = (sel, el) => (el || document).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));

  function h(tag, props) {
    const el = document.createElement(tag);
    if (props) {
      for (const k in props) {
        const v = props[k];
        if (v == null || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'dataset') Object.assign(el.dataset, v);
        else if (k === 'style' && typeof v === 'object') { for (const sk in v) { if (v[sk] == null) continue; if (sk.slice(0, 2) === '--') el.style.setProperty(sk, v[sk]); else el.style[sk] = v[sk]; } }
        else if (k === 'svg') el.innerHTML = v; // trusted static icon markup only
        else if (k === 'indeterminate') el.indeterminate = !!v;
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else if (v === true) el.setAttribute(k, '');
        else el.setAttribute(k, String(v));
      }
    }
    for (let i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) { kid.forEach(k => append(el, k)); return; }
    el.append(kid instanceof Node ? kid : String(kid));
  }
  const SVGNS = 'http://www.w3.org/2000/svg';
  function s(tag, attrs, kids) {
    const el = document.createElementNS(SVGNS, tag);
    if (attrs) for (const k in attrs) if (attrs[k] != null) el.setAttribute(k, attrs[k]);
    if (kids) kids.forEach(c => c && el.append(c));
    return el;
  }

  const ICON = {
    check: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.3l2.3 2.2 4.7-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    x: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 3l6 6M9 3L3 9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    dot: '<svg viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="2.5" fill="currentColor"/></svg>',
    pause: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M4 3v6M8 3v6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    dash: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 6h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    mail: '<svg class="banner-ico" viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="4.5" width="15" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M3 5.5l7 5 7-5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
    term: '<svg class="banner-ico" viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="3.5" width="15" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M6 8l2.5 2L6 12M10.5 12.5H14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    flask: '<svg class="banner-ico" viewBox="0 0 20 20" aria-hidden="true"><path d="M8 2.5h4M8.5 2.5v5L4 15.5a1.3 1.3 0 0 0 1.1 2h9.8a1.3 1.3 0 0 0 1.1-2l-4.5-8v-5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M6.2 12.5h7.6" stroke="currentColor" stroke-width="1.5"/></svg>',
    hand: '<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 4.8v3.7M8 10.7v.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    ext: '<svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M9 3h4v4M13 3L7.5 8.5M11.5 9.5V12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1h2.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  function get(obj, path, dflt) {
    if (!obj) return dflt;
    const parts = path.split('.');
    let cur = obj;
    for (const p of parts) {
      if (cur == null || typeof cur !== 'object' || !(p in cur)) return dflt;
      cur = cur[p];
    }
    return cur === undefined ? dflt : cur;
  }
  function has(obj, path) {
    const sentinel = {};
    return get(obj, path, sentinel) !== sentinel;
  }
  function setPath(obj, path, val) {
    const parts = path.split('.');
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      if (cur[parts[i]] == null || typeof cur[parts[i]] !== 'object') cur[parts[i]] = {};
      cur = cur[parts[i]];
    }
    cur[parts[parts.length - 1]] = val;
  }
  function num(v, d) { const n = Number(v); return Number.isFinite(n) ? n : (d === undefined ? 0 : d); }
  function str(v) { return v == null ? '' : String(v); }
  function asList(d, keys) {
    if (Array.isArray(d)) return d;
    if (d && typeof d === 'object') {
      for (const k of keys.concat(['items', 'rows', 'data', 'results'])) if (Array.isArray(d[k])) return d[k];
    }
    return [];
  }
  function debounce(fn, ms) { let t; return function () { clearTimeout(t); const a = arguments; t = setTimeout(() => fn.apply(null, a), ms); }; }
  function parseTs(v) {
    if (!v) return null;
    if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)) { // date-only: local midnight
      const [y, m, dd] = v.split('-').map(Number);
      return new Date(y, m - 1, dd);
    }
    const d = new Date(v);
    return isNaN(d.getTime()) ? null : d;
  }
  const pad = n => String(n).padStart(2, '0');
  function clock(d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function clockS(d) { return clock(d) + ':' + pad(d.getSeconds()); }
  function sameDay(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  function exact(v) {
    const d = parseTs(v);
    if (!d) return '';
    if (typeof v === 'string' && v.length === 10) { try { return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }); } catch (e) { return v; } }
    try {
      return d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
    } catch (e) { return d.toString(); }
  }
  function dayLabel(d) {
    const now = new Date();
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (sameDay(d, now)) return 'today';
    if (sameDay(d, y)) return 'yesterday';
    return d.getDate() + ' ' + MON[d.getMonth()];
  }
  function rel(v, mode) {
    const d = parseTs(v);
    if (!d) return '—';
    const diff = (Date.now() - d.getTime()) / 1000; // >0 past
    if (mode === 'next') {
      if (typeof v === 'string' && v.length === 10) {
        const today = new Date(); today.setHours(0, 0, 0, 0);
        const dd = Math.round((d - today) / 86400000);
        return dd < 0 ? 'Overdue ' + (-dd) + 'd' : dd === 0 ? 'Due today' : dd === 1 ? 'tomorrow' : 'in ' + dd + 'd';
      }
      if (diff >= 0) return diff < 86400 ? 'Due now' : 'Overdue ' + Math.floor(diff / 86400) + 'd';
      const f = -diff;
      if (f < 3600) return 'in ' + Math.max(1, Math.round(f / 60)) + 'm';
      if (f < 86400) return 'in ' + Math.round(f / 3600) + 'h';
      const days = Math.round(f / 86400);
      return days === 1 ? 'tomorrow' : 'in ' + days + 'd';
    }
    if (diff < -60) return 'in ' + Math.round(-diff / 60) + 'm';
    if (diff < 45) return 'just now';
    if (diff < 3600) return Math.round(diff / 60) + 'm ago';
    if (diff < 86400) return Math.round(diff / 3600) + 'h ago';
    if (diff < 2 * 86400) return 'yesterday';
    if (diff < 7 * 86400) return Math.floor(diff / 86400) + 'd ago';
    return d.getDate() + ' ' + MON[d.getMonth()];
  }
  function relEl(v, mode, extraClass) {
    const d = parseTs(v);
    if (!d) return h('span', { class: 'faint' }, '—');
    const due = mode === 'next' && d.getTime() <= Date.now();
    return h('time', { datetime: v, title: exact(v), class: (due ? 'due ' : '') + (extraClass || ''), dataset: { rel: v, relmode: mode || 'past' } }, rel(v, mode));
  }
  function dur(ms) {
    if (!(ms >= 0)) return '';
    const t = Math.floor(ms / 1000);
    const hh = Math.floor(t / 3600), mm = Math.floor((t % 3600) / 60), ss = t % 60;
    return hh ? hh + ':' + pad(mm) + ':' + pad(ss) : pad(mm) + ':' + pad(ss);
  }
  function durWords(ms) {
    if (!(ms >= 0)) return '';
    const m = Math.round(ms / 60000);
    if (m < 1) return '<1 min';
    if (m < 60) return m + ' min';
    return Math.floor(m / 60) + ' h ' + (m % 60) + ' min';
  }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }
  function fmt1(n) { n = num(n); return n === 0 ? '0' : (n >= 10 || Number.isInteger(n) ? n.toFixed(0) : n.toFixed(1)); }
  // Imported tracker rows carry the original source in the company name, e.g. "Capio Group (JobRight)".
  const CO_SUFFIX = /^(.*\S)\s*\(((?:via\s+)?(?:jobright|linkedin[^)]*|posts?|from post|referral|email))\)$/i;
  function splitCompany(name) {
    name = str(name);
    const m = CO_SUFFIX.exec(name);
    return m ? [m[1], m[2].replace(/^via\s+/i, '')] : [name, ''];
  }
  function reduceMotion() { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; } }
  function scrollToEl(el) { if (el) el.scrollIntoView({ behavior: reduceMotion() ? 'auto' : 'smooth', block: 'start' }); }

  /* ═════════════════════════ Toasts & tooltip ═════════════════════════ */

  const toastSeen = {};
  function toast(msg, kind, key) {
    if (key) {
      const t = toastSeen[key];
      if (t && Date.now() - t < 30000) return;
      toastSeen[key] = Date.now();
    }
    const box = $('#toasts');
    if (!box) return;
    const el = h('div', { class: 'toast toast-' + (kind || 'info'), role: kind === 'bad' ? 'alert' : 'status' },
      h('div', { style: { flex: '1' } }, msg),
      h('button', { class: 'toast-x', type: 'button', 'aria-label': 'Dismiss', onclick: () => close() }, '×'));
    function close() { el.classList.add('out'); setTimeout(() => el.remove(), 220); }
    box.append(el);
    while (box.children.length > 4) box.firstChild.remove();
    setTimeout(close, kind === 'bad' ? 9000 : 5000);
  }
  function apiErrorToast(label, e) {
    const st = e && e.status;
    if (st === 404) toast(label + ' isn’t available from the server yet (404). The rest of the dashboard keeps working.', 'warn', 'e404:' + label);
    else if (st) toast(label + ' failed: ' + (e.message || ('HTTP ' + st)), 'bad', 'e:' + label + st);
    else toast('Can’t reach the Job Hunter server. It may have been closed — reopen Job Hunter from the Desktop.', 'bad', 'net');
  }

  const tip = { el: null };
  function showTip(content, x, y) {
    if (!tip.el) tip.el = $('#tooltip');
    tip.el.replaceChildren(content);
    tip.el.hidden = false;
    const r = tip.el.getBoundingClientRect();
    let left = x + 14, top = y - r.height - 10;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    if (top < 8) top = y + 16;
    tip.el.style.left = left + 'px';
    tip.el.style.top = top + 'px';
  }
  function hideTip() { if (tip.el) tip.el.hidden = true; }

  /* ═════════════════════════ API ═════════════════════════ */

  // Viewing another person's data (Ravi): his own database, via ?profile=<id> on the data endpoints
  function withProfile(path) {
    const p = S.viewProfile;
    if (!p || p === 'akshay' || !/^\/api\//.test(path) || /^\/api\/(run|stop|settings|stream|watch|profiles|version|env)\b/.test(path)) return path;
    return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'profile=' + encodeURIComponent(p);
  }
  async function api(path, opt) {
    opt = opt || {};
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), opt.timeout || 20000) : null;
    try {
      const init = { method: opt.method || 'GET', headers: { Accept: 'application/json' }, signal: ctrl ? ctrl.signal : undefined, cache: 'no-store' };
      if (opt.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opt.body); }
      if (opt.form) init.body = opt.form;
      const r = await fetch(withProfile(path), init);
      const txt = await r.text();
      let data = null;
      if (txt) { try { data = JSON.parse(txt); } catch (e) { data = { raw: txt }; } }
      if (!r.ok) {
        const err = new Error((data && (data.error || data.message || data.detail)) || ('HTTP ' + r.status));
        err.status = r.status; err.data = data;
        throw err;
      }
      return data;
    } catch (e) {
      if (!opt.quiet) apiErrorToast(opt.label || path, e);
      throw e;
    } finally { if (timer) clearTimeout(timer); }
  }
  const tryApi = (path, opt) => api(path, opt).catch(() => null);

  /* ═════════════════════════ Theme ═════════════════════════ */

  function currentTheme() { return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'; }
  function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('jh-theme', t); } catch (e) { /* storage blocked: theme just won't persist */ }
    const b = $('#btn-theme');
    if (b) b.setAttribute('aria-label', t === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
    renderChart();
  }

  /* ═════════════════════════ Lanes model ═════════════════════════ */

  function freshLane(state) {
    return { state: state || 'idle', counts: {}, action: '', actionLevel: 'info', startedAt: null, finishedAt: null, own: false };
  }
  function resetLanes(state) {
    S.lanes = {};
    LANES.forEach(l => { S.lanes[l.id] = freshLane(state); });
    S.sendTimes = [];
  }
  resetLanes('idle');

  function bump(lane, key, by) {
    const L = S.lanes[lane];
    if (!L) return;
    L.counts[key] = num(L.counts[key]) + (by == null ? 1 : by);
  }
  function laneOf(ev) {
    let l = str(ev.lane).toLowerCase();
    if (LANE_ALIAS[l]) l = LANE_ALIAS[l];
    return LANE_BY_ID[l] ? l : 'run';
  }
  const COUNT_KEY_MAP = { emails_sent: 'sent', sent: 'sent', found: 'found', jobs_found: 'found', qualified: 'qualified', skipped: 'skipped',
    nudges: 'nudges', nudges_sent: 'nudges', replies: 'replies', bounces: 'bounces', ready: 'ready', prefilled: 'ready', portal_ready: 'ready',
    failed: 'failed', dry_run: 'dry_run' };

  function applyToLanes(ev) {
    const type = ev.type;
    const lane = laneOf(ev);
    const L = S.lanes[lane];
    const data = ev.data && typeof ev.data === 'object' ? ev.data : {};
    const ts = ev.ts || new Date().toISOString();

    switch (type) {
      case 'run_started':
        resetLanes('queued');
        S.running = true; S.stopping = false; S.runStatus = 'running';
        S.runStartedAt = ts;
        if (ev.run_id != null) S.runId = ev.run_id;
        if (Array.isArray(data.lanes) || data.lanes === 'all') {
          RS.locked = {
            lanes: Array.isArray(data.lanes) ? data.lanes : RUN_LANES.map(x => x[0]),
            regions: Array.isArray(data.regions) ? data.regions : RUN_REGIONS.map(x => x[0]),
          };
        }
        return;
      case 'run_finished': {
        S.running = false; S.stopping = false;
        S.runStatus = data.status || 'done';
        const endState = S.runStatus === 'stopped' ? 'stopped' : (S.runStatus === 'error' ? 'error' : null);
        LANES.forEach(l => {
          const x = S.lanes[l.id];
          if (x.state === 'running') { x.state = endState || 'done'; x.finishedAt = ts; }
          else if (x.state === 'queued') x.state = 'skipped';
        });
        if (S.summary && S.summary.last_run) S.summary.last_run.finished_at = ts;
        return;
      }
      case 'lane_started':
        if (L) { L.state = 'running'; L.startedAt = ts; L.finishedAt = null; L.own = true; if (!L.action) L.action = ev.message || ''; }
        return;
      case 'lane_finished':
        if (L) {
          L.state = ['done', 'error', 'skipped', 'stopped'].includes(data.status) ? data.status : 'done';
          L.finishedAt = ts; L.own = true;
          if (data.counts && typeof data.counts === 'object') {
            for (const k in data.counts) {
              const key = COUNT_KEY_MAP[k] || k;
              L.counts[key] = Math.max(num(L.counts[key]), num(data.counts[k]));
            }
          }
          if (L.state === 'error' && (data.error || ev.message)) { L.action = data.error || ev.message; L.actionLevel = 'error'; }
          else if (!L.action && ev.message) { L.action = ev.message; L.actionLevel = 'info'; }
        }
        return;
      case 'step':
        if (L) { L.action = ev.message || L.action; L.actionLevel = ev.level || 'info'; if (L.state === 'queued' || L.state === 'idle') { L.state = 'running'; L.startedAt = L.startedAt || ts; } }
        return;
      case 'warning': case 'error':
        if (L) { L.action = ev.message || L.action; L.actionLevel = type === 'error' ? 'error' : 'warn'; }
        return;
      case 'job_found': bump(lane, 'found'); return;
      case 'job_qualified': bump(lane, 'qualified'); return;
      case 'job_skipped': bump(lane, 'skipped'); return;
      case 'portal_submitted': bump('submit', 'submitted'); S.lanes.submit.action = ev.message || S.lanes.submit.action; return;
      case 'portal_ready': bump('portal', 'ready'); if (lane !== 'portal') bump(lane, 'ready'); S.lanes.portal.action = ev.message || S.lanes.portal.action; return;
      case 'email_sent': {
        const dry = data.dry_run === true || data.status === 'dry_run';
        if (lane !== 'outreach' && lane !== 'run' && lane !== 'followups') bump(lane, dry ? 'dry_run' : 'sent');
        bump('outreach', dry ? 'dry_run' : 'sent');
        S.lanes.outreach.action = ev.message || S.lanes.outreach.action;
        S.lanes.outreach.actionLevel = 'info';
        if (!dry) S.sendTimes.push(parseTs(ts) || new Date());
        return;
      }
      case 'email_failed':
        bump('outreach', 'failed');
        S.lanes.outreach.action = ev.message || S.lanes.outreach.action;
        S.lanes.outreach.actionLevel = 'error';
        return;
      case 'nudge_sent': bump('followups', 'nudges'); S.lanes.followups.action = ev.message || S.lanes.followups.action; return;
      case 'reply_found': bump('followups', 'replies'); S.lanes.followups.action = ev.message || S.lanes.followups.action; return;
      case 'bounce_found': bump('followups', 'bounces'); S.lanes.followups.action = ev.message || S.lanes.followups.action; return;
      case 'stats':
        if (data && typeof data === 'object') {
          if (data.last_run && S.lastRunStats && data.last_run.id === S.lastRunStats.id) data.last_run.stats = S.lastRunStats.stats;
          S.summary = Object.assign({}, S.summary || {}, data);
          if (data.questions_open != null && S.qCounts && num(data.questions_open) !== num(S.qCounts.open) ) { if (qBusy()) S.qStale = true; else loadQuestions(true); }
          if (typeof data.running === 'boolean' && !S.stopping) S.running = data.running;
        }
        return;
      default: return;
    }
  }

  // Outreach happens inside the source lanes (SPEC §1), so unless the backend emits its own
  // outreach lane events, its state is derived from the source lanes.
  function outreachView() {
    const O = S.lanes.outreach;
    if (O.own) return O;
    const src = SOURCE_LANES.map(id => S.lanes[id]);
    const v = Object.assign({}, O);
    const started = src.map(x => parseTs(x.startedAt)).filter(Boolean);
    const finished = src.map(x => parseTs(x.finishedAt)).filter(Boolean);
    v.startedAt = started.length ? new Date(Math.min.apply(null, started)).toISOString() : null;
    if (src.some(x => x.state === 'running')) { v.state = 'running'; v.finishedAt = null; }
    else if (src.every(x => ['done', 'error', 'skipped', 'stopped'].includes(x.state))) {
      v.state = src.every(x => x.state === 'skipped') ? 'skipped' : (src.some(x => x.state === 'stopped') ? 'stopped' : 'done');
      v.finishedAt = finished.length ? new Date(Math.max.apply(null, finished)).toISOString() : null;
    }
    else if (S.running) v.state = 'queued';
    else v.state = O.state === 'idle' ? 'idle' : O.state;
    return v;
  }

  /* ═════════════════════════ Top bar ═════════════════════════ */

  function renderTop() {
    const agb = $('#ag-pitch'); if (agb) agb.disabled = !!S.running;
    const pill = $('#run-pill');
    const label = $('.pill-label', pill), timer = $('.pill-timer', pill);
    let cls = 'pill-idle', text = 'Idle';
    const lr = (S.summary && S.summary.last_run) || {};
    if (S.running && S.stopping) { cls = 'pill-running pill-stopping'; text = 'Stopping'; }
    else if (S.running) { cls = 'pill-running'; text = 'Running'; }
    else if ((S.runStatus || lr.status) === 'stopped') { cls = 'pill-stopped'; text = 'Stopped'; }
    else if ((S.runStatus || lr.status) === 'error') { cls = 'pill-error'; text = 'Error'; }
    pill.className = 'pill ' + cls;
    label.textContent = text;
    timer.hidden = !S.running;
    tickTimers();

    $('#btn-run').hidden = S.running;
    renderRunbar();
    const stop = $('#btn-stop');
    stop.hidden = !S.running;
    stop.disabled = S.stopping;
    $('span', stop).textContent = S.stopping ? 'Stopping…' : 'Stop';

    // last run line: "today 21:40 · 14 min · 6 sent"
    const lrEl = $('#last-run');
    const st = parseTs(lr.started_at);
    if (!st) { lrEl.textContent = S.summary ? 'No runs yet' : ''; lrEl.title = ''; }
    else {
      const parts = [];
      if (S.running) {
        const now = LANES.filter(l => l.group === 'seq' && S.lanes[l.id].state === 'running').map(l => LANE_SHORT[l.id]);
        if (now.length) parts.push('Now: ' + now.join(' + '));
        parts.push('started ' + clock(st));
        const sent = num(get(S.lanes, 'outreach.counts.sent'));
        parts.push(sent + ' sent so far');
      } else {
        parts.push('Last run ' + dayLabel(st) + ' ' + clock(st));
        const fin = parseTs(lr.finished_at);
        if (fin) parts.push(durWords(fin - st));
        const sent = lr.sent != null ? lr.sent : get(lr, 'stats.sent', get(lr, 'stats.emails_sent', null));
        const dryN = num(get(lr, 'stats.dry_run', 0));
        if (sent != null && !(num(sent) === 0 && dryN)) parts.push(num(sent) + ' sent');
        if (dryN) parts.push(dryN + ' dry run');
      }
      if (parts[0]) parts[0] = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
      lrEl.textContent = parts.join(' · ');
      lrEl.title = 'Run #' + (lr.id != null ? lr.id : '?') + ', started ' + exact(lr.started_at);
    }
    $('#run-id').textContent = S.runId != null ? '#' + S.runId : '';
    const note = $('#run-note');
    if (S.running) note.textContent = 'Live — updates as each step happens';
    else if (S.runId != null) note.textContent = 'Showing the last run';
    else note.textContent = '';
  }

  function tickTimers() {
    const timer = $('.pill-timer');
    if (timer && S.running) {
      const st = parseTs(S.runStartedAt || get(S.summary, 'last_run.started_at'));
      timer.textContent = st ? dur(Date.now() - st.getTime()) : '';
    }
    $$('[data-elapsed-lane]').forEach(el => {
      const id = el.getAttribute('data-elapsed-lane');
      const L = id === 'outreach' ? outreachView() : S.lanes[id];
      el.textContent = laneElapsed(L);
    });
  }
  function laneElapsed(L) {
    if (!L) return '';
    const st = parseTs(L.startedAt);
    if (!st) return '';
    const end = parseTs(L.finishedAt);
    return dur((end ? end.getTime() : (L.state === 'running' ? Date.now() : st.getTime())) - st.getTime());
  }

  /* ═════════════════════════ KPI strip ═════════════════════════ */

  const prevKpi = {};
  function sparkSvg(values, title) {
    const vals = values.map(v => num(v));
    if (vals.length < 2 || vals.every(v => v === 0)) return null;
    const W = 72, H = 28, max = Math.max.apply(null, vals) || 1;
    const step = W / (vals.length - 1);
    const pts = vals.map((v, i) => [i * step, H - 2 - (v / max) * (H - 4)]);
    const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    const svg = s('svg', { class: 'kpi-spark', viewBox: '0 0 ' + W + ' ' + H, preserveAspectRatio: 'none', role: 'img', 'aria-label': title }, [
      s('path', { class: 'area', d: line + ' L' + W + ' ' + H + ' L0 ' + H + ' Z' }),
      s('path', { class: 'line', d: line, 'vector-effect': 'non-scaling-stroke' }),
    ]);
    const t = s('title'); t.textContent = title; svg.prepend(t);
    return svg;
  }
  function dayTotal(d) { return CHART_SERIES.reduce((a, k) => a + num(d[k]), 0); }
  function dailySeries(key) {
    const daily = asList(S.charts && S.charts.daily, []);
    if (!daily.length) return [];
    if (key === 'total') return daily.map(dayTotal);
    return daily.map(d => num(d[key], NaN)).filter(v => !isNaN(v));
  }
  function renderKpis() {
    const box = $('#kpis');
    const sm = S.summary;
    if (!sm) {
      if (!box.children.length) for (let i = 0; i < 6; i++) box.append(h('div', { class: 'kpi' }, h('span', { class: 'skel', style: { width: '50%' } }), h('span', { class: 'skel', style: { width: '30%', height: '20px' } }), h('span', { class: 'skel', style: { width: '60%' } })));
      return;
    }
    const totals = dailySeries('total');
    const rolling = totals.map((_, i) => totals.slice(Math.max(0, i - 6), i + 1).reduce((a, b) => a + b, 0));
    const replyDaily = dailySeries('replies');
    let rate = num(sm.reply_rate, NaN);
    if (!isNaN(rate) && rate <= 1) rate = rate * 100;
    const rateTxt = isNaN(rate) ? '' : (rate >= 10 ? rate.toFixed(0) : rate.toFixed(1)) + '%';

    // send-rate sparkline: sends per 5-minute bucket during this run
    let rateSpark = [];
    const st = parseTs(S.runStartedAt || get(sm, 'last_run.started_at'));
    if (st && S.sendTimes.length) {
      const end = S.running ? Date.now() : (parseTs(get(sm, 'last_run.finished_at')) || new Date()).getTime();
      const bucket = 5 * 60000, n = Math.max(2, Math.min(24, Math.ceil((end - st) / bucket)));
      rateSpark = new Array(n).fill(0);
      S.sendTimes.forEach(t => { const i = Math.min(n - 1, Math.max(0, Math.floor((t - st) / bucket))); rateSpark[i]++; });
    }
    const perHour = sm.per_hour_current_run;
    const avg7 = num(sm.sent_7d) / 7;

    const runSent = num(get(S.lanes, 'outreach.counts.sent'));
    const noRate = perHour == null || (!sm.last_run && !S.running);
    const tiles = [
      { k: 'sent_today', label: 'Sent today', value: num(sm.sent_today), sub: S.running ? runSent + ' this run · ' + num(sm.sent_total) + ' all time' : num(sm.sent_total).toLocaleString() + ' all time', spark: totals, sparkTitle: 'Emails sent per day, last 14 days', hero: true },
      { k: 'sent_7d', label: 'Sent 7 days', value: num(sm.sent_7d), sub: fmt1(avg7) + ' a day on average', spark: rolling, sparkTitle: 'Rolling 7-day total' },
      { k: 'replies', label: 'Replies', value: num(sm.replies_total), unit: rateTxt && num(sm.sent_total) ? rateTxt + ' rate' : '', sub: num(sm.bounced) ? plural(num(sm.bounced), 'bounce') : 'No bounces', spark: replyDaily, sparkTitle: 'Replies per day, last 14 days' },
      { k: 'awaiting', label: 'Awaiting reply', value: num(sm.awaiting), sub: 'No reply yet' },
      { k: 'nudges_due', label: 'Follow-ups due', value: num(sm.nudges_due), sub: num(sm.nudges_due) ? 'Go out on the next run' : 'Nothing due' },
      { k: 'rate', label: S.running || !sm.last_run ? 'Send rate this run' : 'Send rate last run', value: noRate ? '—' : fmt1(perHour), unit: noRate ? '' : '/hr', sub: S.running ? 'Live' : (!sm.last_run ? 'No runs yet' : (num(sm.skipped_today) ? plural(num(sm.skipped_today), 'job') + ' skipped today' : 'No jobs skipped today')), spark: rateSpark, sparkTitle: 'Sends per 5 minutes this run' },
    ];
    box.replaceChildren(...tiles.map(t => {
      const sp = t.spark ? sparkSvg(t.spark, t.sparkTitle) : null;
      const el = h('div', { class: 'kpi' + (t.attn ? ' kpi-attn' : '') + (t.hero ? ' kpi-hero' : '') },
        h('div', { class: 'kpi-label' }, t.label),
        h('div', { class: 'kpi-row' },
          h('div', { class: 'kpi-value' }, String(t.value), t.unit ? h('span', { class: 'kpi-unit' }, t.unit) : null),
          sp),
        h('div', { class: 'kpi-sub' }, t.sub));
      if (prevKpi[t.k] !== undefined && prevKpi[t.k] !== t.value) el.classList.add('flash');
      prevKpi[t.k] = t.value;
      return el;
    }));
  }

  /* ═════════════════════════ Lanes view ═════════════════════════ */

  const STATE_LABEL = { idle: 'Idle', queued: 'Queued', running: 'Running', done: 'Done', error: 'Error', skipped: 'Skipped', stopped: 'Stopped' };
  const STATE_ICON = { running: ICON.dot, done: ICON.check, error: ICON.x, stopped: ICON.pause, skipped: ICON.dash };

  function laneCard(def, L) {
    const counts = h('div', { class: 'lane-counts' }, def.counters.map(([k, label]) => {
      const n = num(L.counts[k]);
      if (k === 'found') { // if the engine never emits job_found, infer found from decisions
        const inferred = num(L.counts.qualified) + num(L.counts.skipped);
        if (inferred > n) return h('span', { class: inferred ? '' : 'zero' }, h('b', null, inferred), label);
      }
      if ((k === 'dry_run' || k === 'failed' || k === 'nudges_skipped') && !n) return null;
      return h('span', { class: n ? '' : 'zero' }, h('b', null, n), label);
    }));
    const actionText = L.action || (L.state === 'idle' || L.state === 'queued' ? def.idle : '');
    // A lane that hasn't started has nothing to count: a row of zeros is just noise.
    const allZero = !def.counters.some(([k]) => num(L.counts[k]) > 0);
    const hideCounts = allZero && (L.state === 'idle' || L.state === 'queued');
    let sub = def.sub;
    if (def.id === 'linkedin_jobs') {   // show the regions this (or the next) run actually searches
      const regs = S.running && RS.locked ? RS.locked.regions : [...RS.regions];
      const names = RUN_REGIONS.filter(([id]) => regs.includes(id)).map(x => x[1]);
      if (names.length) sub = names.join(' + ');
    }
    return h('div', { class: 'lane-card is-' + L.state + (hideCounts ? ' no-counts' : '') },
      h('div', { class: 'lane-top' },
        h('span', { class: 'lane-name', title: sub ? def.name + ' (' + sub + ')' : def.name }, def.name, sub ? h('span', { class: 'muted', style: { fontWeight: 400 } }, '  ' + sub) : null),
        h('span', { class: 'lane-elapsed', 'data-elapsed-lane': def.id }, laneElapsed(L)),
        h('span', { class: 'badge badge-' + L.state }, STATE_LABEL[L.state] || L.state)),
      h('div', { class: 'lane-action' + (L.action ? '' : ' is-empty') + (L.actionLevel === 'error' ? ' fl-error' : ''), title: actionText, style: L.actionLevel === 'error' ? { color: 'var(--bad)' } : (L.actionLevel === 'warn' ? { color: 'var(--warn)' } : null) }, actionText),
      hideCounts ? null : counts);
  }

  let laneRaf = 0;
  function renderLanes() {
    if (laneRaf) return;
    laneRaf = requestAnimationFrame(() => {
      laneRaf = 0;
      const box = $('#lanes');
      const parallel = !!pickSetting('parallel_sources');
      const along = h('div', { class: 'alongside' }, LANES.filter(l => l.group === 'alongside').map(def => laneCard(def, def.id === 'outreach' ? outreachView() : S.lanes[def.id])));
      const rail = h('div', { class: 'rail' + (parallel ? ' parallel' : '') }, LANES.filter(l => l.group === 'seq').map(def => {
        const L = S.lanes[def.id];
        return h('div', { class: 'rail-item is-' + L.state },
          h('div', { class: 'rail-node', svg: STATE_ICON[L.state] || '' }),
          laneCard(def, L));
      }));
      box.replaceChildren(
        h('div', { class: 'lanes-label' }, h('span', null, 'Running alongside the sources')),
        along,
        h('div', { class: 'lanes-label' }, h('span', null, parallel ? 'Sources, in parallel' : 'Sources, in order'), h('span', null, parallel ? '' : 'one Chrome, one at a time')),
        rail);
    });
  }

  /* ═════════════════════════ Activity feed ═════════════════════════ */

  const FEED_CHIPS = [['all', 'All'], ['followups', 'Follow-ups'], ['posts', 'Posts'], ['jobright', 'JobRight'], ['linkedin_jobs', 'LinkedIn Jobs'], ['himalayas', 'Remote boards'], ['agencies', 'Agencies'], ['outreach', 'Outreach'], ['portal', 'Portal'], ['easy_apply', 'Easy Apply'], ['submit', 'Submit']];

  function renderChips() {
    const box = $('#feed-chips');
    box.replaceChildren(...FEED_CHIPS.map(([id, label]) => {
      const n = id === 'all' ? null : num(S.feedCounts[id]);
      return h('button', { type: 'button', class: 'chip', 'aria-pressed': String(S.feedFilter === id), dataset: { lane: id },
        onclick: () => { S.feedFilter = id; renderChips(); applyFeedFilter(); scrollFeedBottom(true); } },
        label, n ? h('span', { class: 'n' }, n) : null);
    }));
  }
  const updateChipCounts = debounce(renderChips, 400);

  function feedLaneTokens(ev) {
    const lane = laneOf(ev);
    const toks = [lane];
    if (ev.type === 'email_sent' || ev.type === 'email_failed') toks.push('outreach');
    if (['nudge_sent', 'reply_found', 'bounce_found'].includes(ev.type)) toks.push('followups');
    if (ev.type === 'portal_ready') toks.push('portal');
    return Array.from(new Set(toks));
  }
  function lineVisible(el) {
    if (el.classList.contains('fl-divider')) return S.feedFilter === 'all';
    if (el.classList.contains('fl-debug') && !S.feedDebug) return false;
    if (S.feedFilter === 'all') return true;
    return (' ' + el.dataset.lanes + ' ').indexOf(' ' + S.feedFilter + ' ') >= 0;
  }
  function applyFeedFilter() {
    const feed = $('#feed');
    let any = false, lastLane = null;
    $$('.fl, .fl-divider', feed).forEach(el => {
      const v = lineVisible(el); el.hidden = !v; any = any || v;
      if (!v) return;
      const ln = el.classList.contains('fl') ? el.dataset.lane : null;
      el.classList.toggle('fl-cont', ln != null && ln === lastLane);
      lastLane = ln;
    });
    const empty = $('.feed-empty', feed);
    if (empty) empty.hidden = any;
  }
  function feedAtBottom() {
    const f = $('#feed');
    return f.scrollHeight - f.scrollTop - f.clientHeight < 24;
  }
  function scrollFeedBottom(force) {
    const f = $('#feed');
    if (force || (!S.feedHover && S.feedStick)) { f.scrollTop = f.scrollHeight; S.feedStick = true; S.feedPending = 0; $('#feed-resume').hidden = true; }
  }
  function ensureFeedEmpty() {
    const feed = $('#feed');
    if (!$('.feed-empty', feed)) feed.append(h('div', { class: 'feed-empty' },
      h('div', { class: 'empty-title' }, 'No activity yet'),
      h('div', null, 'Start a run and every step the engine takes shows up here, live.'),
      h('button', { class: 'btn btn-sm', type: 'button', onclick: () => runNow() }, 'Run now ', h('kbd', null, 'R'))));
  }
  function addDivider(text) {
    const feed = $('#feed');
    feed.append(h('div', { class: 'fl-divider', hidden: S.feedFilter !== 'all' }, text));
    scrollFeedBottom();
  }

  function addFeedLine(ev, live) {
    const feed = $('#feed');
    const type = ev.type || 'step';
    if (type === 'stats') return;
    if (type === 'run_started') {
      const d = parseTs(ev.ts) || new Date();
      addDivider('Run ' + (ev.run_id != null ? '#' + ev.run_id + ' ' : '') + 'started ' + dayLabel(d) + ' at ' + clock(d));
    }
    const lane = laneOf(ev);
    const d = parseTs(ev.ts) || new Date();
    let level = ev.level || 'info';
    if (type === 'error' || type === 'email_failed') level = 'error';
    if (type === 'warning' && level === 'info') level = 'warn';
    const cls = ['fl', 'fl-' + level, 'lc-' + lane];
    if (type === 'tool') cls.push('fl-tool');
    if (['reply_found', 'portal_ready', 'run_started', 'run_finished', 'lane_started', 'lane_finished'].includes(type)) cls.push('fl-strong');
    const toks = feedLaneTokens(ev);
    const msg = str(ev.message) || type.replace(/_/g, ' ');
    const prev = feed.lastElementChild;
    const prevLine = prev && prev.classList.contains('fl') ? prev : null;
    // The same line twice in a row (e.g. a step and the lane_finished that repeats it): show it once with ×2.
    if (prevLine && prevLine.dataset.msg === msg && prevLine.dataset.lanes === toks.join(' ') && prevLine.dataset.level === level) {
      const n = num(prevLine.dataset.rep, 1) + 1;
      prevLine.dataset.rep = String(n);
      let rep = $('.fl-rep', prevLine);
      if (!rep) { rep = h('span', { class: 'fl-rep' }); $('.fl-msg', prevLine).append(rep); }
      rep.textContent = '×' + n;
      if (cls.includes('fl-strong')) prevLine.classList.add('fl-strong');
      return;
    }
    if (live) cls.push('fl-new');
    // Consecutive lines from the same lane: keep the color tick, drop the repeated name.
    let pv = prev, guard = 0;
    while (pv && pv.hidden && guard++ < 200) pv = pv.previousElementSibling;
    if (pv && pv.classList.contains('fl') && pv.dataset.lane === lane) cls.push('fl-cont');
    toks.forEach(t => { S.feedCounts[t] = num(S.feedCounts[t]) + (level === 'debug' ? 0 : 1); });
    const el = h('div', { class: cls.join(' '), dataset: { lanes: toks.join(' '), lane: lane, msg: msg, level: level } },
      h('span', { class: 'fl-ts', title: exact(ev.ts) }, clockS(d)),
      h('span', { class: 'fl-lane' }, LANE_SHORT[lane] || lane),
      h('span', { class: 'fl-msg', title: msg.length > 160 ? msg : null }, msg));
    el.hidden = !lineVisible(el);
    const stick = !S.feedHover && S.feedStick;
    feed.append(el);
    const empty = $('.feed-empty', feed);
    if (empty && !el.hidden) empty.hidden = true;
    while (feed.childElementCount > FEED_MAX) {
      const first = feed.firstElementChild;
      if (first.classList.contains('feed-empty')) { feed.children[1].remove(); } else first.remove();
    }
    if (live) {
      updateChipCounts();
      if (stick) scrollFeedBottom();
      else if (!el.hidden) {
        S.feedPending++;
        const b = $('#feed-resume');
        b.hidden = false;
        $('.n', b).textContent = S.feedPending;
      }
    }
  }

  /* ═════════════════════════ Event handling & SSE ═════════════════════════ */

  function normEvent(ev) {
    if (!ev || typeof ev !== 'object') return null;
    if (typeof ev.data === 'string') { try { ev.data = JSON.parse(ev.data); } catch (e) { ev.data = {}; } }
    if (!ev.data && ev.data_json) { try { ev.data = JSON.parse(ev.data_json); } catch (e) { ev.data = {}; } }
    ev.data = ev.data || {};
    return ev;
  }

  function handleEvent(raw, live) {
    const ev = normEvent(raw);
    if (!ev) return;
    const id = ev.id != null ? num(ev.id, null) : null;
    if (id != null) {
      if (S.seen.has(id)) return;
      S.seen.add(id);
      if (S.seen.size > 5000) { const arr = Array.from(S.seen).slice(-2500); S.seen = new Set(arr); }
      if (id > S.lastEventId) S.lastEventId = id;
    }
    const rid = ev.run_id != null ? num(ev.run_id, null) : null;
    // A newer run whose run_started we missed: reset to it.
    if (rid != null && S.runId != null && rid > S.runId && ev.type !== 'run_started' && ev.type !== 'stats') {
      resetLanes('queued'); S.runId = rid; S.running = true; S.runStartedAt = ev.ts;
      addDivider('Run #' + rid);
    }
    const stale = rid != null && S.runId != null && rid < S.runId && ev.type !== 'stats';
    if (!stale) applyToLanes(ev);
    if (S.runId == null && rid != null) S.runId = rid;
    if (!stale || !live) addFeedLine(ev, live);

    if (live) {
      renderLanes();
      if (['run_started', 'run_finished', 'stats', 'lane_started', 'lane_finished', 'email_sent'].includes(ev.type)) renderTop();
      if (ev.type === 'stats' || ev.type === 'email_sent') renderKpis();
      if (ev.type === 'stats') renderBanners();
      const want = REFRESH_ON[ev.type];
      if (want) want.forEach(k => queueRefresh(k));
      if ((ev.type === 'step' || ev.type === 'tool') && /questions? (for you in|to) the Questions tab/i.test(str(ev.message))) queueRefresh('questions');
      if (ev.type === 'reply_found') {
        const cat = ev.data && ev.data.category;
        const act = ['interview', 'call_request', 'assessment', 'info_request'].includes(cat);
        toast((str(ev.message) || 'New reply') + (ev.data && ev.data.draft ? ' — a reply draft is waiting in your Gmail Drafts.' : ''), act ? 'ok' : 'info', act ? null : undefined);
        if (act) notify(cat === 'interview' ? 'Interview!' : 'Reply needs you', str(ev.message), 'apps');
      }
      if (ev.type === 'jobboard_refresh' || ((ev.type === 'lane_finished' || ev.type === 'job_skipped') && ev.lane === 'board_apply')) queueRefresh('board');
      if (ev.type === 'portal_ready') toast((str(ev.message) || 'A form is ready') + (autoSub() ? ' — Job Hunter submits it by itself.' : ' — review it in Ready to submit.'), 'info');
      if (ev.type === 'portal_ready' && ev.data && ev.data.ready === true && !autoSub()) notify('Ready to submit', str(ev.message).replace(/^Ready to submit:\s*/, ''), 'portal');
      if (ev.type === 'watch_found') { toast(str(ev.message), 'ok', 'watchfound'); notify('New job found', str(ev.message), 'watch'); }
      if (ev.type === 'portal_submitted') { toast(str(ev.message), 'ok'); notify('Application submitted', str(ev.message).replace(/^Submitted:\s*/, ''), 'portal'); }
      if (ev.type === 'run_finished') toast(runFinishedText(ev), ev.data && ev.data.status === 'error' ? 'bad' : 'ok');
    }
  }
  function runFinishedText(ev) {
    const st = ev.data && ev.data.status;
    const stats = (ev.data && ev.data.stats) || {};
    const sent = stats.sent != null ? num(stats.sent) : num(get(S.lanes, 'outreach.counts.sent'));
    const dry = num(stats.dry_run);
    if (st !== 'stopped' && st !== 'error' && dry && !sent) return 'Run finished (dry run). ' + plural(dry, 'email') + ' built, none sent.';
    if (st === 'stopped') return 'Run stopped. ' + plural(sent, 'email') + ' sent before stopping.';
    if (st === 'error') return 'Run ended with an error. Check the activity feed for details.';
    return 'Run finished. ' + plural(sent, 'email') + ' sent.';
  }

  // Debounced refetch: at most once per second per kind.
  const pending = new Set();
  let refreshTimer = 0, lastRefresh = 0;
  function queueRefresh(kind) {
    pending.add(kind);
    if (refreshTimer) return;
    const wait = Math.max(0, 1000 - (Date.now() - lastRefresh));
    refreshTimer = setTimeout(flushRefresh, wait);
  }
  function flushRefresh() {
    refreshTimer = 0; lastRefresh = Date.now();
    const kinds = Array.from(pending); pending.clear();
    if (kinds.includes('summary') || kinds.includes('apps')) loadSummary(true);
    if (kinds.includes('apps')) loadApps(true);
    if (kinds.includes('charts')) loadCharts(true);
    if (kinds.includes('portal')) loadPortal(true);
    if (kinds.includes('skipped')) loadSkipped(true);
    if (kinds.includes('questions') ) { if (qBusy()) S.qStale = true; else loadQuestions(true); }
    if ((kinds.includes('apps') || kinds.includes('summary')) && S.view === 'agencies' && !agBusy()) loadAgencies(true);
    if (S.board && (kinds.includes('board') || ((kinds.includes('portal') || kinds.includes('apps')) && S.view === 'board'))) loadBoard(true);   // the list is up to 1500 rows: off-page only Job board events reload it
  }

  let es = null, backoff = 1000, retryTimer = 0, countdownTimer = 0, everConnected = false;
  function setLive(state, label) {
    S.live = state;
    const el = $('#live');
    el.className = 'live live-' + state;
    $('.live-label', el).textContent = label || ({ on: 'Connected', connecting: 'Connecting…', retry: 'Reconnecting…', off: 'Offline' })[state];
  }
  function connectSSE() {
    if (typeof EventSource === 'undefined') { setLive('off', 'Live updates unsupported'); return; }
    clearTimeout(retryTimer); clearInterval(countdownTimer);
    try { es = new EventSource('/api/stream' + (S.lastEventId ? '?after_id=' + S.lastEventId : '')); } catch (e) { scheduleReconnect(); return; }
    const onMsg = e => {
      if (!e.data) return;
      let ev; try { ev = JSON.parse(e.data); } catch (x) { return; }
      handleEvent(ev, true);
    };
    es.addEventListener('ev', onMsg);
    es.onmessage = onMsg;
    es.onopen = () => {
      const wasReconnect = everConnected;
      everConnected = true; backoff = 1000; setLive('on');
      if (wasReconnect) catchUp();
    };
    es.onerror = () => {
      if (es) { es.close(); es = null; }
      scheduleReconnect();
    };
  }
  function scheduleReconnect() {
    const wait = backoff;
    backoff = Math.min(backoff * 2, 15000);
    let left = Math.ceil(wait / 1000);
    setLive('retry', 'Reconnecting in ' + left + 's');
    clearInterval(countdownTimer);
    countdownTimer = setInterval(() => { left--; if (left > 0) setLive('retry', 'Reconnecting in ' + left + 's'); else { clearInterval(countdownTimer); setLive('retry', 'Reconnecting…'); } }, 1000);
    retryTimer = setTimeout(connectSSE, wait);
  }
  async function catchUp() {
    // The stream replays missed events itself (?after_id=); just refresh the derived views.
    await loadSummary(true);
    queueRefresh('apps'); queueRefresh('charts'); queueRefresh('portal'); queueRefresh('skipped');
  }

  /* ═════════════════════════ Loaders ═════════════════════════ */

  async function loadSummary(quiet) {
    const d = await tryApi('/api/summary', { label: 'Summary', quiet: quiet });
    if (d && typeof d === 'object') {
      S.summary = d;
      if (d.questions_open != null && S.qCounts && num(d.questions_open) !== num(S.qCounts.open) ) { if (qBusy()) S.qStale = true; else loadQuestions(true); }
      if (typeof d.running === 'boolean') {
        if (!d.running) S.stopping = false;
        S.running = d.running;
      }
      if (d.running && d.last_run && d.last_run.started_at && !S.runStartedAt) S.runStartedAt = d.last_run.started_at;
      if (d.last_run && d.last_run.status && !d.running) S.runStatus = d.last_run.status;
      const lr = d.last_run;
      if (lr && lr.id != null && !d.running && lr.sent == null) {
        if (S.lastRunStats && S.lastRunStats.id === lr.id) lr.stats = S.lastRunStats.stats;
        else tryApi('/api/runs/' + encodeURIComponent(lr.id), { quiet: true }).then(r => {
          if (r && r.stats) { S.lastRunStats = { id: lr.id, stats: r.stats }; if (S.summary && S.summary.last_run && S.summary.last_run.id === lr.id) { S.summary.last_run.stats = r.stats; renderTop(); } }
        });
      }
    }
    renderTop(); renderKpis(); renderTabs(); renderBanners();
    return d;
  }

  async function loadBacklog(runId, live) {
    const d = await tryApi('/api/events?run_id=' + encodeURIComponent(runId) + '&limit=300', { label: 'Activity history', quiet: true });
    const list = asList(d, ['events']).slice().sort((a, b) => num(a.id) - num(b.id));
    resetLanes(S.running ? 'queued' : 'idle');
    S.runId = num(runId);
    const lr = (S.summary && S.summary.last_run) || {};
    S.runStartedAt = lr.started_at || null;
    if (!list.some(e => e.type === 'run_started') && list.length) {
      const st = parseTs(lr.started_at) || parseTs(list[0].ts);
      if (st) addDivider('Run #' + runId + ' — ' + dayLabel(st) + ' at ' + clock(st) + (list.length >= 300 ? ' (latest 300 lines)' : ''));
    }
    list.forEach(ev => handleEvent(ev, false));
    // The backlog reflects a finished run: settle any lane still marked running/queued.
    if (!S.running) {
      const st = lr.status || S.runStatus || 'done';
      LANES.forEach(l => {
        const x = S.lanes[l.id];
        if (x.state === 'running') x.state = st === 'stopped' ? 'stopped' : (st === 'error' ? 'error' : 'done');
        else if (x.state === 'queued') x.state = 'skipped';
      });
    }
    if (!list.length) ensureFeedEmpty();
    renderChips(); applyFeedFilter(); renderLanes(); renderTop(); renderKpis(); renderNeeds();
    requestAnimationFrame(() => scrollFeedBottom(true));
    if (live) { queueRefresh('apps'); queueRefresh('charts'); }
  }

  async function loadApps(quiet) {
    const d = await tryApi('/api/applications?limit=2000', { label: 'Applications', quiet: quiet });
    if (d === null && S.apps) return;
    const rows = asList(d, ['applications', 'rows']);
    const prevKnown = S.knownApps;
    S.apps = rows;
    S.knownApps = new Set(rows.map(appKey));
    S.newApps = prevKnown ? new Set(rows.map(appKey).filter(k => !prevKnown.has(k))) : new Set();
    renderTabs(); renderAppsBody(); renderReplies(); renderNeeds();
  }
  async function loadSkipped(quiet) {
    const d = await tryApi('/api/jobs?status=skipped&limit=1000', { label: 'Skipped jobs', quiet: quiet });
    if (d === null && S.skipped) return;
    S.skipped = asList(d, ['jobs']);
    renderTabs(); if (S.tab === 'skipped') renderAppsBody();
  }
  async function loadPortal(quiet) {
    const d = await tryApi('/api/portal', { label: 'Ready-to-submit queue', quiet: quiet });
    if (d === null && S.portal) return;
    S.portal = asList(d, ['portal', 'queue']);
    renderTabs(); if (S.tab === 'portal') renderAppsBody(); renderNeeds();
  }
  async function loadQuestions(quiet) {
    const d = await tryApi('/api/questions', { label: 'Questions', quiet: quiet });
    if (d === null && S.questions) return;
    S.questions = asList(d, ['questions']);
    S.qCounts = (d && d.counts) || { open: S.questions.filter(q => q.status === 'open').length };
    renderTabs(); if (S.tab === 'questions') renderAppsBody(); renderNeeds();
  }
  async function loadCharts(quiet) {
    const d = await tryApi('/api/charts', { label: 'Charts', quiet: quiet });
    if (d === null && S.charts) return;
    S.charts = d || { daily: [], funnel: {} };
    renderChart(); renderFunnel(); renderKpis(); renderReplies(); renderNeeds();
  }
  function autoSub() { return !S.settings || S.settings.auto_submit !== false; }   // auto-submit (default on)
  async function loadSettings() {
    const d = await tryApi('/api/settings', { label: 'Settings', quiet: true });
    if (d && typeof d === 'object') S.settings = (d.settings && typeof d.settings === 'object') ? d.settings : ((d.config && typeof d.config === 'object') ? d.config : d);
    renderBanners(); renderLanes();
  }
  async function loadEnv() {
    const d = await tryApi('/api/env', { label: 'Environment checks', quiet: true, timeout: 30000 });
    S.env = d == null ? null : asList(d, ['checks']);
    renderBanners();
    return S.env;
  }

  /* ═════════════════════════ Applications ═════════════════════════ */

  const appKey = r => str(r.contact_id != null ? 'c' + r.contact_id : 'j' + r.job_id + (r.email || ''));
  const AWAITING = ['sent', 'nudge1', 'nudge2'];
  function portalOpen(p) { const st = str(p.status || 'prefilled'); return st === 'prefilled' || st === 'needs_input'; }

  function matchesQuery(r, q) {
    if (!q) return true;
    const hay = [r.company, r.role, r.contact_name, r.contact_title, r.email, r.skip_reason, r.location, get(r, 'job.company'), get(r, 'job.role')].map(str).join(' ').toLowerCase();
    return q.toLowerCase().split(/\s+/).filter(Boolean).every(w => hay.indexOf(w) >= 0);
  }
  function matchesSource(r) {
    if (!S.source) return true;
    return str(r.source || get(r, 'job.source')) === S.source;
  }
  function filteredApps(tab) {
    const rows = S.apps || [];
    return rows.filter(r => {
      const st = str(r.app_status);
      if (tab === 'awaiting' && AWAITING.indexOf(st) < 0) return false;
      if (tab === 'replied' && st !== 'replied') return false;
      if (tab === 'bounced' && st !== 'bounced' && st !== 'failed') return false;
      return matchesSource(r) && matchesQuery(r, S.q);
    });
  }
  function tabCount(id) {
    if (id === 'skipped') return S.skipped ? S.skipped.filter(r => matchesSource(r) && matchesQuery(r, S.q)).length : null;
    if (id === 'portal') return S.portal ? S.portal.filter(portalOpen).length : null;
    if (id === 'questions') return S.qCounts ? num(S.qCounts.open) : null;
    return S.apps ? filteredApps(id).length : null;
  }
  function renderTabs() {
    const box = $('#tabs');
    box.replaceChildren(...TABS.map(t => {
      const n = tabCount(t.id);
      return h('button', { type: 'button', role: 'tab', class: 'tab' + (t.id === 'portal' ? ' tab-portal' : ''), 'aria-selected': String(S.tab === t.id), id: 'tab-' + t.id,
        onclick: () => { if (S.tab !== t.id) { S.tab = t.id; S.shown = PAGE; renderTabs(); renderAppsBody(); } } },
        t.label, n != null ? h('span', { class: 'n' + ((t.id === 'portal' || t.id === 'questions') && n > 0 ? ' hot' : '') }, n) : null);
    }));
  }

  function srcBadge(src) {
    src = str(src);
    if (!src) return h('span', { class: 'faint' }, '—');
    return h('span', { class: 'src src-' + src, title: SOURCES[src] || src }, SOURCES[src] || src);
  }
  function companyCell(name) {
    const [co, via] = splitCompany(name);
    if (!via) return h('span', { class: 'co', title: str(name) }, co || '—');
    return h('div', { class: 'cell-2', title: str(name) }, h('span', { class: 'co' }, co), h('span', { class: 'sub' }, (/\bvia\b/i.test(via) ? '' : 'via ') + via));
  }
  function statusChip(st) {
    st = str(st) || 'sent';
    return h('span', { class: 'st st-' + st }, STATUS[st] || st.replace(/_/g, ' '));
  }
  function fitCell(v) {
    if (v == null || v === '') return h('span', { class: 'faint' }, '—');
    const n = Math.max(0, Math.min(100, num(v)));
    return h('span', { class: 'fit' + (n >= 75 ? ' fit-hi' : ''), title: 'Fit score ' + n + ' / 100' },
      String(Math.round(n)), h('span', { class: 'fit-bar', 'aria-hidden': 'true' }, h('i', { style: { height: n + '%' } })));
  }
  function emptyState(title, text, action) {
    return h('div', { class: 'empty' }, h('div', { class: 'empty-title' }, title), h('div', { class: 'empty-text' }, text), action || null);
  }
  function skeletonRows(n) {
    const wrap = h('div', null);
    for (let i = 0; i < n; i++) wrap.append(h('div', { class: 'skel-row' },
      h('span', { class: 'skel', style: { width: '70%' } }), h('span', { class: 'skel', style: { width: '85%' } }), h('span', { class: 'skel', style: { width: '80%' } }),
      h('span', { class: 'skel', style: { width: '60%' } }), h('span', { class: 'skel', style: { width: '75%' } }), h('span', { class: 'skel', style: { width: '50%' } })));
    return wrap;
  }

  // While Akshay is typing in the Questions tab, live refreshes must not rebuild the list under his cursor.
  function qBusy() {
    const el = document.activeElement, body = $('#apps-body');
    return S.tab === 'questions' && !!el && !!body && body.contains(el) && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA');
  }
  function renderAppsBody(force) {
    if (!force && qBusy()) { S.qDirty = true; return; }
    S.qDirty = false;
    const body = $('#apps-body');
    body.setAttribute('role', 'tabpanel');
    body.setAttribute('aria-labelledby', 'tab-' + S.tab);
    if (S.tab === 'portal') return renderPortal(body);
    if (S.tab === 'questions') return renderQuestions(body);
    if (S.tab === 'skipped') return renderSkipped(body);
    if (S.apps === null) { body.replaceChildren(skeletonRows(6)); return; }
    const rows = filteredApps(S.tab).slice().sort((a, b) => (parseTs(b.last_touch_at || b.sent_at) || 0) - (parseTs(a.last_touch_at || a.sent_at) || 0));
    if (!rows.length) {
      const filtered = S.q || S.source;
      const msgs = {
        all: ['No applications yet', 'When a run finds a qualified job and a recruiter email, the application lands here. You can also import your old tracker.csv from Settings.'],
        awaiting: ['Nobody is waiting on a reply', 'Applications that were sent or nudged without a reply show up here.'],
        replied: ['No replies yet', 'Replies are picked up from Gmail at the start of every run. Nudges go out after 1 and 3 business days.'],
        bounced: ['No bounces', 'Undeliverable emails show up here so you can try another contact at the company.'],
      };
      const m = filtered ? ['Nothing matches', 'Try a different search or clear the source filter.'] : msgs[S.tab];
      const act = filtered ? h('button', { class: 'btn btn-sm', type: 'button', onclick: clearFilters }, 'Clear filters')
        : (S.tab === 'all' ? h('button', { class: 'btn btn-sm', type: 'button', onclick: () => openSettings('import') }, 'Import tracker.csv') : null);
      body.replaceChildren(emptyState(m[0], m[1], act));
      return;
    }
    const shown = rows.slice(0, S.shown);
    const cols = [
      ['col-company', 'Company'], ['col-role', 'Role'], ['col-source', 'Source'], ['col-fit num', 'Fit'],
      ['col-contact', 'Contact'], ['col-email', 'Email'], ['col-time', 'Sent'], ['col-time col-last', 'Last touch'],
      ['col-time col-next', 'Next follow-up'], ['col-status', 'Status'],
    ];
    const tbody = h('tbody', null, shown.map(r => {
      const key = appKey(r);
      const done = ['replied', 'bounced', 'failed', 'dry_run', 'closed'].indexOf(str(r.app_status)) >= 0;
      const hist = str(r.source) === 'import';
      const tr = h('tr', { tabindex: '0', class: (S.newApps && S.newApps.has(key) ? 'row-new' : '') + (S.activeRow === key ? ' is-active' : '') + (hist ? ' row-hist' : ''), dataset: { key: key },
        onclick: () => openApp(r), onkeydown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openApp(r); } } },
        h('td', { class: 'col-company' }, companyCell(r.company)),
        h('td', { class: 'col-role', title: str(r.role) }, str(r.role) || '—'),
        h('td', { class: 'col-source' }, srcBadge(r.source)),
        h('td', { class: 'col-fit num' }, fitCell(r.fit_score)),
        h('td', { class: 'col-contact' }, h('div', { class: 'cell-2' },
          h('span', { title: str(r.contact_name) }, str(r.contact_name) || '—'),
          h('span', { class: 'sub', title: str(r.contact_title) }, str(r.contact_title)))),
        h('td', { class: 'col-email muted', title: str(r.email) }, str(r.email) || '—'),
        h('td', { class: 'col-time' }, relEl(r.sent_at)),
        h('td', { class: 'col-time col-last' }, relEl(r.last_touch_at || r.sent_at)),
        h('td', { class: 'col-time col-next' }, done ? h('span', { class: 'faint' }, '—') : (r.next_followup_at ? relEl(r.next_followup_at, 'next') : h('span', { class: 'faint', title: 'Both nudges sent; sequence done' }, 'Done'))),
        h('td', { class: 'col-status' }, statusChip(r.app_status)));
      return tr;
    }));
    const table = h('table', { class: 'tbl' },
      h('thead', null, h('tr', null, cols.map(c => h('th', { class: c[0], scope: 'col' }, c[1])))),
      tbody);
    const foot = h('div', { class: 'tbl-foot' },
      h('span', null, 'Showing ' + shown.length + ' of ' + rows.length),
      rows.length > shown.length ? h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { S.shown += PAGE; renderAppsBody(); } }, 'Show ' + Math.min(PAGE, rows.length - shown.length) + ' more') : h('span', null, ''));
    body.replaceChildren(h('div', { class: 'tbl-wrap' }, table), foot);
    S.newApps = new Set();
  }

  function renderSkipped(body) {
    if (S.skipped === null) { body.replaceChildren(skeletonRows(5)); return; }
    const rows = S.skipped.filter(r => matchesSource(r) && matchesQuery(r, S.q))
      .sort((a, b) => (parseTs(b.created_at || b.updated_at) || 0) - (parseTs(a.created_at || a.updated_at) || 0));
    if (!rows.length) {
      body.replaceChildren(emptyState(S.q || S.source ? 'Nothing matches' : 'No skipped jobs',
        S.q || S.source ? 'Try a different search or clear the source filter.' : 'Jobs the engine passes on (US-only, clearance, W2, onsite, no verified email) are listed here with the reason.',
        S.q || S.source ? h('button', { class: 'btn btn-sm', type: 'button', onclick: clearFilters }, 'Clear filters') : null));
      return;
    }
    const shown = rows.slice(0, S.shown);
    const table = h('table', { class: 'tbl' },
      h('thead', null, h('tr', null,
        h('th', { class: 'col-company' }, 'Company'), h('th', { class: 'col-role' }, 'Role'), h('th', { class: 'col-source' }, 'Source'),
        h('th', { class: 'col-fit num' }, 'Fit'), h('th', { class: 'col-reason' }, 'Why it was skipped'), h('th', { class: 'col-time' }, 'Found'))),
      h('tbody', null, shown.map(r => h('tr', { tabindex: '0', class: str(r.source) === 'import' ? 'row-hist' : null, onclick: () => openSkipped(r), onkeydown: e => { if (e.key === 'Enter') openSkipped(r); } },
        h('td', { class: 'col-company' }, companyCell(r.company)),
        h('td', { class: 'col-role', title: str(r.role) }, str(r.role) || '—'),
        h('td', { class: 'col-source' }, srcBadge(r.source)),
        h('td', { class: 'col-fit num' }, fitCell(r.fit_score)),
        h('td', { class: 'col-reason muted', title: str(r.skip_reason) }, str(r.skip_reason) || '—'),
        h('td', { class: 'col-time' }, relEl(r.created_at || r.updated_at))))));
    body.replaceChildren(h('div', { class: 'tbl-wrap' }, table),
      h('div', { class: 'tbl-foot' }, h('span', null, 'Showing ' + shown.length + ' of ' + rows.length),
        rows.length > shown.length ? h('button', { class: 'btn btn-sm', type: 'button', onclick: () => { S.shown += PAGE; renderAppsBody(); } }, 'Show more') : h('span')));
  }

  function portalQuestions(p) {
    let q = p.questions != null ? p.questions : (p.questions_left != null ? p.questions_left : p.questions_json);
    if (typeof q === 'string') { try { q = JSON.parse(q); } catch (e) { q = q ? [q] : []; } }
    if (!Array.isArray(q)) q = [];
    return q.map(x => typeof x === 'string' ? x : str(x && (x.label || x.question || x.text || x.name)))
      .map(x => x.replace(/\s*[—–-]+\s*left (?:empty )?for you\.?$/i, '')).filter(Boolean);
  }

  /* ═════════════════════════ Questions for you ═════════════════════════ */

  const Q_FILTERS = [['open', 'To answer'], ['answered', 'Answered'], ['mine', 'Always ask me']];
  const Q_TYPE = { yes_no: 'Yes / No', number: 'Number', choice: 'Pick one', multi: 'Pick any', date: 'Date', textarea: 'Long text', checkbox: 'Checkbox', text: 'Text', other: 'Other' };
  const LANE_NAME = { easy_apply: 'Easy Apply', portal: 'Portal form' };

  async function qSave(row, patch, okMsg) {
    try {
      await api('/api/questions/' + row.id, { method: 'POST', body: patch, label: 'Saving your answer', quiet: true });
      if (S.qDrafts) delete S.qDrafts[row.id];
      if (okMsg) toast(okMsg, 'ok', 'qsave');
      S.qEditing = null;
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
      await loadQuestions(true);
    } catch (e) { apiErrorToast('Saving your answer', e); }
  }
  async function qAdd(question, answer) {
    try {
      await api('/api/questions', { method: 'POST', body: { question: question, answer: answer }, label: 'Adding a question', quiet: true });
      toast('Saved. Forms will use this answer.', 'ok', 'qsave');
      S.qAdding = false; S.qDraft = { q: '', a: '' };
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
      await loadQuestions(true);
    } catch (e) { apiErrorToast('Adding a question', e); }
  }

  function qAnswerBox(row) {
    const opts = Array.isArray(row.options) ? row.options : [];
    const yesNo = row.field_type === 'yes_no' && !opts.length ? ['Yes', 'No'] : opts;
    const long = row.field_type === 'textarea';
    const input = long
      ? h('textarea', { class: 'q-input', rows: 3, placeholder: 'Your answer', 'aria-label': 'Answer for: ' + row.question })
      : h('input', { class: 'q-input', type: row.field_type === 'number' ? 'text' : 'text', inputmode: row.field_type === 'number' ? 'decimal' : null,
          placeholder: row.field_type === 'date' ? 'e.g. Within 15 days / 2026-11-03' : 'Your answer', 'aria-label': 'Answer for: ' + row.question });
    S.qDrafts = S.qDrafts || {};
    if (S.qDrafts[row.id] != null) input.value = S.qDrafts[row.id];
    else if (row.status === 'answered' && row.answer) input.value = row.answer;
    input.addEventListener('input', () => { S.qDrafts[row.id] = input.value; });
    input.addEventListener('focus', () => { S.qEditing = row.id; });
    input.addEventListener('blur', () => { if (S.qEditing === row.id) S.qEditing = null; });
    const save = () => {
      const v = input.value.trim();
      if (!v) { toast('Type or pick an answer first.', 'info', 'qempty'); input.focus(); return; }
      qSave(row, { answer: v }, row.status === 'answered' ? 'Answer updated.' : 'Saved. Next forms will fill this for you.');
    };
    input.addEventListener('keydown', e => { if (e.key === 'Enter' && (!long || e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); } if (e.key === 'Escape') { S.qEditing = null; input.blur(); } });
    const picks = yesNo.length ? h('div', { class: 'q-picks' }, yesNo.slice(0, 12).map(o => h('button', { type: 'button', class: 'chip', 'aria-pressed': String(input.value === o),
      onclick: () => { input.value = o; save(); } }, o))) : null;
    return h('div', { class: 'q-answer' }, picks, h('div', { class: 'q-row' }, input,
      h('button', { type: 'button', class: 'btn btn-sm btn-primary', onclick: save }, row.status === 'answered' ? 'Update' : 'Save')));
  }

  function qItem(row) {
    const companies = row.last_company ? ' · last at ' + splitCompany(row.last_company)[0] : '';
    const seen = num(row.times_seen) ? 'Seen in ' + plural(num(row.times_seen), 'form') + companies : 'Added by you';
    const meta = h('div', { class: 'q-meta' },
      h('span', { class: 'q-type' }, Q_TYPE[row.field_type] || 'Text'),
      h('span', null, seen),
      row.last_lane && LANE_NAME[row.last_lane] ? h('span', null, LANE_NAME[row.last_lane]) : null,
      row.source === 'agent' ? h('span', { class: 'q-learned', title: 'Job Hunter filled this on a form and saved it. Change it if it is not right.' }, 'Learned from a form') : null,
      num(row.times_used) ? h('span', { class: 'q-used' }, 'Auto-filled ' + plural(num(row.times_used), 'time')) : null);
    const del = h('button', { type: 'button', class: 'btn btn-sm btn-ghost q-del', title: 'Delete this question' }, 'Delete');
    del.addEventListener('click', () => {
      if (del.dataset.armed) { qSave(row, { delete: true }, 'Deleted.'); return; }
      del.dataset.armed = '1'; del.textContent = 'Sure?'; del.classList.add('is-armed');
      setTimeout(() => { if (del.isConnected) { delete del.dataset.armed; del.textContent = 'Delete'; del.classList.remove('is-armed'); } }, 3000);
    });
    let side;
    if (row.status === 'mine') {
      side = h('div', { class: 'q-actions' },
        h('span', { class: 'muted q-mine' }, 'Left blank for you on every form'),
        h('button', { type: 'button', class: 'btn btn-sm', onclick: () => qSave(row, { status: 'open' }) }, 'Answer it instead'), del);
    } else {
      side = h('div', { class: 'q-actions' },
        h('button', { type: 'button', class: 'btn btn-sm btn-ghost', title: 'Never auto-fill this one; always leave it for me',
          onclick: () => qSave(row, { status: 'mine' }, 'OK — this one stays blank for you.') }, 'Always ask me'), del);
    }
    return h('li', { class: 'q-item q-' + row.status, dataset: { id: row.id } },
      h('div', { class: 'q-head' }, h('div', { class: 'q-text' }, row.question), meta),
      row.status === 'mine' ? null : qAnswerBox(row), side);
  }


  /* ═════════════════════════ Agencies page (separate view) ═════════════════════════ */
  // Two tracks, tracked and pitched separately: agencies outside India (US first) and agencies in India.
  const AG_TRACKS = {
    outside: { name: 'Outside India', sub: 'US C2C vendors first, then UK, EU, Middle East, APAC' },
    india: { name: 'India', sub: 'Indian consultancies staffing foreign client projects' },
  };
  const AG_STATUS = { new: 'To pitch', pitched: 'Pitched', replied: 'Replied', no_contact: 'No contact yet', not_fit: 'Not a fit',
    paused: 'Paused', manual: 'Manual' };
  const AG_FILTERS = [['all', 'All'], ['new', 'To pitch'], ['pitched', 'Approached'], ['replied', 'Replied'],
    ['no_contact', 'No contact yet'], ['aside', 'Set aside']];
  const AG_AREA = { all: 'All areas', us: 'US', non_us: 'UK, EU and other' };
  const AG_PRIORITY = { A: 'Verified fit', B: 'Agency outside the US', C: 'US C2C vendor', D: 'Seen in your jobs', U: 'Added by you' };
  const AG_TAG = { A: 'Top pick', B: 'Abroad', C: 'US', D: 'From your jobs', U: 'Added by you' };

  function setView(v, push) {
    v = ['agencies', 'board'].includes(v) ? v : 'jobs';
    S.view = v;
    $('#page-jobs').hidden = v !== 'jobs';
    $('#page-agencies').hidden = v !== 'agencies';
    $('#page-board').hidden = v !== 'board';
    document.querySelectorAll('.vs-btn').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === v)));
    const run = $('#btn-run'); if (run) run.classList.toggle('vs-hide', v !== 'jobs');
    if (push) { try { history.replaceState(null, '', v === 'jobs' ? '#' : '#' + v); } catch (e) { /* file:// */ } }
    if (v === 'agencies') { if (S.agencies) renderAgencies(); loadAgencies(!!S.agencies); }
    if (v === 'board') { renderBoard(); loadBoard(!!S.board); }
  }
  const APP_BUILD = '2026-10-30.6';   // must match jh/__init__.py BUILD
  async function checkBuild() {
    let b = null;
    try { const r = await fetch('/api/version', { cache: 'no-store' }); if (r.ok) b = (await r.json()).build; } catch (e) { return; }
    const el = $('#stale'); if (!el) return;
    if (b === APP_BUILD) { el.hidden = true; return; }
    el.replaceChildren(h('b', null, 'Restart Job Hunter to load the update. '),
      'This page is newer than the app running in the background (', b ? 'running ' + b : 'running an older version', ', page ' + APP_BUILD + '). ',
      'Until you restart, new features such as Agencies won’t work. Close the app (or run the Stop command) and open the Job Hunter icon again.');
    el.hidden = false;
  }
  async function loadAgencies(quiet) {
    const d = await tryApi('/api/agencies', { label: 'Agencies', quiet: quiet });
    if (d === null && S.agencies) return;
    S.agencies = d || { agencies: [], tracks: {}, settings: {} };
    if (!S.agTrack) S.agTrack = (S.agencies.settings || {}).focus === 'india' ? 'india' : 'outside';
    if (S.view === 'agencies' && !agBusy()) renderAgencies();
  }
  function agBusy() {
    const el = document.activeElement, page = $('#page-agencies');
    return S.view === 'agencies' && !!el && !!page && page.contains(el) && isTextEntry(el);
  }
  // A focused text box / number box / dropdown must not be rebuilt under the cursor; a focused checkbox can be.
  function isTextEntry(el) {
    if (el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') return true;
    return el.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit'].includes(String(el.type).toLowerCase());
  }
  async function agSetStatus(a, status) {
    try {
      await api('/api/agencies/' + a.id, { method: 'POST', body: { status: status }, label: 'Updating agency' });
      const words = a.kind === 'marketplace' ? (status === 'paused' ? 'marked as registered' : 'moved back to your to-do list')
        : { not_fit: 'marked not a fit', paused: 'paused', new: 'added back to the pitch list', manual: 'set to manual' }[status];
      toast(a.name + ' ' + (words || 'updated') + '.', 'ok');
      loadAgencies(true);
    } catch (e) { /* api() shows the error */ }
  }
  async function agSetRegion(a, region) {
    try {
      await api('/api/agencies/' + a.id, { method: 'POST', body: { region: region }, label: 'Moving agency' });
      toast(a.name + ' moved to ' + (region === 'india' ? 'India' : 'outside India') + '.', 'ok');
      S.agOpen = null; loadAgencies(true);
    } catch (e) { /* shown */ }
  }
  async function agSaveSettings(patch, quiet) {
    const cur = (S.agencies && S.agencies.settings) || {};
    const next = Object.assign({}, cur, patch);
    await api('/api/settings', { method: 'POST', body: { agencies: next }, label: 'Saving agency settings' });
    if (S.agencies) S.agencies.settings = next;
    if (!quiet) toast('Saved.', 'ok');
  }
  async function agPitch() {
    if (S.running) { toast('A run is going. Pitch agencies when it ends.', 'info', 'agbusy'); return; }
    const btn = $('#ag-pitch'); if (btn) btn.disabled = true;
    try {
      await agSaveSettings({ focus: S.agTrack }, true);
      const d = await api('/api/run', { method: 'POST', body: { trigger: 'button', lanes: ['agencies'] }, label: 'Pitching agencies', quiet: true });
      S.running = true; S.runStatus = 'running';
      if (d && d.run_id != null) { S.runId = num(d.run_id); resetLanes('queued'); addDivider('Run #' + d.run_id + ': agencies'); }
      S.runStartedAt = new Date().toISOString();
      renderTop(); renderLanes();
      toast('Pitching the next ' + num((S.agencies.settings || {}).per_run, 5) + ' agencies ' + (S.agTrack === 'india' ? 'in India' : 'outside India') + '. Progress shows in the Jobs view’s live feed.', 'ok');
      setTimeout(() => loadSummary(true), 800);
    } catch (e) {
      if (e.status === 409) toast('A run is already in progress.', 'info', 'run409');
      else if (e.status === 400) { checkBuild(); toast('The app running in the background is an older version. Restart Job Hunter, then pitch again.', 'bad', 'agold'); }
      else apiErrorToast('Pitching agencies', e);
    } finally { if (btn) btn.disabled = false; renderAgencies(); }
  }
  function agRows(track) {
    return ((S.agencies && S.agencies.agencies) || []).filter(a => a.kind !== 'marketplace' && (a.track || (a.region === 'india' ? 'india' : 'outside')) === track);
  }
  function agMatch(a, f, area, q) {
    const via = num(a.via_jobs) > 0;
    if (q) { const hay = (str(a.name) + ' ' + str(a.kind) + ' ' + str(a.notes) + ' ' + str(a.known_contacts)).toLowerCase(); if (!hay.includes(q)) return false; }
    if (f === 'new' && via) return false;
    if (f === 'pitched' && via && (a.status === 'new' || a.status === 'manual')) return area === 'all' || a.region === area;
    if (f === 'replied' && a.via_replied) return area === 'all' || a.region === area;
    if (f === 'aside' && via && a.status === 'manual') return false;
    if (f === 'aside' ? !['not_fit', 'paused', 'manual'].includes(a.status) : (f !== 'all' && a.status !== f)) return false;
    if (area !== 'all' && a.region !== area) return false;
    return true;
  }
  function agTrackCard(id) {
    const t = ((S.agencies || {}).tracks || {})[id] || {};
    const total = num(t.total), done = num(t.approached);
    const pct = total ? Math.round(100 * done / total) : 0;
    const on = S.agTrack === id;
    return h('button', { type: 'button', class: 'ag-track' + (on ? ' on' : ''), 'aria-pressed': String(on),
      onclick: () => { if (S.agTrack !== id) { S.agTrack = id; S.agFilter = 'all'; S.agArea = 'all'; S.agShown = 0; renderAgencies(); } } },
      h('span', { class: 'ag-track-name' }, AG_TRACKS[id].name),
      h('span', { class: 'ag-track-sub' }, AG_TRACKS[id].sub),
      h('span', { class: 'ag-track-big' }, h('b', { class: 'tnum' }, done), h('span', null, ' of ' + total + ' agencies approached')),
      h('span', { class: 'ag-meter', role: 'img', 'aria-label': pct + '% approached' }, h('span', { style: { width: Math.max(pct, done ? 2 : 0) + '%' } })),
      h('span', { class: 'ag-track-stats' },
        h('span', null, h('b', { class: 'tnum' }, num(t.replied)), ' replied'),
        h('span', null, h('b', { class: 'tnum' }, num(t.people)), num(t.people) === 1 ? ' agency pitch sent' : ' agency pitches sent'),
        h('span', null, h('b', { class: 'tnum' }, num(t.to_pitch)), ' to pitch')));
  }
  function agItem(a) {
    const site = safeHref(a.website);
    const open = S.agOpen === a.id;
    const sent = (a.recruiters || []);
    const via = num(a.via_jobs);
    const who = !sent.length && via
      ? [h('span', { class: 'ag-via' }, 'In touch through ' + via + (via === 1 ? ' job email' : ' job emails'), a.via_replied ? h('span', { class: 'ag-st ag-st-replied' }, 'replied') : null)]
      : sent.length
      ? sent.map(r => h('span', { class: 'ag-person', title: str(r.email) }, str(r.name) || str(r.email), r.email_status ? h('span', { class: 'ag-dot ag-dot-' + str(r.email_status), 'aria-label': str(r.email_status) }) : null))
      : [h('span', { class: 'faint' }, a.known_contacts ? 'Known: ' + str(a.known_contacts).split(';')[0].slice(0, 70) : 'Not contacted yet')];
    const actions = [];
    if (['new', 'pitched', 'no_contact', 'replied'].includes(a.status)) {
      actions.push(h('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => agSetStatus(a, 'paused') }, 'Pause'));
      actions.push(h('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => agSetStatus(a, 'not_fit') }, 'Not a fit'));
    } else if (a.past_client) {
      actions.push(h('span', { class: 'faint ag-small' }, 'Past client: you email them yourself.'));
    } else {
      actions.push(h('button', { type: 'button', class: 'btn btn-sm', onclick: () => agSetStatus(a, 'new') }, 'Pitch again'));
    }
    const row = h('li', { class: 'ag-row' + (open ? ' open' : '') },
      h('button', { type: 'button', class: 'ag-main', 'aria-expanded': String(open), onclick: () => { S.agOpen = open ? null : a.id; renderAgencies(); } },
        h('span', { class: 'ag-name' }, str(a.name), a.priority && AG_PRIORITY[a.priority] ? h('span', { class: 'ag-tag ag-tag-' + a.priority, title: AG_PRIORITY[a.priority] }, AG_TAG[a.priority]) : null),
        h('span', { class: 'ag-kind' }, str(a.kind)),
        h('span', { class: 'ag-who' }, who),
        h('span', { class: 'ag-state' }, (a.status === 'new' || a.status === 'manual') && via
            ? h('span', { class: 'ag-st ag-st-pitched' }, a.via_replied ? 'Replied (jobs)' : 'In touch (jobs)')
            : h('span', { class: 'ag-st ag-st-' + str(a.status) }, AG_STATUS[a.status] || str(a.status)),
          a.next_touch && ['pitched', 'no_contact'].includes(a.status) ? h('span', { class: 'faint ag-next' }, 'again ' + str(a.next_touch)) : null)));
    if (open) {
      row.append(h('div', { class: 'ag-detail' },
        h('dl', null,
          h('dt', null, 'Website'), h('dd', null, site ? h('a', { href: site, target: '_blank', rel: 'noopener noreferrer' }, site.replace(/^https?:\/\//, '')) : '—'),
          h('dt', null, 'Where from'), h('dd', null, str(a.source) || '—'),
          h('dt', null, 'People'), h('dd', null, sent.length ? sent.map(r => h('div', null, str(r.name), r.title ? ', ' + str(r.title) : '', ' · ', str(r.email), r.email_status ? ' (' + str(r.email_status) + ')' : '')) : (str(a.known_contacts) || 'Found on LinkedIn when this agency is pitched')),
          h('dt', null, 'Notes'), h('dd', null, str(a.notes) || '—')),
        h('div', { class: 'ag-actions' }, actions,
          h('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => agSetRegion(a, a.region === 'india' ? 'us' : 'india') },
            a.region === 'india' ? 'Move to outside India' : 'Move to India'))));
    }
    return row;
  }
  function renderAgencies() {
    const page = $('#page-agencies');
    if (!page) return;
    if (!S.agencies) { page.replaceChildren(h('div', { class: 'ag-wrap' }, skeletonRows(5))); return; }
    const track = S.agTrack || 'outside';
    const set = S.agencies.settings || {};
    const f = S.agFilter || 'all', area = S.agArea || 'all', q = (S.agQ || '').trim().toLowerCase();
    const rows = agRows(track);
    const shownRows = rows.filter(a => agMatch(a, f, track === 'india' ? 'all' : area, q));
    const lim = S.agShown || 50;
    const perRun = num(set.per_run, 5), cap = num(set.daily_cap, 15);

    const head = h('div', { class: 'ag-head' },
      h('div', null, h('h1', { id: 'h-agencies' }, 'Agencies'),
        h('p', { class: 'ag-lede' }, 'Staffing firms that can put you on Salesforce contracts for foreign clients. Pitched on C2C through Trysyntx, separate from job applications.')),
      h('div', { class: 'ag-cta' },
        h('button', { type: 'button', id: 'ag-pitch', class: 'btn btn-primary', disabled: S.running ? true : null, onclick: agPitch },
          'Pitch next ' + perRun + (track === 'india' ? ' in India' : ' outside India')),
        h('span', { class: 'faint' }, S.running ? 'A run is going. This waits until it ends.' : num(S.agencies.sent_today) + ' of ' + cap + ' agency emails sent today')));

    const tracks = h('div', { class: 'ag-tracks', role: 'group', 'aria-label': 'Which agencies' }, agTrackCard('outside'), agTrackCard('india'));

    const count = id => rows.filter(a => agMatch(a, id, track === 'india' ? 'all' : area, q)).length;
    const search = h('input', { type: 'search', class: 'ag-search', placeholder: 'Search agencies', 'aria-label': 'Search agencies', value: S.agQ || '' });
    search.addEventListener('input', () => { S.agQ = search.value; S.agShown = 0; clearTimeout(S.agQT); S.agQT = setTimeout(() => { renderAgencies(); const el = $('.ag-search'); if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }, 200); });
    const filters = h('div', { class: 'ag-filters' },
      h('div', { class: 'chips', role: 'group', 'aria-label': 'Status' }, AG_FILTERS.map(([id, label]) =>
        h('button', { type: 'button', class: 'chip', 'aria-pressed': String(f === id), onclick: () => { S.agFilter = id; S.agShown = 0; renderAgencies(); } },
          label, h('span', { class: 'n' }, count(id))))),
      h('div', { class: 'ag-filters-right' },
        track === 'outside' ? h('select', { 'aria-label': 'Area', onchange: e => { S.agArea = e.target.value; S.agShown = 0; renderAgencies(); } },
          Object.entries(AG_AREA).map(([v, l]) => h('option', { value: v, selected: area === v ? true : null }, l))) : null,
        search));

    let list;
    if (!rows.length && track === 'india') {
      list = h('div', { class: 'empty' }, h('div', { class: 'empty-title' }, 'No Indian agencies yet'),
        h('div', { class: 'empty-text' }, 'Indian consultancies show up here as the job lanes meet them (posts that say "our client", "C2C" or "staffing" from India). You can also add one yourself with "Add an agency".'));
    } else if (!shownRows.length) {
      list = h('div', { class: 'empty' }, h('div', { class: 'empty-title' }, 'Nothing matches'), h('div', { class: 'empty-text' }, 'Try another status or clear the search.'));
    } else {
      list = h('div', null,
        h('div', { class: 'ag-cols', 'aria-hidden': 'true' }, h('span', null, 'Agency'), h('span', null, 'Type'), h('span', null, 'Recruiters'), h('span', null, 'Status')),
        h('ul', { class: 'ag-list' }, shownRows.slice(0, lim).map(agItem)),
        shownRows.length > lim ? h('div', { class: 'more' }, h('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => { S.agShown = lim + 50; renderAgencies(); } }, 'Show ' + Math.min(50, shownRows.length - lim) + ' more of ' + (shownRows.length - lim))) : null);
    }
    const main = h('section', { class: 'panel ag-listpanel', 'aria-label': AG_TRACKS[track].name + ' agencies' }, filters, list);

    // side column
    const perIn = h('input', { type: 'number', min: '1', max: '20', value: String(perRun), class: 'ag-num', id: 'ag-per', onchange: e => agSaveSettings({ per_run: Math.max(1, Math.min(20, Math.round(num(e.target.value, 5)))) }).then(() => renderAgencies()).catch(() => {}) });
    const capIn = h('input', { type: 'number', min: '0', max: '50', value: String(cap), class: 'ag-num', id: 'ag-cap', onchange: e => agSaveSettings({ daily_cap: Math.max(0, Math.min(50, Math.round(num(e.target.value, 15)))) }).then(() => renderAgencies()).catch(() => {}) });
    const sets = h('section', { class: 'panel ag-side-card' }, h('h2', null, 'Pace'),
      h('label', { class: 'ag-field', for: 'ag-per' }, h('span', null, 'Agencies per click'), perIn),
      h('label', { class: 'ag-field', for: 'ag-cap' }, h('span', null, 'Agency emails per day'), capIn),
      h('p', { class: 'faint ag-small' }, 'Agency emails have their own daily limit, so they never use up your job-application emails.'));
    const how = h('section', { class: 'panel ag-side-card' }, h('h2', null, 'What one pitch does'),
      h('ol', { class: 'ag-steps' },
        h('li', null, 'Picks the next agencies on the selected list: top picks first, then US C2C vendors.'),
        h('li', null, 'Finds one or two Salesforce recruiters at each on LinkedIn and gets their work email with SalesQL.'),
        h('li', null, 'Sends a short C2C pitch with your resume, then follow-ups on day 3 and 7. No reply? It tries again in 90 days.')));
    const market = ((S.agencies.agencies) || []).filter(a => a.kind === 'marketplace');
    const mk = h('section', { class: 'panel ag-side-card' }, h('h2', null, 'Sign up yourself'),
      h('p', { class: 'faint ag-small' }, 'These need your own account, test or login, so Job Hunter can’t do them for you.'),
      h('ul', { class: 'ag-market' }, market.map(m => {
        const done = m.status === 'paused';
        const link = safeHref(m.website);
        return h('li', { class: done ? 'done' : '' },
          h('label', null, h('input', { type: 'checkbox', checked: done ? true : null, onchange: e => agSetStatus(m, e.target.checked ? 'paused' : 'manual') }),
            h('span', null, str(m.name))),
          link ? h('a', { href: link, target: '_blank', rel: 'noopener noreferrer', class: 'ag-open', 'aria-label': 'Open ' + str(m.name) }, 'Open') : null);
      })));
    const add = h('section', { class: 'panel ag-side-card' }, h('h2', null, 'Add an agency'));
    const ni = h('input', { class: 'q-input', placeholder: 'Agency name', 'aria-label': 'Agency name' });
    const wi = h('input', { class: 'q-input', placeholder: 'Website, e.g. acmestaffing.com', 'aria-label': 'Website' });
    const ri = h('select', { 'aria-label': 'Where it is' }, [['us', 'US'], ['non_us', 'UK, EU or other'], ['india', 'India']].map(([v, l]) => h('option', { value: v, selected: (track === 'india' ? 'india' : 'us') === v ? true : null }, l)));
    const addBtn = h('button', { type: 'button', class: 'btn btn-sm', onclick: async () => {
      if (!ni.value.trim()) { ni.focus(); return; }
      try {
        await api('/api/agencies', { method: 'POST', body: { name: ni.value.trim(), website: wi.value.trim(), region: ri.value }, label: 'Adding agency' });
        toast('Added ' + ni.value.trim() + '. It’s first in line for the next pitch.', 'ok');
        loadAgencies(true);
      } catch (e) { /* shown */ }
    } }, 'Add agency');
    add.append(h('div', { class: 'ag-addform' }, ni, wi, ri, addBtn));
    const side = h('aside', { class: 'ag-side' }, sets, how, mk, add);
    page.replaceChildren(h('div', { class: 'ag-wrap' }, head, tracks, h('div', { class: 'ag-grid' }, main, side)));
  }

  /* ═════════════════════════ Job board page (separate view) ═════════════════════════ */
  // Every remote Salesforce job from the last 24/48 h, fetched by Job Hunter's own Python (no AI) — then one click
  // (or auto-fill) to fill its application. Submitting still needs "Submit selected".
  const JB_STATE = { new: ['New', 'new'], queued: ['Queued', 'queued'], filling: ['Filling…', 'filling'], needs_you: ['Needs you', 'needs'],
    ready: ['Ready to submit', 'ready'], submitting: ['Submitting…', 'ready'], submitted: ['Submitted', 'done'], emailed: ['Emailed', 'mail'],
    applied: ['Applied by you', 'done'], skipped: ['Skipped', 'muted'], hidden: ['Hidden', 'muted'] };
  const JB_KIND = { portal: 'Job portal form', login_portal: 'Portal login', company_site: 'Company site', easy_apply: 'Easy Apply',
    email: 'Email', board: 'Board page', unknown: 'LinkedIn', closed: 'Closed' };
  const JB_ELIG = { open: ['Open to you', 'open'], check: ['Check where', 'check'], locked: ['Region locked', 'locked'], blocked: ['Filtered', 'locked'] };
  const JB_SRC = { linkedin: 'LinkedIn', careers: 'Company portal', himalayas: 'Himalayas', jobicy: 'Jobicy', hn: 'Hacker News',
    arbeitnow: 'Arbeitnow', wwr: 'We Work Remotely' };
  const JB_FILTERS = [['todo', 'To apply'], ['ready', 'Ready'], ['needs', 'Needs you'], ['applied', 'Applied'], ['filtered', 'Filtered out'], ['hidden', 'Hidden']];
  const JB_APPLIED = ['submitted', 'submitting', 'emailed', 'applied'];
  function jbMatch(x, f) {
    const good = x.elig === 'open' || x.elig === 'check';
    if (f === 'hidden') return x.state === 'hidden';
    if (x.state === 'hidden') return false;
    if (f === 'filtered') return !good && !JB_APPLIED.includes(x.state);
    if (f === 'ready') return x.state === 'ready';
    if (f === 'needs') return x.state === 'needs_you';
    if (f === 'applied') return JB_APPLIED.includes(x.state);
    return good && ['new', 'queued', 'filling'].includes(x.state);
  }
  function jbFresh(x) {
    const d = parseTs(x.posted_at);
    return !!d && Date.now() - d.getTime() < 3 * 3600e3 && (x.applicants == null || num(x.applicants) < 25) && x.elig === 'open';
  }
  async function loadBoard(quiet) {
    const hours = S.jbHours || 24;
    const d = await tryApi('/api/jobboard?hours=48', { label: 'Job board', quiet: quiet });
    if (d === null && S.board) return;
    S.board = d || { listings: [], status: {}, sources: {} };
    S.jbHours = hours;
    renderBoardBadge();
    if (S.view === 'board' && !jbBusy()) renderBoard();
    clearTimeout(S.jbPoll);
    if (S.board.status && S.board.status.refreshing) S.jbPoll = setTimeout(() => loadBoard(true), 4000);
  }
  function jbBusy() {
    const el = document.activeElement, page = $('#page-board');
    return S.view === 'board' && !!el && !!page && page.contains(el) && isTextEntry(el);
  }
  function jbInWindow(x) {
    const d = parseTs(x.posted_at);
    return !d || Date.now() - d.getTime() <= (S.jbHours || 24) * 3600e3 || ['queued', 'filling', 'needs_you', 'ready', 'submitting'].includes(x.state);
  }
  function renderBoardBadge() {
    const b = document.querySelector('.vs-btn[data-view="board"] .vs-n');
    if (!b || !S.board) return;
    const n = (S.board.listings || []).filter(x => jbInWindow(x) && jbMatch(x, 'todo') && x.elig === 'open').length;
    b.textContent = n ? String(n) : '';
    b.hidden = !n;
  }
  async function jbRefresh(linkedin) {
    try {
      await api('/api/jobboard/refresh', { method: 'POST', body: { linkedin: !!linkedin }, label: 'Refreshing the Job board', quiet: true });
      if (S.board) S.board.status = Object.assign({}, S.board.status, { refreshing: true });
      renderBoard();
      toast('Fetching fresh jobs' + (linkedin ? ' (incl. LinkedIn)' : '') + '. This takes a minute or two — the list updates by itself.', 'info', 'jbref');
      clearTimeout(S.jbPoll); S.jbPoll = setTimeout(() => loadBoard(true), 4000);
    } catch (e) {
      if (e.status === 404) { checkBuild(); toast('Restart Job Hunter to use the Job board.', 'bad', 'jbold'); }
      else apiErrorToast('Refreshing the Job board', e);
    }
  }
  async function jbApply(ids) {
    if (!ids.length) return;
    try {
      const d = await api('/api/jobboard/apply', { method: 'POST', body: { ids: ids }, label: 'Starting applications', quiet: true });
      const ok = (d.queued || []).length, bad = (d.skipped || []).length;
      toast(ok ? plural(ok, 'application') + (d.waiting ? ' queued — filling starts when the current run ends.' : ' being filled now. Watch them move to Ready or Needs you.')
        + (bad ? ' ' + bad + ' skipped (' + str(d.skipped[0].reason) + ').' : '') : 'Nothing queued: ' + str(((d.skipped || [])[0] || {}).reason), ok ? 'ok' : 'info');
      S.jbSel = new Set();
      if (d.run_id != null) { S.running = true; S.runStatus = 'running'; S.runId = num(d.run_id); resetLanes('queued'); addDivider('Run #' + d.run_id + ': Job board'); renderTop(); renderLanes(); }
      loadBoard(true);
    } catch (e) {
      if (e.status === 404) { checkBuild(); toast('Restart Job Hunter to use the Job board.', 'bad', 'jbold'); }
      else apiErrorToast('Starting applications', e);
    }
  }
  async function jbSetStatus(x, status) {
    try {
      await api('/api/jobboard/' + x.id, { method: 'POST', body: { status: status }, label: 'Updating job' });
      toast({ hidden: 'Hidden.', applied_self: 'Marked as applied by you.', new: 'Back on the list.' }[status] || 'Updated.', 'ok');
      loadBoard(true);
    } catch (e) { /* shown */ }
  }
  async function jbSubmit(xs) {
    const ids = xs.map(x => x.portal_id).filter(v => v != null);
    if (!ids.length) return;
    try {
      const d = await api('/api/portal/submit', { method: 'POST', body: { ids: ids, seen: Object.fromEntries(xs.map(x => [x.portal_id, str(x.portal_seen)])) }, label: 'Submitting applications', quiet: true });
      const ok = (d.approved || []).length, bad = (d.rejected || []).length;
      toast(ok ? plural(ok, 'application') + (d.queued ? ' approved — submitting as soon as the current run ends.' : ' approved — submitting now.') + (bad ? ' ' + bad + ' could not be approved (' + str(d.rejected[0].reason) + ').' : '')
        : 'Nothing could be approved: ' + str(((d.rejected || [])[0] || {}).reason), ok ? 'ok' : 'info');
      S.jbSel = new Set(); loadBoard(true); loadPortal(true);
    } catch (e) { apiErrorToast('Submitting applications', e); }
  }
  async function jbSaveSettings(patch) {
    try {
      const d = await api('/api/jobboard/settings', { method: 'POST', body: patch, label: 'Saving Job board settings' });
      if (S.board) S.board.status = Object.assign({}, S.board.status, { settings: d });
      toast('Saved.', 'ok', 'jbset'); renderBoard();
    } catch (e) { /* shown */ }
  }
  // Two-step button for actions that send something: the first click arms it, the second (within 4 s) runs it.
  function armButton(cls, label, armedLabel, fn) {
    let armed = false, t = 0;
    const b = h('button', { type: 'button', class: cls }, label);
    b.addEventListener('click', () => {
      if (!armed) { armed = true; b.textContent = armedLabel; t = setTimeout(() => { armed = false; b.textContent = label; }, 4000); return; }
      clearTimeout(t); b.disabled = true;
      Promise.resolve(fn()).finally(() => { if (b.isConnected) { armed = false; b.disabled = false; b.textContent = label; } });
    });
    return b;
  }
  function jbItem(x) {
    const open = S.jbOpen === x.id;
    const st = JB_STATE[x.state] || [x.state, 'muted'];
    const el = JB_ELIG[x.elig] || ['', ''];
    const sel = S.jbSel || (S.jbSel = new Set());
    const canPick = ['new', 'needs_you', 'ready'].includes(x.state);
    const cb = h('input', { type: 'checkbox', class: 'jb-cb', 'aria-label': 'Select ' + str(x.title) + ' at ' + str(x.company), checked: sel.has(x.id) ? true : null,
      disabled: canPick ? null : true, onchange: e => { if (e.target.checked) sel.add(x.id); else sel.delete(x.id); renderBoard(); } });
    const fresh = jbFresh(x);
    const srcs = (x.sources && x.sources.length ? x.sources : [x.source]).map(s => h('span', { class: 'jb-src jb-src-' + str(s) }, JB_SRC[s] || str(s)));
    const row = h('li', { class: 'jb-row' + (open ? ' open' : '') + (fresh ? ' fresh' : '') },
      h('span', { class: 'jb-pick' }, cb),
      h('button', { type: 'button', class: 'jb-main', 'aria-expanded': String(open), onclick: () => { S.jbOpen = open ? null : x.id; renderBoard(); } },
        h('span', { class: 'jb-title' }, h('b', { title: str(x.title) }, str(x.title)), fresh ? h('span', { class: 'jb-fresh', title: 'Posted under 3 hours ago, few applicants, open to India' }, 'Fresh') : null,
          h('span', { class: 'jb-co', title: str(x.company) + (x.region ? ' · ' + str(x.region) : '') }, str(x.company), x.region ? ' · ' + str(x.region) : '')),
        h('span', { class: 'jb-when' }, relEl(x.posted_at), x.applicants != null ? h('span', { class: 'faint jb-appl' }, num(x.applicants) + ' applicants') : null),
        h('span', { class: 'jb-how' }, h('span', { class: 'jb-kind' }, JB_KIND[x.apply_kind] || str(x.apply_kind), x.platform && !['linkedin', 'email'].includes(x.platform) ? h('span', { class: 'faint' }, ' · ' + str(x.platform)) : null), h('span', { class: 'jb-srcs' }, srcs)),
        h('span', { class: 'jb-flags' }, el[0] ? h('span', { class: 'jb-elig jb-elig-' + el[1], title: str(x.reason) }, el[0]) : null,
          h('span', { class: 'jb-st jb-st-' + st[1] }, st[0]))));
    if (open) {
      const jobLink = safeHref(x.url), formLink = safeHref(x.apply_url);
      const acts = [];
      if (['new', 'needs_you'].includes(x.state) && x.apply_kind !== 'closed') acts.push(h('button', { type: 'button', class: 'btn btn-sm btn-primary', onclick: () => jbApply([x.id]) }, x.state === 'needs_you' ? 'Try filling again' : 'Fill application'));
      if (x.state === 'ready') acts.push(armButton('btn btn-sm btn-primary', 'Submit this application', 'Submit it? Click again', () => jbSubmit([x])));
      if (!['submitted', 'submitting', 'applied', 'hidden', 'filling', 'queued'].includes(x.state)) acts.push(h('button', { type: 'button', class: 'btn btn-sm', onclick: () => jbSetStatus(x, 'applied_self') }, 'I applied myself'));
      if (x.state === 'hidden' || x.state === 'applied') acts.push(h('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => jbSetStatus(x, 'new') }, 'Put back on the list'));
      else if (!['filling', 'queued', 'submitting'].includes(x.state)) acts.push(h('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => jbSetStatus(x, 'hidden') }, 'Hide'));
      const needs = [];
      if (x.state === 'needs_you') {
        const items = (x.questions || []).slice();
        if (!items.length && x.skip_reason) items.push(str(x.skip_reason).replace(/^needs you:\s*/i, ''));
        if (!items.length && x.note) items.push(str(x.note));
        needs.push(h('div', { class: 'jb-needs' }, h('b', null, 'Left for you'),
          items.length ? h('ul', null, items.map(q => h('li', null, str(q)))) : h('p', { class: 'faint' }, 'Open the form and finish it.'),
          h('p', { class: 'faint jb-small' }, 'Answer questions once in the Questions tab and they are reused on every form. Signed in now? Click "Try filling again".')));
      }
      row.append(h('div', { class: 'jb-detail' },
        h('div', null,
          h('dl', null,
            h('dt', null, 'Location'), h('dd', null, str(x.location) || '—'),
            h('dt', null, 'Where they hire'), h('dd', null, el[0] || '—', x.reason ? h('span', { class: 'faint' }, ' — ' + str(x.reason)) : null),
            x.salary ? h('dt', null, 'Pay') : null, x.salary ? h('dd', null, str(x.salary)) : null,
            h('dt', null, 'Apply'), h('dd', null, JB_KIND[x.apply_kind] || str(x.apply_kind), x.emails && x.emails.length ? ' · ' + x.emails.join(', ') : ''),
            x.state === 'skipped' && x.skip_reason ? h('dt', null, 'Skipped') : null, x.state === 'skipped' && x.skip_reason ? h('dd', null, str(x.skip_reason)) : null,
            h('dt', null, 'Links'), h('dd', { class: 'jb-links' },
              jobLink ? h('a', { href: jobLink, target: '_blank', rel: 'noopener noreferrer' }, 'Job page') : null,
              formLink && formLink !== jobLink ? h('a', { href: formLink, target: '_blank', rel: 'noopener noreferrer' }, 'Application form') : null)),
          needs,
          x.jd ? h('details', { class: 'jb-jd' }, h('summary', null, 'Job description'), h('p', null, str(x.jd))) : null),
        h('div', { class: 'jb-actions' }, acts)));
    }
    return row;
  }
  // Why the "To apply" list is empty, from the last fetch's own numbers (so an empty board is never a mystery)
  function jbWhyEmpty(last, hours) {
    const bits = [];
    if (last && last.at) bits.push('Last fetch read ' + num(last.read) + ' jobs: ' + num(last.salesforce) + ' Salesforce roles, ' + num(last.old) + ' of them older than 48 hours, ' + num(last.new) + ' new.');
    if (last && last.li_off) bits.push('LinkedIn is not fetched here (automatic LinkedIn is off to keep your account safe); LinkedIn jobs come from your own LinkedIn runs.');
    else if (last && last.li_paused_until) bits.push('LinkedIn — the biggest source of fresh jobs — asked us to slow down; it is fetched again after ' + fmtTime(last.li_paused_until) + '.');
    else if (last && last.li_deferred) bits.push('LinkedIn is fetched after the current run (they use LinkedIn at the same time).');
    else if (last && !last.li) bits.push('LinkedIn is fetched every ' + num(((S.board.status || {}).settings || {}).li_every_hours, 2) + ' hours; it was not part of this fetch.');
    bits.push(hours === 24 ? 'Try "Last 48h".' : 'New jobs show up here as soon as they are posted.');
    return bits.join(' ');
  }
  function renderBoard() {
    const page = $('#page-board');
    if (!page) return;
    if (!S.board) { page.replaceChildren(h('div', { class: 'jb-wrap' }, skeletonRows(6))); return; }
    const st = S.board.status || {};
    const set = st.settings || {};
    const hours = S.jbHours || 24;
    const all = (S.board.listings || []).filter(jbInWindow);
    const f = S.jbFilter || 'todo';
    const q = (S.jbQ || '').trim().toLowerCase();
    const src = S.jbSrc || 'all';
    const narrowed = all.filter(x => (src === 'all' || (x.sources || [x.source]).includes(src))
      && (!q || (str(x.title) + ' ' + str(x.company) + ' ' + str(x.location)).toLowerCase().includes(q)));
    let rows = narrowed.filter(x => jbMatch(x, f));
    if (f === 'todo') rows = rows.slice().sort((a, b) => (a.elig === 'open' ? 0 : 1) - (b.elig === 'open' ? 0 : 1) || (jbFresh(b) ? 1 : 0) - (jbFresh(a) ? 1 : 0) || str(b.posted_at).localeCompare(str(a.posted_at)));
    const last = st.last || {};
    const sel = S.jbSel || (S.jbSel = new Set());
    for (const id of Array.from(sel)) if (!all.some(x => x.id === id)) sel.delete(id);
    const picked = all.filter(x => sel.has(x.id));
    const toFill = picked.filter(x => ['new', 'needs_you'].includes(x.state));
    const toSubmit = picked.filter(x => x.state === 'ready');

    const refreshing = !!st.refreshing;
    const head = h('div', { class: 'jb-head' },
      h('div', null, h('h1', { id: 'h-board' }, 'Job board'),
        h('p', { class: 'jb-lede' }, 'Remote Salesforce jobs worldwide posted in the last ' + hours + ' hours: company job portals and remote boards. Found by Job Hunter itself (no AI tokens); the AI fills the applications you pick' + (autoSub() ? ' and submits each one by itself once it is fully filled.' : '. Nothing is submitted without your click.'))),
      h('div', { class: 'jb-cta' },
        h('div', { class: 'seg', role: 'group', 'aria-label': 'Posted within' },
          [24, 48].map(n => h('button', { type: 'button', class: 'seg-btn', 'aria-pressed': String(hours === n), onclick: () => { S.jbHours = n; S.jbShown = 0; renderBoard(); renderBoardBadge(); } }, 'Last ' + n + 'h'))),
        h('button', { type: 'button', class: 'btn', disabled: refreshing ? true : null, onclick: () => jbRefresh(true) }, refreshing ? 'Fetching…' : 'Refresh now'),
        h('span', { class: 'faint jb-upd' }, refreshing ? 'Fetching fresh jobs…' : last.at ? 'Updated ' + rel(last.at) + (st.next_in_s != null ? ' · next in ' + Math.max(1, Math.round(num(st.next_in_s) / 60)) + 'm' : '') : 'Not fetched yet')));

    const counts = {};
    JB_FILTERS.forEach(([id]) => { counts[id] = narrowed.filter(x => jbMatch(x, id)).length; });
    const stripCounts = {};
    JB_FILTERS.forEach(([id]) => { stripCounts[id] = all.filter(x => jbMatch(x, id)).length; });
    const openN = all.filter(x => jbMatch(x, 'todo') && x.elig === 'open').length;
    const freshN = all.filter(jbFresh).length;
    const strip = h('div', { class: 'jb-strip' },
      [[openN, 'open to you'], [freshN, 'fresh (<3h, few applicants)'], [stripCounts.ready, 'ready to submit'], [stripCounts.needs, 'need you'], [stripCounts.applied, 'applied']].map(([n, l]) =>
        h('div', { class: 'jb-stat' }, h('b', { class: 'tnum' }, num(n)), h('span', null, l))));

    const search = h('input', { type: 'search', class: 'ag-search', placeholder: 'Search title, company, place', 'aria-label': 'Search jobs', value: S.jbQ || '' });
    search.addEventListener('input', () => { S.jbQ = search.value; S.jbShown = 0; clearTimeout(S.jbQT); S.jbQT = setTimeout(() => { renderBoard(); const el = $('#page-board .ag-search'); if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } }, 200); });
    const srcSet = new Set(); all.forEach(x => (x.sources || [x.source]).forEach(s => srcSet.add(s)));
    const filters = h('div', { class: 'ag-filters' },
      h('div', { class: 'chips', role: 'group', 'aria-label': 'Show' }, JB_FILTERS.map(([id, label]) =>
        h('button', { type: 'button', class: 'chip', 'aria-pressed': String(f === id), onclick: () => { S.jbFilter = id; S.jbShown = 0; renderBoard(); } }, label, h('span', { class: 'n' }, counts[id])))),
      h('div', { class: 'ag-filters-right' },
        h('select', { 'aria-label': 'Source', onchange: e => { S.jbSrc = e.target.value; S.jbShown = 0; renderBoard(); } },
          [['all', 'All sources']].concat(Array.from(srcSet).sort().map(s => [s, JB_SRC[s] || s])).map(([v, l]) => h('option', { value: v, selected: src === v ? true : null }, l))),
        search));

    const bulk = picked.length ? h('div', { class: 'jb-bulk', role: 'region', 'aria-label': 'Selected jobs' },
      h('span', null, h('b', null, picked.length), ' selected'),
      toFill.length ? h('button', { type: 'button', class: 'btn btn-sm btn-primary', onclick: () => jbApply(toFill.map(x => x.id)) }, 'Fill ' + plural(toFill.length, 'application')) : null,
      toSubmit.length ? armButton('btn btn-sm btn-primary', 'Submit ' + toSubmit.length + ' ready', 'Submit ' + plural(toSubmit.length, 'application') + '? Click again', () => jbSubmit(toSubmit)) : null,
      h('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => { Promise.all(picked.map(x => api('/api/jobboard/' + x.id, { method: 'POST', body: { status: 'hidden' }, quiet: true }).catch(() => null))).then(() => { S.jbSel = new Set(); loadBoard(true); }); } }, 'Hide'),
      h('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => { S.jbSel = new Set(); renderBoard(); } }, 'Clear')) : null;

    const lim = S.jbShown || 60;
    let list;
    if (!all.length) {
      list = h('div', { class: 'empty' }, h('div', { class: 'empty-title' }, refreshing ? 'Fetching jobs…' : 'No jobs fetched yet'),
        h('div', { class: 'empty-text' }, refreshing ? 'Company portals and remote boards first, then LinkedIn. Usually 1–3 minutes.' : 'Click "Refresh now". After that the board refreshes itself every ' + num(set.fast_minutes, 30) + ' minutes while the app is open.'));
    } else if (!rows.length) {
      list = h('div', { class: 'empty' }, h('div', { class: 'empty-title' }, 'Nothing here'), h('div', { class: 'empty-text' }, q ? 'No job matches "' + S.jbQ.trim() + '" here. Try another filter or clear the search.'
        : f === 'todo' ? jbWhyEmpty(last, hours) : 'Try another filter or source.'));
    } else {
      const visible = rows.slice(0, lim);
      const pickable = visible.filter(x => ['new', 'needs_you', 'ready'].includes(x.state));
      const allOn = pickable.length && pickable.every(x => sel.has(x.id));
      const someOn = pickable.some(x => sel.has(x.id));
      list = h('div', null,
        h('div', { class: 'jb-cols' },
          h('span', null, h('input', { type: 'checkbox', 'aria-label': 'Select all shown', checked: allOn ? true : null, indeterminate: !allOn && someOn, disabled: pickable.length ? null : true,
            onchange: e => { pickable.forEach(x => e.target.checked ? sel.add(x.id) : sel.delete(x.id)); renderBoard(); } })),
          h('span', { 'aria-hidden': 'true' }, 'Job'), h('span', { 'aria-hidden': 'true' }, 'Posted'), h('span', { 'aria-hidden': 'true' }, 'How to apply'), h('span', { 'aria-hidden': 'true' }, 'Status')),
        h('ul', { class: 'jb-list' }, visible.map(jbItem)),
        rows.length > lim ? h('div', { class: 'more' }, h('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: () => { S.jbShown = lim + 60; renderBoard(); } }, 'Show ' + Math.min(60, rows.length - lim) + ' more of ' + (rows.length - lim))) : null);
    }
    const main = h('section', { class: 'panel ag-listpanel', 'aria-label': 'Jobs' }, filters, bulk, list);

    // side: auto-fill, sources scoreboard, health, how it works
    const autoOn = set.auto_fill !== false;
    const perIn = h('input', { type: 'number', min: '1', max: '15', value: String(num(set.per_run, 5)), class: 'ag-num', id: 'jb-per', onchange: e => jbSaveSettings({ per_run: Math.max(1, Math.min(15, Math.round(num(e.target.value, 5)))) }) });
    const capIn = h('input', { type: 'number', min: '0', max: '60', value: String(num(set.daily_cap, 20)), class: 'ag-num', id: 'jb-cap', onchange: e => jbSaveSettings({ daily_cap: Math.max(0, Math.min(60, Math.round(num(e.target.value, 20)))) }) });
    const usdIn = h('input', { type: 'number', min: '0', max: '100', value: String(num(set.daily_usd, 6)), class: 'ag-num', id: 'jb-usd', onchange: e => jbSaveSettings({ daily_usd: Math.max(0, Math.min(100, Math.round(num(e.target.value, 6)))) }) });
    const auto = h('section', { class: 'panel ag-side-card' }, h('h2', null, 'Auto-fill'),
      h('label', { class: 'jb-switch' }, h('input', { type: 'checkbox', checked: autoOn ? true : null, onchange: e => jbSaveSettings({ auto_fill: e.target.checked }) }),
        h('span', null, 'Fill the best new jobs by itself after each refresh')),
      h('label', { class: 'ag-field', for: 'jb-per' }, h('span', null, 'Jobs per round'), perIn),
      h('label', { class: 'ag-field', for: 'jb-cap' }, h('span', null, 'Jobs per day'), capIn),
      h('label', { class: 'ag-field', for: 'jb-usd' }, h('span', null, 'AI budget per day (USD)'), usdIn),
      h('p', { class: 'faint ag-small' }, 'Today: ' + num(st.auto_today) + ' of ' + num(set.daily_cap, 20) + ' picked · $' + num(st.spent_today).toFixed(2) + ' of $' + num(set.daily_usd, 6) + ' used. Picks only job-portal forms that need no account, Easy Apply and email, open to India first, fewest applicants first. Forms stop before Submit.'));
    const srcStats = S.board.sources || {};
    const srcRows = Object.entries(srcStats).sort((a, b) => num(b[1].listed) - num(a[1].listed));
    const score = h('section', { class: 'panel ag-side-card' }, h('h2', null, 'Which source works (14 days)'),
      srcRows.length ? h('table', { class: 'jb-score' }, h('thead', null, h('tr', null, h('th', null, 'Source'), h('th', null, 'Jobs'), h('th', null, 'Applied'), h('th', null, 'Replies'))),
        h('tbody', null, srcRows.map(([s, v]) => h('tr', null, h('td', null, JB_SRC[s] || s), h('td', { class: 'tnum' }, num(v.listed)), h('td', { class: 'tnum' }, num(v.applied)), h('td', { class: 'tnum' }, num(v.replies))))))
        : h('p', { class: 'faint ag-small' }, 'Fills in as you apply. Use it to see which sources bring replies.'));
    const health = last.health || {};
    const hRows = Object.entries(health);
    const healthCard = hRows.length ? h('section', { class: 'panel ag-side-card' }, h('h2', null, 'Last fetch'),
      h('ul', { class: 'jb-health' }, hRows.map(([k, v]) => h('li', null, h('span', { class: 'ag-dot ag-dot-' + (v.ok ? 'sent' : 'failed'), 'aria-label': v.ok ? 'ok' : 'problem' }),
        h('span', null, JB_SRC[k] || k), h('span', { class: 'faint' }, v.ok ? num(v.cards) + ' jobs read' + (v.boards != null ? ' · ' + num(v.boards) + ' company pages' : '') : str(v.error) || 'failed')))),
      st.linkedin_paused_s ? h('p', { class: 'faint ag-small' }, 'LinkedIn asked us to slow down: paused for ' + Math.ceil(num(st.linkedin_paused_s) / 60) + ' more minutes.') : null) : null;
    const how = h('section', { class: 'panel ag-side-card' }, h('h2', null, 'How applying works'),
      h('ol', { class: 'ag-steps' },
        h('li', null, 'Pick jobs and click Fill (or let auto-fill pick). The AI opens each application in your Chrome, fills it with your answers and tailored resume, and stops before Submit.'),
        h('li', null, 'Portals that need your account (Workday, iCIMS…) are filled only if you are already signed in there. Otherwise they land in Needs you.'),
        h('li', null, 'Check Ready, select, and click Submit. Only then is anything sent.')));
    const side = h('aside', { class: 'ag-side' }, auto, score, healthCard, how);
    page.replaceChildren(h('div', { class: 'jb-wrap' }, head, strip, h('div', { class: 'ag-grid' }, main, side)));
  }

  function renderQuestions(body) {
    if (S.questions === null || S.questions === undefined) { body.replaceChildren(skeletonRows(3)); loadQuestions(true); return; }
    const f = S.qFilter || 'open';
    const c = S.qCounts || {};
    const bar = h('div', { class: 'q-bar' },
      h('div', { class: 'chips', role: 'group', 'aria-label': 'Show' }, Q_FILTERS.map(([id, label]) =>
        h('button', { type: 'button', class: 'chip', 'aria-pressed': String(f === id), onclick: () => { S.qFilter = id; renderAppsBody(true); } },
          label, h('span', { class: 'n' }, num(c[id]) || 0)))),
      h('div', { class: 'q-tools' },
        h('a', { class: 'btn btn-sm btn-ghost', href: '/api/questions/export.csv?kind=bank', download: 'answers_bank.csv', title: 'All questions and your answers, as a CSV that opens in Excel' }, 'Answers for Excel'),
        h('a', { class: 'btn btn-sm btn-ghost', href: '/api/questions/export.csv?kind=log', download: 'form_answers_log.csv', title: 'Every answer filled on every form (company, role, question, answer)' }, 'Filled-answers log'),
        h('button', { type: 'button', class: 'btn btn-sm', onclick: () => { S.qAdding = !S.qAdding; S.qAddFocus = S.qAdding; renderAppsBody(true); } }, S.qAdding ? 'Cancel' : '+ Add your own')));
    const note = h('div', { class: 'portal-note' },
      h('span', { svg: ICON.hand, style: { display: 'contents' } }),
      h('div', null, h('b', null, 'Answer once, reused everywhere. '),
        'When Easy Apply or a portal form asks something Job Hunter doesn\'t know, the question lands here and that field is left blank — the run keeps going. Your answer is saved, and the next form with the same (or a similarly worded) question is filled automatically. Answers Job Hunter fills itself are logged too (both lists are also kept as CSV files in the data folder).'));
    const kids = [note, bar];
    if (S.qAdding) {
      S.qDraft = S.qDraft || { q: '', a: '' };
      const qi = h('input', { class: 'q-input', placeholder: 'Question, e.g. "Desired hourly rate (USD)"', 'aria-label': 'New question' });
      const ai = h('input', { class: 'q-input', placeholder: 'Your answer', 'aria-label': 'Answer' });
      qi.value = S.qDraft.q; ai.value = S.qDraft.a;
      qi.addEventListener('input', () => { S.qDraft.q = qi.value; });
      ai.addEventListener('input', () => { S.qDraft.a = ai.value; });
      const go = () => { if (!qi.value.trim() || !ai.value.trim()) return toast('Fill both the question and the answer.', 'info', 'qadd'); qAdd(qi.value.trim(), ai.value.trim()); };
      [qi, ai].forEach(x => x.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); go(); } }));
      kids.push(h('div', { class: 'q-add' }, qi, ai, h('button', { type: 'button', class: 'btn btn-sm btn-primary', onclick: go }, 'Save')));
      if (S.qAddFocus) { S.qAddFocus = false; setTimeout(() => qi.focus(), 0); }
    }
    const rows = S.questions.filter(q => q.status === f && (!S.q || (str(q.question) + ' ' + str(q.answer)).toLowerCase().indexOf(S.q.toLowerCase()) >= 0));
    if (!rows.length && S.q) {
      kids.push(emptyState('Nothing matches', 'No question here matches "' + S.q + '". Clear the search to see them all.',
        h('button', { class: 'btn btn-sm', type: 'button', onclick: clearFilters }, 'Clear search')));
    } else if (!rows.length) {
      const empty = { open: ['No questions waiting', 'New questions from Easy Apply and portal forms show up here during a run.'],
        answered: ['No saved answers yet', 'Answer a question (or add your own) and it is reused on every form.'],
        mine: ['Nothing here', 'Questions you mark "Always ask me" are never auto-filled.'] }[f];
      kids.push(emptyState(empty[0], empty[1]));
    } else {
      kids.push(h('ul', { class: 'q-list' }, rows.map(qItem)));
    }
    body.replaceChildren(...kids);
  }

  function submitSelectedButton() {
    const n = S.pSel ? S.pSel.size : 0;
    let armed = false, t = 0;
    const b = h('button', { type: 'button', class: 'btn btn-sm btn-primary', disabled: !n }, n ? 'Submit selected (' + n + ')' : 'Submit selected');
    b.addEventListener('click', async () => {
      if (!armed) {
        armed = true; b.textContent = 'Submit ' + plural(n, 'application') + '? Click again';
        t = setTimeout(() => { armed = false; b.textContent = 'Submit selected (' + n + ')'; }, 4000);
        return;
      }
      clearTimeout(t); b.disabled = true; b.textContent = 'Starting…';
      try {
        const d = await api('/api/portal/submit', { method: 'POST', body: { ids: [...S.pSel], seen: Object.fromEntries([...S.pSel].map(id => [id, (S.pSeen || {})[id] || ''])) }, label: 'Submitting applications', quiet: true });
        const ok = (d.approved || []).length, bad = (d.rejected || []).length;
        toast(ok ? (plural(ok, 'application') + (d.queued ? ' approved — submitting as soon as the current run ends.' : ' approved — submitting now.')) + (bad ? ' ' + bad + ' could not be approved.' : '')
          : 'Nothing could be approved: ' + ((d.rejected || [])[0] || {}).reason, ok ? 'ok' : 'info');
        S.pSel = new Set();
        loadPortal(true); loadSummary(true);
      } catch (e) { apiErrorToast('Submitting applications', e); b.disabled = false; }
    });
    return b;
  }

  function renderPortal(body) {
    if (S.portal === null) { body.replaceChildren(skeletonRows(3)); return; }
    const note = h('div', { class: 'portal-note' },
      h('span', { svg: ICON.hand, style: { display: 'contents' } }),
      h('div', null,
        autoSub() ? h('b', null, 'Submitted by itself. ') : h('b', null, 'Nothing is submitted without your click. '),
        autoSub() ? 'Job Hunter fills LinkedIn Easy Apply and job-portal forms with your profile, saved answers and tailored resume, and submits every form that is fully filled, right after the run. Forms with blank required questions stay here until you answer them in Questions for you; forms on a company’s own website you submit there.'
          : 'Job Hunter fills LinkedIn Easy Apply, Greenhouse, Lever and Ashby forms with your profile, saved answers and tailored resume, then stops at Review. Tick the ones marked Ready and click Submit selected — it then submits exactly those. Forms with blank required questions stay here until you answer them in Questions for you.'));
    const rows = S.portal.slice().sort((a, b) => (portalOpen(b) - portalOpen(a)) || ((parseTs(b.created_at) || 0) - (parseTs(a.created_at) || 0)));
    if (!rows.length) {
      body.replaceChildren(note, emptyState('Nothing waiting for you', 'When a qualified job has a Greenhouse, Lever or Ashby form, the engine prefills it during the Portal lane and it appears here for you to review.'));
      return;
    }
    const open = rows.filter(portalOpen), done = rows.filter(p => !portalOpen(p));
    const isReady = p => portalOpen(p) && num(p.ready) === 1 && !p.approved_at;
    const readyRows = open.filter(isReady);
    S.pSeen = Object.fromEntries(readyRows.map(p => [p.id, p.updated_at || '']));
    S.pSel = new Set([...(S.pSel || [])].filter(id => readyRows.some(p => p.id === id)));
    const allOn = readyRows.length && readyRows.every(p => S.pSel.has(p.id));
    const submitBar = readyRows.length ? h('div', { class: 'pq-bar' },
      h('label', { class: 'check-inline' }, h('input', { type: 'checkbox', checked: !!allOn,
        indeterminate: !allOn && S.pSel.size > 0 ? true : null,
        onchange: e => { S.pSel = new Set(e.target.checked ? readyRows.map(p => p.id) : []); renderAppsBody(true); } }),
        'Select all ready (' + readyRows.length + ')'),
      h('button', { type: 'button', class: 'btn btn-sm', title: 'Select every ready form, then click Submit to confirm',
        onclick: () => { S.pSel = new Set(readyRows.map(p => p.id)); renderAppsBody(true); toast(plural(readyRows.length, 'ready form') + ' selected. Click Submit to send them.', 'info', 'selall'); } },
        'Approve all ready (' + readyRows.length + ')'),
      submitSelectedButton()) : null;
    const item = p => {
      const company = str(p.company || get(p, 'job.company')) || '—';
      const role = str(p.role || get(p, 'job.role'));
      const qs = portalQuestions(p);
      const st = str(p.status || 'prefilled');
      const isOpen = portalOpen(p);
      const url = str(p.apply_url || get(p, 'job.apply_url') || get(p, 'job.url'));
      const resume = str(p.resume_path);
      const ready = isReady(p);
      const sel = ready ? h('input', { type: 'checkbox', class: 'pq-sel', checked: S.pSel.has(p.id), 'aria-label': 'Select ' + company + ' for submitting',
        onchange: e => { if (e.target.checked) S.pSel.add(p.id); else S.pSel.delete(p.id); renderAppsBody(true); } }) : null;
      const actions = isOpen ? [
        safeHref(url) ? h('a', { class: 'btn btn-sm', href: safeHref(url), target: '_blank', rel: 'noopener noreferrer', title: 'Opens the application page in a new tab' }, h('span', { svg: ICON.ext, style: { display: 'contents' } }), 'Open form') : null,
        h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: e => portalMark(p, 'submitted', e.currentTarget) }, 'Mark submitted'),
        confirmButton('Abandon', 'Confirm abandon', e => portalMark(p, 'abandoned', e)),
      ] : [statusChip(st), h('span', { class: 'muted', style: { fontSize: '12px' } }, relEl(p.updated_at || p.created_at))];
      const shownQs = qs.slice(0, 5);
      return h('li', { class: 'pq-item' + (isOpen ? '' : ' is-done') },
        h('div', { class: 'pq-head' },
          h('span', { class: 'pq-co', title: company }, sel, company),
          h('span', { class: 'pq-role', title: role }, role || '—'),
          h('div', { class: 'pq-meta' },
            p.ats ? h('span', { class: 'ats' }, ({ greenhouse: 'Greenhouse', lever: 'Lever', ashby: 'Ashby', linkedin_easy_apply: 'Easy Apply' })[str(p.ats).toLowerCase()] || str(p.ats)) : null,
            p.fit_score != null ? fitCell(p.fit_score) : null,
            isOpen ? (p.approved_at ? h('span', { class: 'st st-approved' }, 'Submitting…')
              : ready ? h('span', { class: 'st st-ready' }, 'Ready') : h('span', { class: 'st st-needs_input' }, 'Needs answers')) : null,
            isOpen && p.submit_note ? h('span', { class: 'pq-note', title: str(p.submit_note) }, str(p.submit_note)) : null,
            h('span', null, 'Prefilled ', relEl(p.created_at)))),
        !isOpen ? h('div', { class: 'pq-none' }, resume ? 'Resume: ' + resume.split(/[\\/]/).pop() : '') : h('div', { style: { minWidth: 0 } },
          h('div', { class: 'pq-q-title' }, qs.length ? 'Left for you to answer (' + qs.length + ')' : 'Questions'),
          qs.length ? h('ul', { class: 'pq-qs' }, shownQs.map(q => h('li', { class: 'q', title: q }, q)),
            qs.length > shownQs.length ? h('li', { class: 'q q-more', title: qs.slice(5).join('\n') }, '+' + (qs.length - 5) + ' more') : null)
            : h('div', { class: 'pq-none' }, 'None left — just review and submit.'),
          resume && isOpen ? h('div', { class: 'pq-none', style: { marginTop: '6px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: resume }, 'Resume: ', h('code', null, resume.split(/[\\/]/).pop())) : null,
          isOpen && Array.isArray(p.answers) && p.answers.length ? h('details', { class: 'pq-ans' },
            h('summary', null, 'Filled answers (' + p.answers.length + ')'),
            h('dl', null, p.answers.slice(0, 40).map(a => [h('dt', null, str(a.question)), h('dd', { title: 'from: ' + str(a.source) }, str(a.answer))]))) : null),
        h('div', { class: 'pq-actions' }, actions));
    };
    const parts = [note, submitBar, h('ul', { class: 'pq' }, open.map(item))].filter(Boolean);
    if (!open.length) parts.splice(1, 0, emptyState('All caught up', 'Every prefilled form has been submitted or abandoned.'));
    if (done.length) parts.push(h('div', { class: 'pq-group' }, 'Done'), h('ul', { class: 'pq' }, done.slice(0, 20).map(item)));
    body.replaceChildren(...parts);
  }

  function confirmButton(label, confirmLabel, fn) {
    let armed = false, t = 0;
    const b = h('button', { class: 'btn btn-sm btn-ghost', type: 'button' }, label);
    b.addEventListener('click', () => {
      if (!armed) { armed = true; b.textContent = confirmLabel; b.classList.add('btn-danger'); b.classList.remove('btn-ghost'); t = setTimeout(() => { armed = false; b.textContent = label; b.classList.remove('btn-danger'); b.classList.add('btn-ghost'); }, 3000); return; }
      clearTimeout(t); fn(b);
    });
    return b;
  }
  async function portalMark(p, status, btn) {
    if (btn) btn.disabled = true;
    try {
      await api('/api/portal/' + encodeURIComponent(p.id), { method: 'POST', body: { status: status }, label: 'Updating the form status' });
      p.status = status; p.updated_at = new Date().toISOString();
      toast(status === 'submitted' ? 'Marked ' + str(p.company || get(p, 'job.company')) + ' as submitted.' : 'Abandoned ' + str(p.company || get(p, 'job.company')) + '.', status === 'submitted' ? 'ok' : 'info');
      renderTabs(); renderAppsBody();
      queueRefresh('portal');
    } catch (e) { if (btn) btn.disabled = false; }
  }

  function clearFilters() {
    S.q = ''; S.source = ''; $('#q').value = ''; $('#source').value = '';
    S.shown = PAGE; renderTabs(); renderAppsBody();
  }

  /* ═════════════════════════ Drawer ═════════════════════════ */

  let drawerReturn = null, drawerToken = 0;
  function openDrawer(title, sub, tags) {
    drawerReturn = document.activeElement;
    $('#drawer-title').textContent = title || '—';
    const subEl = $('#drawer-sub');
    subEl.replaceChildren(sub || '', tags ? h('div', { class: 'drawer-tags' }, tags) : null);
    const d = $('#drawer');
    $('#scrim').hidden = false;
    d.classList.add('open');
    d.setAttribute('aria-hidden', 'false');
    setTimeout(() => d.focus(), 30);
  }
  function closeDrawer() {
    const d = $('#drawer');
    if (!d.classList.contains('open')) return false;
    d.classList.remove('open');
    d.setAttribute('aria-hidden', 'true');
    $('#scrim').hidden = true;
    S.activeRow = null;
    $$('.tbl tr.is-active').forEach(tr => tr.classList.remove('is-active'));
    if (drawerReturn && drawerReturn.focus) try { drawerReturn.focus(); } catch (e) { /* ignore */ }
    return true;
  }
  function dsec(title, kids, aside) { return h('section', { class: 'd-sec' }, h('h3', null, title, aside ? h('span', { class: 'muted' }, aside) : null), kids); }
  function kv(pairs) {
    return h('dl', { class: 'kv' }, pairs.filter(p => p[1] != null && p[1] !== '').map(p => [h('dt', null, p[0]), h('dd', null, p[1])]));
  }
  const safeHref = u => (/^https?:\/\//i.test(String(u || '')) ? String(u) : null);
  function linkOut(href, label) {
    href = safeHref(href);
    if (!href) return null;
    return h('a', { class: 'btn btn-sm', href: href, target: '_blank', rel: 'noopener noreferrer' }, h('span', { svg: ICON.ext, style: { display: 'contents' } }), label);
  }
  function jdBlock(text) {
    if (!text) return h('p', { class: 'muted' }, 'No job description was saved for this job.');
    const el = h('div', { class: 'jd' }, text);
    const wrap = h('div', null, el);
    requestAnimationFrame(() => {
      if (el.scrollHeight > el.clientHeight + 8) {
        el.classList.add('clamped');
        const b = h('button', { class: 'btn btn-sm btn-ghost', type: 'button', style: { marginTop: '6px' } }, 'Show full description');
        b.onclick = () => { const ex = el.classList.toggle('expanded'); el.classList.toggle('clamped', !ex); b.textContent = ex ? 'Show less' : 'Show full description'; };
        wrap.append(b);
      }
    });
    return wrap;
  }

  async function openApp(r) {
    const key = appKey(r);
    S.activeRow = key;
    $$('.tbl tr').forEach(tr => tr.classList.toggle('is-active', tr.dataset.key === key));
    if (r.contact_id != null) markRepliesSeen(x => x.contact_id === r.contact_id);
    openDrawer(str(r.company) || 'Application', str(r.role), [srcBadge(r.source), statusChip(r.app_status), r.fit_score != null ? h('span', { class: 'badge' }, 'Fit ' + Math.round(num(r.fit_score))) : null]);
    const body = $('#drawer-body');
    body.replaceChildren(h('div', { class: 'd-skel' }, [80, 60, 90, 40, 70, 85, 50].map(w => h('span', { class: 'skel', style: { width: w + '%', height: '12px' } }))));
    const token = ++drawerToken;
    let d = null;
    if (r.contact_id != null) d = await tryApi('/api/applications/' + encodeURIComponent(r.contact_id), { label: 'Application details' });
    if (token !== drawerToken) return;
    renderAppDetail(r, d || {});
  }

  function renderAppDetail(row, d) {
    const job = d.job || {};
    const c = d.contact || {};
    const emails = asList(d.emails, []).slice().sort((a, b) => (parseTs(a.sent_at) || 0) - (parseTs(b.sent_at) || 0));
    const replies = asList(d.replies, []);
    const body = $('#drawer-body');
    const url = job.url || row.url;
    const applyUrl = job.apply_url;

    // timeline: prefer the server's, else derive from emails + replies
    let tl = asList(d.timeline, []).map(t => ({ ts: t.ts || t.at || t.date, kind: str(t.status === 'bounced' ? 'bounced' : (t.kind || t.type || t.status)), label: str(t.label || t.message || t.text || t.kind || t.type), next: t.status === 'due' || t.next === true }));
    if (!tl.length) {
      const KL = { initial: 'Application sent', nudge1: 'Nudge 1 sent', nudge2: 'Nudge 2 sent' };
      emails.forEach(e => tl.push({ ts: e.sent_at, kind: e.status === 'bounced' ? 'bounced' : (e.kind || 'sent'), label: (KL[e.kind] || 'Email sent') + (e.status === 'bounced' ? ' — bounced' : (e.status === 'dry_run' ? ' (dry run, not sent)' : '')) }));
      replies.forEach(rp => tl.push({ ts: rp.received_at, kind: rp.kind === 'bounce' ? 'bounce' : 'reply', label: rp.kind === 'bounce' ? 'Bounce received' : (rp.kind === 'auto' ? 'Auto-reply from ' + str(rp.from_addr) : 'Reply from ' + (str(c.name || row.contact_name) || str(rp.from_addr))) }));
      tl.sort((a, b) => (parseTs(a.ts) || 0) - (parseTs(b.ts) || 0));
      const st = str(row.app_status);
      if (row.next_followup_at && AWAITING.indexOf(st) >= 0) tl.push({ ts: row.next_followup_at, kind: 'next', label: st === 'sent' ? 'Nudge 1 scheduled' : 'Nudge 2 scheduled', next: true });
    }

    const sections = [];
    sections.push(dsec('Contact', [
      kv([
        ['Name', str(c.name || row.contact_name) || '—'],
        ['Title', str(c.title || row.contact_title)],
        ['Email', (c.email || row.email) ? h('a', { href: 'mailto:' + str(c.email || row.email) }, str(c.email || row.email)) : '—'],
        ['Email found via', str(c.email_source || row.email_source)],
        ['LinkedIn', safeHref(c.linkedin) ? h('a', { href: safeHref(c.linkedin), target: '_blank', rel: 'noopener noreferrer' }, str(c.linkedin).replace(/^https?:\/\/(www\.)?/, '')) : ''],
      ]),
    ]));
    sections.push(dsec('Timeline', tl.length ? h('ol', { class: 'tl' }, tl.map(t => h('li', { class: 'tl-' + (t.next ? 'next' : t.kind) },
      h('span', { class: 'tl-dot' }), h('span', { class: 'tl-label' }, t.label), h('span', { class: 'tl-when' }, t.next ? relEl(t.ts, 'next') : (parseTs(t.ts) ? h('time', { title: exact(t.ts) }, dayLabel(parseTs(t.ts)) + ' ' + clock(parseTs(t.ts))) : '—')))))
      : h('p', { class: 'muted' }, 'No activity recorded yet.')));

    const KIND = { initial: 'Application', nudge1: 'Nudge 1', nudge2: 'Nudge 2' };
    sections.push(dsec('Emails sent', emails.length ? emails.map((e, i) => h('details', { class: 'mail', open: i === 0 ? true : null },
      h('summary', null,
        h('span', { class: 'mail-subj' }, str(e.subject) || '(no subject)'),
        h('span', { class: 'mail-when', title: exact(e.sent_at) }, rel(e.sent_at)),
        h('span', { class: 'mail-meta' }, KIND[e.kind] || str(e.kind) || 'Email', e.status && e.status !== 'sent' ? statusChip(e.status) : null, e.resume_path ? h('span', null, 'Akshay_Verma_Resume.pdf attached') : null, e.error ? h('span', { style: { color: 'var(--bad)' } }, str(e.error)) : null)),
      h('div', { class: 'mail-body' }, str(e.body) || '(body not stored)'))) : h('p', { class: 'muted' }, row.contact_id == null ? 'Details are unavailable for this row.' : 'No emails stored for this contact.'), emails.length ? plural(emails.length, 'email') : null));

    if (replies.length || str(row.app_status) === 'replied') {
      sections.push(dsec('Replies', replies.length ? replies.map(rp => h('div', { class: 'mail ' + (rp.kind === 'auto' ? 'mail-auto' : (rp.kind === 'bounce' ? '' : 'mail-reply')) },
        h('div', { style: { padding: '10px 12px', display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: '2px 12px' } },
          h('span', { class: 'mail-subj' }, str(rp.subject) || '(no subject)'),
          h('span', { class: 'mail-when', title: exact(rp.received_at) }, rel(rp.received_at)),
          h('span', { class: 'mail-meta' }, str(rp.from_addr), rp.kind === 'auto' ? h('span', { class: 'st st-skipped' }, 'Auto-reply') : null, rp.kind === 'bounce' ? statusChip('bounced') : null)),
        rp.snippet ? h('div', { class: 'mail-body' }, str(rp.snippet)) : null)) : h('p', { class: 'muted' }, 'A reply was detected but its text was not stored. Open Gmail to read it.')));
    }

    sections.push(dsec('Job', [
      kv([
        ['Location', [str(job.location), (job.remote === 1 || job.remote === true) && !/remote/i.test(str(job.location)) ? 'Remote' : ''].filter(Boolean).join(' · ')],
        ['Posted', str(job.posted)],
        ['Found', job.created_at ? h('time', { title: exact(job.created_at) }, rel(job.created_at)) : ''],
        ['Fit score', job.fit_score != null ? Math.round(num(job.fit_score)) + ' / 100' : (row.fit_score != null ? Math.round(num(row.fit_score)) + ' / 100' : '')],
        ['Notes', str(job.notes)],
      ]),
      (url || applyUrl) ? h('div', { class: 'd-links' }, linkOut(url, 'Job posting'), applyUrl && applyUrl !== url ? linkOut(applyUrl, 'Apply page') : null) : null,
    ]));
    sections.push(dsec('Job description', jdBlock(str(job.jd_text))));
    body.replaceChildren(...sections);
  }

  function openSkipped(r) {
    openDrawer(str(r.company) || 'Skipped job', str(r.role), [srcBadge(r.source), statusChip('skipped'), r.fit_score != null ? h('span', { class: 'badge' }, 'Fit ' + Math.round(num(r.fit_score))) : null]);
    const body = $('#drawer-body');
    body.replaceChildren(
      dsec('Why it was skipped', h('p', null, str(r.skip_reason) || 'No reason recorded.')),
      dsec('Job', [kv([['Location', str(r.location)], ['Posted', str(r.posted)], ['Found', r.created_at ? h('time', { title: exact(r.created_at) }, rel(r.created_at)) : ''], ['Notes', str(r.notes)]]),
        r.url ? h('div', { class: 'd-links' }, linkOut(r.url, 'Job posting')) : null]),
      dsec('Job description', jdBlock(str(r.jd_text))));
  }

  /* ═════════════════════════ Insights ═════════════════════════ */

  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function renderChart() {
    const box = $('#chart'), legend = $('#chart-legend');
    if (!box) return;
    const daily0 = asList(S.charts && S.charts.daily, []);
    const series = daily0.some(d => num(d.import) > 0) ? CHART_SERIES : CHART_SOURCES;
    legend.replaceChildren(...series.slice().reverse().map(k => h('span', { class: k === 'import' ? 'lg-hist' : null }, h('i', { style: { '--src-c': 'var(--src-' + k + ')' } }), LEGEND[k] || SOURCES[k])));
    if (S.charts === null) { box.replaceChildren(h('div', { style: { paddingTop: '16px', display: 'flex', flexDirection: 'column', gap: '18px' } }, [90, 70, 80, 60, 75].map(w => h('span', { class: 'skel', style: { width: w + '%' } })))); return; }
    const daily = asList(S.charts.daily, []);
    const W = box.clientWidth - 32, H = box.clientHeight - 12;
    if (W <= 40 || H <= 40) return;
    const totals = daily.map(d => series.reduce((a, k) => a + num(d[k]), 0));
    if (!daily.length || totals.every(t => t === 0)) {
      box.replaceChildren(emptyState('No applications in the last 14 days', 'Each day’s sends will stack here by source once a run sends its first email.'));
      return;
    }
    const max = Math.max.apply(null, totals);
    const stepRaw = max / 4;
    const mag = Math.pow(10, Math.floor(Math.log10(Math.max(1, stepRaw))));
    const step = [1, 2, 5, 10].map(m => m * mag).find(v => v >= stepRaw) || mag * 10;
    const top = Math.max(step, Math.ceil(max / step) * step);
    const padL = 28, padB = 22, padT = 6;
    const cw = W - padL, ch = H - padB - padT;
    const band = cw / daily.length;
    const bw = Math.max(6, Math.min(28, band * 0.56));
    const y = v => padT + ch - (v / top) * ch;

    const svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Applications per day by source, last 14 days' });
    const grid = s('g', { class: 'grid' }), axis = s('g', { class: 'axis' });
    for (let v = 0; v <= top + 0.001; v += step) {
      if (v > 0) grid.append(s('line', { x1: padL, x2: W, y1: y(v), y2: y(v) }));
      const t = s('text', { x: padL - 8, y: y(v) + 4, 'text-anchor': 'end' }); t.textContent = String(Math.round(v)); axis.append(t);
    }
    svg.append(grid);
    svg.append(s('line', { class: 'baseline', x1: padL, x2: W, y1: y(0) + 0.5, y2: y(0) + 0.5 }));
    const labelEvery = band < 34 ? 2 : 1;
    daily.forEach((d, i) => {
      const cx = padL + band * i + band / 2;
      const g = s('g', { class: 'col' });
      g.append(s('rect', { class: 'hit', x: padL + band * i, y: padT, width: band, height: ch }));
      let acc = 0;
      const segs = series.map(k => ({ k: k, v: num(d[k]) })).filter(x => x.v > 0);
      segs.forEach((sg, si) => {
        const y0 = y(acc), y1 = y(acc + sg.v);
        acc += sg.v;
        const isTop = si === segs.length - 1;
        const gap = si > 0 ? 2 : 0; // 2px surface gap between stacked segments
        const hgt = Math.max(1, y0 - y1 - gap);
        const x = cx - bw / 2, yy = y1;
        const r = isTop ? Math.min(4, hgt, bw / 2) : 0;
        const path = 'M' + x + ' ' + (yy + hgt) + ' V' + (yy + r) + (r ? ' Q' + x + ' ' + yy + ' ' + (x + r) + ' ' + yy : '') + ' H' + (x + bw - r) + (r ? ' Q' + (x + bw) + ' ' + yy + ' ' + (x + bw) + ' ' + (yy + r) : '') + ' V' + (yy + hgt) + ' Z';
        g.append(s('path', { d: path, fill: 'var(--src-' + sg.k + ')' }));
      });
      const dt = parseTs(d.date + 'T12:00:00') || parseTs(d.date);
      if (i % labelEvery === (daily.length - 1) % labelEvery) {
        const t = s('text', { x: cx, y: H - 4, 'text-anchor': 'middle' });
        t.textContent = dt ? (i === daily.length - 1 ? 'Today' : (dt.getDate() === 1 || i < labelEvery ? dt.getDate() + ' ' + MON[dt.getMonth()] : String(dt.getDate()))) : str(d.date);
        axis.append(t);
      }
      g.addEventListener('mousemove', e => {
        const tt = h('div', null,
          h('div', { class: 'tt-title' }, dt ? DOW[dt.getDay()] + ', ' + dt.getDate() + ' ' + MON[dt.getMonth()] : str(d.date)),
          series.slice().reverse().map(k => h('div', { class: 'tt-row' }, h('i', { style: { '--src-c': 'var(--src-' + k + ')' } }), h('span', null, k === 'import' ? 'History (imported)' : SOURCES[k]), h('b', null, num(d[k])))),
          h('div', { class: 'tt-total' }, h('span', null, 'Total'), h('b', null, totals[i])));
        showTip(tt, e.clientX, e.clientY);
      });
      g.addEventListener('mouseleave', hideTip);
      svg.append(g);
    });
    svg.append(axis);
    box.replaceChildren(svg);
  }

  function renderFunnel() {
    const box = $('#funnel');
    if (S.charts === null) { box.replaceChildren(...[1, 2, 3, 4].map(() => h('span', { class: 'skel', style: { height: '28px' } }))); return; }
    const f = S.charts.funnel || {};
    const steps = [['found', 'Jobs found'], ['qualified', 'Qualified'], ['contacted', 'Contacted'], ['replied', 'Replied']];
    const max = Math.max(1, num(f.found), num(f.qualified), num(f.contacted), num(f.replied));
    if (!steps.some(st => num(f[st[0]]) > 0)) { box.replaceChildren(emptyState('No data yet', 'Found, qualified, contacted and replied counts appear after the first run.')); return; }
    box.replaceChildren(...steps.map((st, i) => {
      const v = num(f[st[0]]);
      const prev = i ? num(f[steps[i - 1][0]]) : null;
      const pct = prev ? Math.round((v / prev) * 100) : null;
      return h('div', { class: 'fn-row', title: pct != null ? pct + '% of ' + steps[i - 1][1].toLowerCase() : '' },
        h('span', { class: 'fn-label' }, st[1]),
        h('span', { class: 'fn-val' }, String(v), pct != null ? h('span', { class: 'pct' }, pct + '%') : null),
        h('div', { class: 'fn-bar' }, h('i', { style: { width: Math.max(v ? 2 : 0, (v / max) * 100) + '%' } })));
    }));
  }

  // Prefer the full table row (source, status, next follow-up) when opening a reply.
  function appRowFor(r) {
    const full = (S.apps || []).find(a => r.contact_id != null && a.contact_id === r.contact_id);
    return full || r;
  }
  function renderReplies() {
    const box = $('#replies');
    if (S.apps === null && !(S.charts && S.charts.latest_replies)) { box.replaceChildren(...[1, 2, 3].map(() => h('div', { class: 'rp' }, h('span', { class: 'skel', style: { width: '60%' } }), h('span', { class: 'skel', style: { width: '85%', marginTop: '6px' } })))); return; }
    const rows = replyList().slice(0, 4);
    if (!rows.length) { box.replaceChildren(emptyState('No replies yet', 'Replies are checked in Gmail at the start of every run.')); return; }
    const fresh = new Set(newReplies().map(replyKey));
    box.replaceChildren(...rows.map(r => h('button', { class: 'rp' + (fresh.has(replyKey(r)) ? ' is-new' : ''), type: 'button', onclick: () => openApp(appRowFor(r)) },
      h('div', { class: 'rp-top' }, h('span', { class: 'rp-who' }, str(r.contact_name) || str(r.email) || '—', fresh.has(replyKey(r)) ? h('span', { class: 'rp-new' }, 'New') : null), h('span', { class: 'rp-when' }, relEl(r.replied_at || r.last_touch_at))),
      h('div', { class: 'rp-co' }, [splitCompany(r.company)[0], str(r.role)].filter(Boolean).join(' — ')),
      (r.reply_snippet || r.last_reply_snippet) ? h('div', { class: 'rp-snip' }, str(r.reply_snippet || r.last_reply_snippet)) : null)));
  }

  /* ═════════════════════════ Replies: read / unread (per browser) ═════════════════════════ */

  const SEEN_KEY = 'jh-seen-replies';
  function replyList() {
    const latest = asList(S.charts && S.charts.latest_replies, []);
    return (latest.length ? latest.map(r => Object.assign({ app_status: 'replied', replied_at: r.received_at, reply_snippet: r.snippet }, r))
      : (S.apps || []).filter(r => str(r.app_status) === 'replied'))
      .sort((a, b) => (parseTs(b.replied_at || b.last_touch_at) || 0) - (parseTs(a.replied_at || a.last_touch_at) || 0));
  }
  function replyKey(r) { return r.id != null && r.received_at ? 'r' + r.id : 'c' + str(r.contact_id) + '@' + str(r.replied_at || r.last_touch_at); }
  function saveSeen() { try { localStorage.setItem(SEEN_KEY, JSON.stringify(Array.from(S.seenReplies).slice(-500))); } catch (e) { /* not persisted; fine */ } }
  // "New" = not opened in this browser yet. First visit ever: anything older than 3 days already counts as read.
  function newReplies() {
    // Wait for /api/charts (latest_replies) so the first-visit baseline uses the same keys as later visits.
    if (S.charts === null) return [];
    const list = replyList();
    if (!S.seenReplies) {
      let stored = null;
      try { const v = localStorage.getItem(SEEN_KEY); if (v) stored = JSON.parse(v); } catch (e) { stored = null; }
      if (Array.isArray(stored)) S.seenReplies = new Set(stored);
      else {
        const cut = Date.now() - 3 * 86400000;
        S.seenReplies = new Set(list.filter(r => (parseTs(r.replied_at || r.last_touch_at) || 0) < cut).map(replyKey));
        saveSeen();
      }
    }
    return list.filter(r => !S.seenReplies.has(replyKey(r)));
  }
  function markRepliesSeen(pred) {
    const fresh = newReplies().filter(pred);
    if (!fresh.length) return;
    fresh.forEach(r => S.seenReplies.add(replyKey(r)));
    saveSeen(); renderReplies(); renderNeeds();
  }

  /* ═════════════════════════ Needs you ═════════════════════════ */

  function goTab(id) {
    if (S.tab !== id) { S.tab = id; S.shown = PAGE; renderTabs(); renderAppsBody(); }
    scrollToEl($('#apps'));
  }
  function showFeedFor(lane) {
    S.feedFilter = LANE_BY_ID[lane] ? lane : 'all';
    renderChips(); applyFeedFilter(); scrollFeedBottom(true);
    scrollToEl($('#live-panel'));
  }
  function gmailMissing() {
    const sm = S.summary || {};
    if (typeof sm.gmail_configured === 'boolean') return !sm.gmail_configured;
    if (S.settings) return !str(pickSetting('gmail')) || (get(S.settings, 'email.app_password_set') === false);
    return !!(S.env || []).find(c => /^gmail(_configured)?$/i.test(str(c.id)) && c.ok === false);
  }
  function listNames(arr, max) {
    const names = arr.slice(0, max);
    return names.join(', ') + (arr.length > max ? ' +' + (arr.length - max) + ' more' : '');
  }

  function renderNeeds() {
    const box = $('#needs');
    if (!box) return;
    if (!S.summary) { box.hidden = true; return; }
    const sm = S.summary;
    const items = [];
    if (gmailMissing()) items.push({ tone: 'accent', title: 'Set up Gmail to start sending', detail: 'Add your address and an app password.', act: ['Set up Gmail', () => openSettings(), true] });

    const errLanes = S.running ? [] : LANES.filter(l => S.lanes[l.id] && S.lanes[l.id].state === 'error');
    if (errLanes.length) items.push({ tone: 'bad', title: errLanes.length === 1 ? errLanes[0].name + ' failed last run' : plural(errLanes.length, 'lane') + ' failed last run', detail: errLanes.length === 1 ? (S.lanes[errLanes[0].id].action || 'See the activity feed.') : errLanes.map(l => l.name).join(', '), act: ['Show activity', () => showFeedFor(errLanes.length === 1 ? errLanes[0].id : 'all')] });
    else if (!S.running && S.runStatus === 'error') items.push({ tone: 'bad', title: 'Last run ended with an error', detail: 'The activity feed has the details.', act: ['Show activity', () => showFeedFor('all')] });

    const open = (S.portal || []).filter(portalOpen);
    const coName = p => splitCompany(p.company || get(p, 'job.company'))[0] || '—';
    // auto-submit on: a ready form is not his job — it is being submitted by itself
    const readyL = autoSub() ? [] : open.filter(p => num(p.ready) === 1 && !p.approved_at);
    const sendingL = open.filter(p => p.approved_at || (autoSub() && num(p.ready) === 1));
    const needsL = open.filter(p => num(p.ready) !== 1 && !p.approved_at);
    if (readyL.length) items.push({ tone: 'accent', n: readyL.length, title: readyL.length === 1 ? 'application ready to submit' : 'applications ready to submit', detail: listNames(readyL.map(coName), 2), act: ['Review & submit', () => { S.pSel = new Set(readyL.map(p => p.id)); goTab('portal'); }, true] });
    if (sendingL.length) items.push({ tone: 'ok', n: sendingL.length, title: 'being submitted', detail: listNames(sendingL.map(coName), 2), act: ['View', () => goTab('portal')] });
    if (needsL.length) items.push({ tone: 'warn', n: needsL.length, title: needsL.length === 1 ? 'form needs answers' : 'forms need answers', detail: listNames(needsL.map(coName), 2), act: ['Review', () => goTab('portal')] });

    const qOpen = S.qCounts ? num(S.qCounts.open) : 0;
    if (qOpen) items.push({ tone: 'accent', n: qOpen, title: qOpen === 1 ? 'question for you' : 'questions for you', detail: 'Answer once — future forms fill it for you.', act: ['Answer', () => goTab('questions'), true] });

    const fresh = newReplies();
    if (fresh.length) items.push({ tone: 'ok', n: fresh.length, title: fresh.length === 1 ? 'new reply' : 'new replies', detail: listNames(fresh.map(r => str(r.contact_name) || str(r.from_addr) || '—'), 2), act: ['Read', () => openApp(appRowFor(fresh[0]))] });

    const weekAgo = Date.now() - 7 * 86400000;
    const bounced = (S.apps || []).filter(r => str(r.app_status) === 'bounced' && (parseTs(r.last_touch_at || r.sent_at) || 0) >= weekAgo);
    if (bounced.length) items.push({ tone: 'warn', n: bounced.length, title: bounced.length === 1 ? 'bounce this week' : 'bounces this week', detail: 'Try another contact at ' + listNames(Array.from(new Set(bounced.map(r => splitCompany(r.company)[0]))), 2), act: ['View', () => goTab('bounced')] });

    const dryOn = typeof sm.dry_run === 'boolean' ? sm.dry_run : !!(S.settings && pickSetting('dry_run'));
    if (dryOn) items.push({ tone: 'warn', title: 'Dry run is on', detail: 'Emails are built but not sent.', act: ['Turn off', () => openSettings('dry_run')] });

    if (S.apps && !S.apps.length && !S.running) items.push({ tone: 'neutral', title: 'Import your old tracker.csv', detail: 'So past contacts are never emailed twice.', act: ['Import', () => openSettings('import')] });

    box.hidden = false;
    box.classList.toggle('is-clear', !items.length);
    const label = h('h2', { class: 'needs-label' }, 'Needs you', items.length ? h('span', { class: 'needs-count tnum' }, items.length) : null);
    if (!items.length) {
      const due = num(sm.nudges_due);
      box.replaceChildren(label, h('div', { class: 'needs-clear' },
        h('span', { class: 'needs-ok', svg: ICON.check }),
        h('span', null, 'Nothing right now.'),
        h('span', { class: 'muted' }, due ? plural(due, 'follow-up') + ' will go out on the next run.' : 'Replies and forms to submit will show up here.')));
      return;
    }
    box.replaceChildren(label, h('ul', { class: 'needs-list' }, items.map(it => h('li', { class: 'need need-' + it.tone },
      h('span', { class: 'need-mark', 'aria-hidden': 'true' }),
      h('div', { class: 'need-main' },
        h('div', { class: 'need-title' }, it.n != null ? h('b', { class: 'need-n tnum' }, it.n) : null, it.n != null ? ' ' : null, it.title),
        it.detail ? h('div', { class: 'need-detail', title: typeof it.detail === 'string' ? it.detail : null }, it.detail) : null),
      h('button', { class: 'btn btn-sm' + (it.act[2] ? ' btn-primary' : ''), type: 'button', onclick: it.act[1] }, it.act[0])))));
  }

  /* ═════════════════════════ Settings ═════════════════════════ */

  // Each field lists candidate config paths; the first one present in GET /api/settings is used
  // and written back on save, so the UI adapts to the backend's config.json shape.
  const FIELDS = {
    gmail: { paths: ['email.address', 'email.gmail_address', 'email.user', 'gmail_address', 'gmail.address'], def: 'email.address', type: 'text' },
    password: { paths: ['email.app_password', 'email.gmail_app_password', 'gmail_app_password', 'app_password', 'gmail.app_password'], def: 'email.app_password', type: 'password' },
    dry_run: { paths: ['dry_run', 'email.dry_run', 'safety.dry_run'], def: 'dry_run', type: 'bool' },
    max_emails: { paths: ['max_emails_per_run', 'email.max_emails_per_run', 'safety.max_emails_per_run'], def: 'max_emails_per_run', type: 'int' },
    auto_run: { paths: ['auto_run_on_open', 'run.auto_run_on_open'], def: 'auto_run_on_open', type: 'bool' },
    lookback: { paths: ['lookback'], def: 'lookback', type: 'text' },
    easy_apply: { paths: ['easy_apply'], def: 'easy_apply', type: 'bool' },
    auto_submit: { paths: ['auto_submit'], def: 'auto_submit', type: 'bool' },
    li_fast: { paths: ['li_fast.on'], def: 'li_fast.on', type: 'bool' },
    parallel: { paths: ['parallel_sources', 'engine.parallel_sources', 'run.parallel_sources'], def: 'parallel_sources', type: 'bool' },
    claude_path: { paths: ['engine.claude_path', 'claude_path'], def: 'engine.claude_path', type: 'text' },
    chrome_browser: { paths: ['engine.chrome_browser'], def: 'engine.chrome_browser', type: 'text' },
    chrome_device_id: { paths: ['engine.chrome_device_id'], def: 'engine.chrome_device_id', type: 'text' },
    claude_args: { paths: ['engine.claude_args', 'claude_args'], def: 'engine.claude_args', type: 'args' },
    lane_timeout: { paths: ['engine.lane_timeout_min', 'lane_timeout_min'], def: 'engine.lane_timeout_min', type: 'int' },
    model: { paths: ['engine.model'], def: 'engine.model', type: 'text' },
    mock: { paths: ['engine.mock', 'mock_engine'], def: 'engine.mock', type: 'bool' },
    backend: { paths: ['email.backend', 'email_backend'], def: 'email.backend', type: 'text' },
  };
  function fieldPath(f) { const p = FIELDS[f].paths.find(x => has(S.settings, x)); return p || FIELDS[f].def; }
  function pickSetting(name) {
    const alias = { parallel_sources: 'parallel', dry_run: 'dry_run', gmail: 'gmail' }[name] || name;
    if (!S.settings || !FIELDS[alias]) return undefined;
    return get(S.settings, fieldPath(alias));
  }

  function switchEl(id, checked, warn) {
    return h('span', { class: 'switch' + (warn ? ' warn' : '') }, h('input', { type: 'checkbox', id: id, role: 'switch', checked: checked ? true : null }), h('span', { 'aria-hidden': 'true' }));
  }
  function toggleRow(id, label, help, checked, warn) {
    return h('div', { class: 'toggle-row' },
      h('label', { for: id, style: { cursor: 'pointer' } }, h('div', { class: 't-label' }, label), help ? h('div', { class: 'help' }, help) : null),
      switchEl(id, checked, warn));
  }
  function fs(title, desc, fields) {
    return h('fieldset', { class: 'fs' },
      h('div', null, h('legend', { class: 'fs-title' }, title), desc ? h('div', { class: 'fs-desc' }, desc) : null),
      h('div', { class: 'fs-fields' }, fields));
  }
  function checksList(list, emptyText) {
    if (!list || !list.length) return h('p', { class: 'muted' }, emptyText || 'No checks reported.');
    return h('ul', { class: 'checks' }, list.map(c => h('li', { class: c.ok ? 'ck-ok' : 'ck-bad' },
      h('span', { class: 'ck-ico', svg: c.ok ? ICON.check : ICON.x, 'aria-label': c.ok ? 'OK' : 'Problem' }),
      h('div', { style: { minWidth: 0 } },
        h('div', { class: 'ck-label' }, str(c.label || c.id)),
        c.detail ? h('div', { class: 'ck-detail' }, str(c.detail)) : null,
        !c.ok && c.fix ? h('div', { class: 'ck-fix' }, str(c.fix)) : null))));
  }

  async function openSettings(focus) {
    const modal = $('#modal');
    modal.hidden = false;
    $('#settings-status').textContent = '';
    const form = $('#settings-form');
    form.replaceChildren(h('div', { class: 'd-skel', style: { padding: '24px 0' } }, [50, 80, 65, 40, 70].map(w => h('span', { class: 'skel', style: { width: w + '%', height: '12px' } }))));
    if (!S.settings) await loadSettings();
    buildSettingsForm(form);
    loadEnv().then(renderEnvInModal);
    setTimeout(() => {
      if (focus === 'import') { const b = $('#btn-import'); if (b) { b.scrollIntoView({ block: 'center' }); b.focus(); } }
      else if (focus === 'dry_run') { const el = $('#f-dry_run'); if (el) { el.scrollIntoView({ block: 'center' }); el.focus(); } }
      else if (focus === 'env') { const el = $('#env-list'); if (el) el.scrollIntoView({ block: 'start' }); }
      else { const el = $('#f-gmail'); if (el) el.focus(); }
    }, 40);
  }
  function closeSettings() { const m = $('#modal'); if (m.hidden) return false; m.hidden = true; hideTip(); const g = $('#btn-settings'); if (g) g.focus(); return true; }

  function buildSettingsForm(form) {
    const cfg = S.settings || {};
    const val = f => get(cfg, fieldPath(f));
    const pwSaved = get(cfg, 'email.app_password_set') != null ? !!get(cfg, 'email.app_password_set') : !!str(val('password'));
    const args = val('claude_args');
    const argsText = Array.isArray(args) ? args.join('\n') : str(args);

    const pw = h('input', { type: 'password', id: 'f-password', autocomplete: 'new-password', spellcheck: 'false', placeholder: pwSaved ? 'Saved — type a new one to replace it' : '16-character app password' });
    const pwToggle = h('button', { class: 'btn btn-sm', type: 'button', 'aria-controls': 'f-password' }, 'Show');
    pwToggle.onclick = () => { const show = pw.type === 'password'; pw.type = show ? 'text' : 'password'; pwToggle.textContent = show ? 'Hide' : 'Show'; };
    const testRes = h('span', { class: 'result', id: 'test-email-result', 'aria-live': 'polite' });
    const testBtn = h('button', { class: 'btn btn-sm', type: 'button', id: 'btn-test-email' }, 'Test email connection');
    testBtn.onclick = () => testEmail(testBtn, testRes);

    const engRes = h('div', { id: 'engine-result', 'aria-live': 'polite' });
    const engBtn = h('button', { class: 'btn btn-sm', type: 'button', id: 'btn-engine-test' }, 'Run engine test');
    engBtn.onclick = () => engineTest(engBtn, engRes);

    const file = h('input', { type: 'file', id: 'f-import', accept: '.csv,text/csv', hidden: true });
    const impBtn = h('button', { class: 'btn btn-sm', type: 'button', id: 'btn-import' }, 'Import tracker.csv');
    const impRes = h('span', { class: 'result', 'aria-live': 'polite' });
    impBtn.onclick = () => file.click();
    file.onchange = () => { if (file.files && file.files[0]) importCsv(file.files[0], impBtn, impRes); file.value = ''; };

    const recheck = h('button', { class: 'btn btn-sm btn-ghost', type: 'button' }, 'Check again');
    recheck.onclick = async () => { recheck.disabled = true; $('#env-list').replaceChildren(h('span', { class: 'spinner' })); await loadEnv(); renderEnvInModal(); recheck.disabled = false; };

    const backend = str(val('backend')) || 'smtp';
    form.replaceChildren(
      fs('Gmail', 'Used to send applications and read replies. Stays on this computer.', [
        h('div', { class: 'field' }, h('label', { for: 'f-gmail' }, 'Gmail address'),
          h('input', { type: 'email', id: 'f-gmail', value: str(val('gmail')), placeholder: 'you@gmail.com', spellcheck: 'false' })),
        h('div', { class: 'field' }, h('label', { for: 'f-password' }, 'App password'),
          h('div', { class: 'field-row' }, pw, pwToggle),
          h('div', { class: 'help' }, 'Not your Google password. Turn on 2-Step Verification, then create one at ',
            h('a', { href: 'https://myaccount.google.com/apppasswords', target: '_blank', rel: 'noopener noreferrer' }, 'myaccount.google.com/apppasswords'),
            ' and paste the 16 characters. Spaces are fine.')),
        h('div', { class: 'field-row' }, testBtn, testRes),
      ]),
      fs('Finding emails', 'When SalesQL finds no work email, Job Hunter first checks people at that company who already wrote to you (Gmail), then these services if you add a key. Only addresses they verify are used.', [
        h('div', { class: 'field' }, h('label', { for: 'f-hunter' }, 'Hunter.io API key (optional)'),
          h('input', { type: 'password', id: 'f-hunter', autocomplete: 'off', spellcheck: 'false', placeholder: get(cfg, 'email_finder.hunter_api_key_set') ? 'Saved — paste a new key to replace it' : 'Paste your key' }),
          h('div', { class: 'help' }, 'Free plan: 25 searches a month. Get it at ', h('a', { href: 'https://hunter.io/api-keys', target: '_blank', rel: 'noopener noreferrer' }, 'hunter.io/api-keys'), '.')),
        h('div', { class: 'field' }, h('label', { for: 'f-apollo' }, 'Apollo.io API key (optional)'),
          h('input', { type: 'password', id: 'f-apollo', autocomplete: 'off', spellcheck: 'false', placeholder: get(cfg, 'email_finder.apollo_api_key_set') ? 'Saved — paste a new key to replace it' : 'Paste your key' }),
          h('div', { class: 'help' }, 'Uses your Apollo email credits. Apollo: Settings → Integrations → API.')),
        h('div', { class: 'field' }, h('label', { for: 'f-finder_cap' }, 'Paid lookups per day'),
          h('input', { type: 'number', id: 'f-finder_cap', min: '0', max: '500', value: String(num(get(cfg, 'email_finder.daily_cap'), 25)) })),
      ]),
      fs('Sending', 'Safety limits for every run.', [
        toggleRow('f-dry_run', 'Dry run', 'Builds each resume and email but sends nothing. Turn off once the email test passes.', !!val('dry_run'), true),
        h('div', { class: 'field' }, h('label', { for: 'f-max_emails' }, 'Max emails per run'),
          h('input', { type: 'number', id: 'f-max_emails', min: '0', max: '500', step: '1', value: val('max_emails') != null ? String(val('max_emails')) : '25' }),
          h('div', { class: 'help' }, 'The run stops sending new applications after this many. Nudges are not counted.')),
      ]),
      fs('Runs', null, [
        toggleRow('f-auto_run', 'Start a run when Job Hunter opens', 'Double-clicking the Desktop icon opens the dashboard and starts right away.', val('auto_run') == null ? true : !!val('auto_run')),
        h('div', { class: 'field' }, h('label', { for: 'f-lookback' }, 'Job age'),
          h('select', { id: 'f-lookback' },
            h('option', { value: '24h', selected: val('lookback') !== 'week' ? true : null }, 'Past 24 hours (daily)'),
            h('option', { value: 'week', selected: val('lookback') === 'week' ? true : null }, 'Past week (catch-up)')),
          h('div', { class: 'help' }, 'How old a job or post can be. Use Past week after a break, then switch back to 24 hours.')),
        toggleRow('f-easy_apply', 'LinkedIn Easy Apply prefill', 'Fills Easy Apply forms up to the Review step.', val('easy_apply') == null ? true : !!val('easy_apply')),
        toggleRow('f-li_fast', 'Apply to new LinkedIn jobs by itself', 'While the app is open (8 AM to midnight), every 30 min your own Chrome checks LinkedIn Easy Apply + Remote jobs from the past hour and applies at once — max 5 per check, 30 a day. Off: LinkedIn only when you click Run.', val('li_fast') == null ? true : !!val('li_fast')),
        toggleRow('f-auto_submit', 'Submit ready applications by itself', 'Every fully filled form (Easy Apply and job-portal forms) is submitted without your click. Off: you approve each batch with Submit selected.', val('auto_submit') == null ? true : !!val('auto_submit')),
        h('div', { class: 'field' }, h('label', { for: 'f-chrome_device_id' }, 'Chrome for LinkedIn (locked to this laptop)'),
          h('input', { type: 'text', id: 'f-chrome_device_id', value: str(val('chrome_device_id')), placeholder: 'Picked automatically on the next run', spellcheck: 'false' }),
          h('div', { class: 'help' }, 'Device id of YOUR Chrome (akshay.verma0031 profile, Akshay’s LinkedIn) on this laptop. Only this browser is ever used for LinkedIn and forms. Clear it to let the next run find your Chrome on this laptop again.')),
        toggleRow('f-parallel', 'Run sources in parallel', 'Runs LinkedIn posts, JobRight and LinkedIn Jobs at the same time. Faster, but they share one Chrome — leave off unless you know it works.', !!val('parallel')),
      ]),
      fs('Engine', 'Claude Code runs the browser lanes. Change these only if the engine test fails.', [
        h('div', { class: 'field' }, h('label', { for: 'f-claude_path' }, 'Claude CLI path'),
          h('input', { type: 'text', id: 'f-claude_path', value: str(val('claude_path')), placeholder: 'Found automatically', spellcheck: 'false', readonly: true }),
          h('div', { class: 'help' }, 'Read-only here for safety (a web page must never be able to change which program runs). To change it, edit engine.claude_path in data/config.json.')),
        h('div', { class: 'field' }, h('label', { for: 'f-claude_args' }, 'Extra arguments'),
          h('textarea', { id: 'f-claude_args', rows: '5', spellcheck: 'false', readonly: true }, argsText),
          h('div', { class: 'help' }, 'Read-only here for safety. To change them, edit engine.claude_args in data/config.json.')),
        h('div', { class: 'field' }, h('label', { for: 'f-model' }, 'AI model'),
          h('select', { id: 'f-model', style: { width: '320px' } },
            h('option', { value: 'sonnet', selected: ['', 'sonnet'].includes(str(val('model'))) ? true : null }, 'Sonnet — recommended, about 5x cheaper'),
            h('option', { value: 'opus', selected: str(val('model')) === 'opus' ? true : null }, 'Opus — strongest, most expensive'),
            h('option', { value: 'default', selected: str(val('model')) === 'default' ? true : null }, "Claude's own default"),
            !['', 'sonnet', 'opus', 'default'].includes(str(val('model'))) ? h('option', { value: str(val('model')), selected: true }, str(val('model')) + ' (from config.json)') : null),
          h('div', { class: 'help' }, 'The model the browser lanes use. Sonnet handles searching, SalesQL and forms well and saves most of the cost.')),
        h('div', { class: 'field' }, h('label', { for: 'f-lane_timeout' }, 'Lane timeout (minutes)'),
          h('input', { type: 'number', id: 'f-lane_timeout', min: '1', max: '240', value: val('lane_timeout') != null ? String(val('lane_timeout')) : '25' })),
        h('div', { class: 'field' }, h('label', { for: 'f-backend' }, 'Email backend'),
          h('select', { id: 'f-backend', style: { width: '240px' } },
            h('option', { value: 'smtp', selected: backend === 'smtp' ? true : null }, 'Gmail (SMTP + IMAP)'),
            h('option', { value: 'file', selected: backend === 'file' ? true : null }, 'Files only (testing)'))),
        toggleRow('f-mock', 'Mock engine', 'Replays a scripted run instead of starting Claude. For testing only.', !!val('mock')),
        h('div', { class: 'field' },
          h('div', { class: 'field-row' }, engBtn, h('span', { class: 'help', style: { color: 'var(--muted)', fontSize: '12px' } }, 'Starts a short Claude session to confirm the Job Hunter tools and Chrome both respond. Takes up to a minute.')),
          engRes),
      ]),
      fs('Environment', h('span', null, 'What this computer needs before a run.', h('br'), recheck), [
        h('div', { id: 'env-list' }, S.env ? checksList(S.env) : h('span', { class: 'spinner', 'aria-label': 'Checking' })),
      ]),
      fs('History', 'Bring in applications you sent before Job Hunter so they are never contacted twice and nudges continue.', [
        h('div', { class: 'field' },
          h('div', { class: 'field-row' }, impBtn, impRes, file),
          h('div', { class: 'help' }, 'Expects the columns date, company, role, contact, title, email, status, next_step.')),
      ]),
    );
    form.onsubmit = e => { e.preventDefault(); saveSettings(); };
  }
  function renderEnvInModal() {
    const box = $('#env-list');
    if (box) box.replaceChildren(S.env ? checksList(S.env) : h('p', { class: 'muted' }, 'Environment checks aren’t available from the server.'));
  }

  function collectSettings() {
    const out = JSON.parse(JSON.stringify(S.settings || {}));
    const v = id => { const el = $('#f-' + id); return el ? el : null; };
    const put = (f, value) => setPath(out, fieldPath(f), value);
    if (v('gmail')) put('gmail', v('gmail').value.trim());
    if (v('password') && v('password').value) put('password', v('password').value.replace(/\s+/g, ''));
    // empty password field = keep the saved one (the masked value is sent back unchanged)
    if (v('dry_run')) put('dry_run', v('dry_run').checked);
    if (v('max_emails')) put('max_emails', Math.max(0, Math.round(num(v('max_emails').value, 25))));
    if (v('auto_run')) put('auto_run', v('auto_run').checked);
    if (v('easy_apply')) put('easy_apply', v('easy_apply').checked);
    if (v('auto_submit')) put('auto_submit', v('auto_submit').checked);
    if (v('li_fast')) put('li_fast', v('li_fast').checked);
    if (v('lookback')) put('lookback', v('lookback').value === 'week' ? 'week' : '24h');
    if (v('parallel')) put('parallel', v('parallel').checked);
    if (v('claude_path')) put('claude_path', v('claude_path').value.trim());
    if (v('chrome_device_id')) put('chrome_device_id', v('chrome_device_id').value.trim().slice(0, 64));
    if (v('claude_args')) {
      const orig = get(S.settings, fieldPath('claude_args'));
      const lines = v('claude_args').value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
      put('claude_args', typeof orig === 'string' ? lines.join(' ') : lines);
    }
    if (v('lane_timeout')) put('lane_timeout', Math.max(1, Math.round(num(v('lane_timeout').value, 25))));
    if (v('mock')) put('mock', v('mock').checked);
    if (v('model')) put('model', v('model').value);
    if (v('backend')) put('backend', v('backend').value);
    const ef = Object.assign({}, out.email_finder || {});
    delete ef.hunter_api_key_set; delete ef.apollo_api_key_set;
    if (v('hunter') && v('hunter').value.trim()) ef.hunter_api_key = v('hunter').value.trim();
    if (v('apollo') && v('apollo').value.trim()) ef.apollo_api_key = v('apollo').value.trim();
    if (v('finder_cap')) ef.daily_cap = Math.max(0, Math.min(500, Math.round(num(v('finder_cap').value, 25))));
    out.email_finder = ef;
    return out;
  }
  async function saveSettings() {
    const btn = $('#settings-save');
    const st = $('#settings-status');
    const gm = $('#f-gmail');
    if (gm && gm.value.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(gm.value.trim())) { st.textContent = 'That Gmail address doesn’t look right.'; st.style.color = 'var(--bad)'; gm.focus(); return; }
    btn.disabled = true; st.style.color = ''; st.textContent = 'Saving…';
    try {
      const payload = collectSettings();
      await api('/api/settings', { method: 'POST', body: payload, label: 'Saving settings' });
      S.settings = payload;
      await loadSettings(); // re-read so the masked password and server-side defaults come back
      toast('Settings saved.', 'ok');
      closeSettings();
      loadEnv();
    } catch (e) { st.textContent = 'Not saved: ' + (e.message || 'error'); st.style.color = 'var(--bad)'; }
    finally { btn.disabled = false; }
  }
  async function testEmail(btn, res) {
    btn.disabled = true;
    res.className = 'result'; res.replaceChildren(h('span', { class: 'spinner' }), 'Signing in to Gmail…');
    const body = {};  // same shape as config.json so the server tests what's in the form
    const gm = $('#f-gmail'), pw = $('#f-password');
    if (gm && gm.value.trim()) setPath(body, fieldPath('gmail'), gm.value.trim());
    if (pw && pw.value) setPath(body, fieldPath('password'), pw.value.replace(/\s+/g, ''));
    try {
      const d = await api('/api/settings/test-email', { method: 'POST', body: body, label: 'Email test', quiet: true, timeout: 45000 });
      if (d && d.ok) { res.className = 'result ok'; res.replaceChildren(h('span', { svg: ICON.check, style: { width: '12px', display: 'inline-flex' } }), 'Connected — sending and reading both work.'); }
      else { res.className = 'result bad'; res.textContent = 'Failed: ' + (str(d && d.error) || 'unknown error'); }
    } catch (e) {
      res.className = 'result bad';
      res.textContent = e.status === 404 ? 'The server doesn’t support the email test yet.' : 'Failed: ' + (e.message || 'no response');
    } finally { btn.disabled = false; }
  }
  async function engineTest(btn, res) {
    btn.disabled = true;
    res.replaceChildren(h('p', { class: 'muted', style: { display: 'flex', gap: '8px', alignItems: 'center', marginTop: '8px' } }, h('span', { class: 'spinner' }), 'Starting Claude Code… this can take up to a minute.'));
    try {
      let d;
      try { d = await api('/api/engine-test', { method: 'POST', body: {}, label: 'Engine test', quiet: true, timeout: 240000 }); }
      catch (e) { if (e.status !== 404) throw e; d = await api('/api/env/engine-test', { method: 'POST', body: {}, label: 'Engine test', quiet: true, timeout: 240000 }); }
      const checks = asList(d, ['checks']);
      res.replaceChildren(
        h('p', { class: 'result ' + (d && d.ok ? 'ok' : 'bad'), style: { margin: '8px 0' } }, d && d.ok ? 'Engine works: Job Hunter tools and Chrome both responded.' : 'Engine test found problems' + (d && d.error ? ': ' + str(d.error) : '.')),
        checks.length ? checksList(checks.map(c => ({ id: c.id, label: c.label || prettyId(c.id), ok: c.ok, detail: c.detail, fix: c.fix }))) : null);
    } catch (e) {
      res.replaceChildren(h('p', { class: 'result bad', style: { margin: '8px 0' } }, e.status === 404 ? 'The server doesn’t support the engine test yet.' : 'Engine test failed: ' + (e.message || 'no response')));
    } finally { btn.disabled = false; }
  }
  const CHECK_LABELS = { claude_found: 'Claude Code found', version: 'Claude Code version', auth: 'Logged in to Claude', run: 'Headless run (-p)',
    mcp_jobhunt: 'Job Hunter tools (MCP) connected', log_step: 'Job Hunter tool call', chrome: 'Chrome tool call', cli: 'Claude Code found', engine: 'AI engine' };
  function prettyId(id) { if (CHECK_LABELS[id]) return CHECK_LABELS[id]; id = str(id).replace(/[_-]+/g, ' '); return id.charAt(0).toUpperCase() + id.slice(1); }
  async function importCsv(file, btn, res) {
    btn.disabled = true;
    res.className = 'result'; res.replaceChildren(h('span', { class: 'spinner' }), 'Importing ' + file.name + '…');
    const fd = new FormData(); fd.append('file', file, file.name);
    try {
      const d = await api('/api/import', { method: 'POST', form: fd, label: 'Import', quiet: true, timeout: 60000 });
      const c = (d && d.counts) || {};
      const parts = [];
      if (c.contacts != null) parts.push(plural(num(c.contacts), 'contact'));
      if (c.skipped_jobs != null) parts.push(plural(num(c.skipped_jobs), 'skipped job'));
      if (c.duplicates) parts.push(num(c.duplicates) + ' already in Job Hunter');
      if (!parts.length && d && d.imported != null) parts.push(plural(num(d.imported), 'row') + ' imported');
      res.className = 'result ok'; res.textContent = parts.join(', ') || 'Imported.';
      toast('History imported' + (parts.length ? ': ' + parts.join(', ') : '.'), 'ok');
      loadApps(true); loadSkipped(true); loadSummary(true); loadCharts(true);
    } catch (e) { res.className = 'result bad'; res.textContent = 'Import failed: ' + (e.message || 'error'); }
    finally { btn.disabled = false; }
  }

  /* ═════════════════════════ Banners ═════════════════════════ */

  function renderBanners() {
    const box = $('#banners');
    const out = [];
    const env = S.env || [];
    const failing = re => env.find(c => re.test(str(c.id)) && c.ok === false);

    // Gmail setup and dry run live in the Needs-you strip; banners are kept for blocking setup problems with steps.
    const cli = failing(/^claude(_cli|_path|_installed|_version)?$|claude.*(cli|install|path|found)/i);
    const login = !cli && failing(/claude.*(login|auth)/i);
    if (cli) {
      out.push(h('div', { class: 'banner banner-bad' }, h('span', { svg: ICON.term, style: { display: 'contents' } }),
        h('div', { class: 'banner-main' }, h('div', { class: 'banner-title' }, 'Claude Code isn’t installed'),
          h('div', { class: 'banner-text' }, 'The browser lanes need the Claude Code CLI. Follow-ups still run without it.'),
          h('ol', { class: 'banner-steps' },
            h('li', null, 'Open PowerShell and run ', h('code', null, 'irm https://claude.ai/install.ps1 | iex')),
            h('li', null, 'Run ', h('code', null, 'claude'), ' once and log in with your Claude account.'),
            h('li', null, 'Come back here and choose Check again in Settings.'))),
        h('div', { class: 'banner-actions' }, h('button', { class: 'btn btn-sm', type: 'button', onclick: () => openSettings('env') }, 'Check again'))));
    } else if (login) {
      out.push(h('div', { class: 'banner banner-bad' }, h('span', { svg: ICON.term, style: { display: 'contents' } }),
        h('div', { class: 'banner-main' }, h('div', { class: 'banner-title' }, 'Log in to Claude Code'),
          h('div', { class: 'banner-text' }, 'Open PowerShell, run ', h('code', null, 'claude'), ' and sign in with your Claude subscription. ', str(login.detail))),
        h('div', { class: 'banner-actions' }, h('button', { class: 'btn btn-sm', type: 'button', onclick: () => openSettings('env') }, 'Check again'))));
    }
    box.replaceChildren(...out);
    renderNeeds();
  }

  /* ═════════════════════════ Run picker ═════════════════════════ */

  const RUN_LANES = [['followups', 'Follow-ups'], ['posts', 'LinkedIn posts'], ['jobright', 'JobRight'],
    ['linkedin_jobs', 'LinkedIn Jobs'], ['himalayas', 'Remote boards'], ['easy_apply', 'Easy Apply'], ['portal', 'Portal forms']];
  // off by default (architect review 2026-10-22, real data): JobRight 0 human replies, Remote-boards emails reach jobs
  // 3-4 days old, Portal forms mostly abandoned. One click turns each back on.
  const RUN_OPT_IN = new Set(['jobright', 'himalayas', 'portal']);
  const RUN_REGIONS = [['us', 'US'], ['india', 'India'], ['world', 'Worldwide']];
  const RUNSEL_KEY = 'jh-runsel-2';   // v2: new defaults (Easy Apply first, India + Worldwide)
  const RS = { lanes: new Set(RUN_LANES.map(x => x[0]).filter(id => !RUN_OPT_IN.has(id))), regions: new Set(['india', 'world']), locked: null };

  function loadRunSel() {
    try {
      const v = JSON.parse(localStorage.getItem(RUNSEL_KEY) || 'null');
      if (v && Array.isArray(v.lanes)) {
        RS.lanes = new Set(v.lanes.filter(l => RUN_LANES.some(x => x[0] === l)));
        // lanes added after the selection was saved start ticked
        const known = Array.isArray(v.known) ? v.known : ['followups', 'posts', 'jobright', 'linkedin_jobs', 'easy_apply', 'portal'];
        RUN_LANES.forEach(([id]) => { if (!known.includes(id) && !RUN_OPT_IN.has(id)) RS.lanes.add(id); });
      }
      if (v && Array.isArray(v.regions) && v.regions.length) RS.regions = new Set(v.regions.filter(r => RUN_REGIONS.some(x => x[0] === r)));
    } catch (e) { /* defaults */ }
  }
  function saveRunSel() {
    try { localStorage.setItem(RUNSEL_KEY, JSON.stringify({ lanes: [...RS.lanes], regions: [...RS.regions], known: RUN_LANES.map(x => x[0]) })); } catch (e) { /* not persisted */ }
  }
  function tickChip(id, label, on, disabled, onclick) {
    return h('button', { type: 'button', class: 'chip', 'aria-pressed': String(on), disabled: disabled, dataset: { id: id }, onclick: onclick },
      h('span', { class: 'tick', 'aria-hidden': 'true' }), label);
  }
  function renderRunbar() {
    const box = $('#rb-lanes'); if (!box) return;
    const locked = S.running;
    // while a run is going, show what that run is doing (if known), not the next selection
    const showLanes = locked && RS.locked ? new Set(RS.locked.lanes) : RS.lanes;
    const showRegions = locked && RS.locked ? new Set(RS.locked.regions) : RS.regions;
    box.replaceChildren(...RUN_LANES.map(([id, label]) => tickChip(id, label, showLanes.has(id), locked, () => {
      RS.lanes.has(id) ? RS.lanes.delete(id) : RS.lanes.add(id); saveRunSel(); renderRunbar();
    })));
    const regionsUsed = showLanes.has('linkedin_jobs') || showLanes.has('easy_apply') || !!(S.watch && S.watch.on);
    $('#rb-regions').replaceChildren(...RUN_REGIONS.map(([id, label]) => tickChip(id, label, showRegions.has(id), locked || !regionsUsed, () => {
      if (RS.regions.has(id)) { if (RS.regions.size > 1) RS.regions.delete(id); else return toast('Keep at least one region.', 'info', 'rbregion'); }
      else RS.regions.add(id);
      saveRunSel(); renderRunbar();
      if (S.watch && S.watch.on) setWatch({ regions: [...RS.regions] });
    })));
    $('#rb-regions-wrap').classList.toggle('is-off', !regionsUsed);
    $('#runbar').classList.toggle('is-locked', !!locked);
    const n = RS.lanes.size;
    $('#rb-note').textContent = locked ? 'This run'
      : n === 0 ? 'Pick at least one' : n === RUN_LANES.length ? 'Everything' : n + ' of ' + RUN_LANES.length + ' selected';
    const btn = $('#btn-run');
    if (btn && !locked) {
      btn.disabled = n === 0;
      $('span', btn).textContent = n === RUN_LANES.length || n === 0 ? 'Run now' : 'Run selected';
      btn.title = n ? 'Start: ' + RUN_LANES.filter(x => RS.lanes.has(x[0])).map(x => x[1]).join(', ') + ' (R)' : 'Pick what to run first';
    }
  }
  function setAllLanes(on) { RS.lanes = new Set(on ? RUN_LANES.map(x => x[0]) : []); saveRunSel(); renderRunbar(); }

  /* ═════════════════════════ Watch mode ═════════════════════════ */

  S.watch = null;
  let watchTimer = 0;
  async function loadWatch(quiet) {
    const d = await tryApi('/api/watch', { label: 'Watch mode', quiet: true });
    if (d && typeof d === 'object') S.watch = d;
    renderWatch();
    clearTimeout(watchTimer);
    watchTimer = setTimeout(() => loadWatch(true), S.watch && S.watch.on ? 30000 : 120000);
  }
  async function setWatch(patch, msg) {
    try {
      S.watch = await api('/api/watch', { method: 'POST', body: patch, label: 'Watch mode', quiet: true });
      if (msg) toast(msg, 'ok', 'watchset');
    } catch (e) { apiErrorToast('Watch mode', e); }
    renderWatch();
  }
  function fmtIn(sec) {
    if (sec == null) return '';
    if (sec < 60) return 'under a minute';
    return Math.round(sec / 60) + ' min';
  }
  function renderWatch() {
    const box = $('#rb-watch'); if (!box) return;
    const w = S.watch;
    if (!w) { box.replaceChildren(h('span', { class: 'runbar-note' }, '…')); return; }
    const on = !!w.on, auto = !!w.autopilot;
    const sw = h('button', { type: 'button', class: 'chip watch-switch' + (on ? ' is-on' : ''), 'aria-pressed': String(auto || on),
      title: auto ? 'Autopilot is on: Easy Apply jobs, replies, follow-ups and LinkedIn jobs are handled by themselves while the app is open. Click to stop.'
        : 'Turn on Autopilot: check LinkedIn every ' + (w.interval_min || 10) + ' min, replies every 15 min, and work new jobs by itself',
      onclick: () => setWatch(auto || on ? { autopilot: false } : { autopilot: true }, auto || on ? 'Autopilot off. Runs only start when you click Run.'
        : (w.linkedin_safe ? 'Autopilot on: replies, follow-ups and the Job board run by themselves. LinkedIn only when you click Run.'
          : 'Autopilot on. New jobs, replies and follow-ups are handled by themselves while the app is open.')) },
      h('span', { class: 'watch-dot', 'aria-hidden': 'true' }), auto ? 'Autopilot on' : on ? 'Watching' : 'Autopilot: off');
    const posts = h('button', { type: 'button', class: 'chip', 'aria-pressed': String(!!w.posts), disabled: !on, title: 'Also run a short LinkedIn-posts pass every ' + (w.posts_interval_min || 30) + ' min',
      onclick: () => setWatch({ posts: !w.posts }) }, h('span', { class: 'tick', 'aria-hidden': 'true' }), 'Posts every ' + (w.posts_interval_min || 30) + ' min');
    let note = '';
    if (w.linkedin_safe) {
      const bits = [w.li_fast ? 'LinkedIn: new jobs (past hour) applied to by itself every ' + (w.li_fast_every_min || 30) + ' min in your Chrome' + (w.next_li_fast_in_s != null ? ' · next in ' + fmtIn(w.next_li_fast_in_s) : '')
        : 'LinkedIn: only when you click Run (automatic LinkedIn is off to keep your account safe)'];
      if (auto && w.next_followups_in_s != null) bits.push('replies in ' + fmtIn(w.next_followups_in_s));
      if (auto && w.budget_usd) bits.push('AI today $' + num(w.spent_today).toFixed(2) + ' of $' + num(w.budget_usd));
      if (w.over_budget) bits.unshift('⚠ Today’s AI budget is used up: free checks continue');
      note = bits.join(' · ');
    } else if (on) {
      const bits = [];
      if (w.over_budget) bits.push('⚠ Today’s AI budget ($' + num(w.budget_usd) + ') is used up: free checks continue, AI work resumes tomorrow');
      if (w.last_error) bits.push('⚠ ' + w.last_error);
      else if (w.last_poll) bits.push('LinkedIn checked ' + fmtTime(w.last_poll));
      if (w.next_poll_in_s != null) bits.push('next in ' + fmtIn(w.next_poll_in_s));
      if (auto && w.next_followups_in_s != null) bits.push('replies in ' + fmtIn(w.next_followups_in_s));
      if (num(w.found_today)) bits.push(plural(num(w.found_today), 'new job') + ' today');
      if (num(w.queued)) bits.push(num(w.queued) + ' waiting');
      if (auto && w.budget_usd) bits.push('AI today $' + num(w.spent_today).toFixed(2) + ' of $' + num(w.budget_usd));
      note = bits.join(' · ');
    } else note = 'Off: nothing runs until you click Run. Autopilot reaches new jobs and replies within minutes.';
    const bell = ('Notification' in window) && Notification.permission !== 'granted'
      ? h('button', { type: 'button', class: 'btn btn-sm btn-ghost', title: 'Pop-up + sound when a job is found or a form is ready',
          onclick: () => Notification.requestPermission().then(() => renderWatch()) }, 'Notify me') : null;
    box.replaceChildren(...[sw, w.linkedin_safe ? null : posts, bell, h('span', { class: 'runbar-note watch-note' + (w.last_error ? ' is-warn' : ''), title: note }, note)].filter(Boolean));
  }
  function fmtTime(ts) {
    const d = parseTs(ts); if (!d) return '';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  let audioCtx = null;
  function beep() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = 880; g.gain.value = 0.06; o.connect(g); g.connect(audioCtx.destination);
      o.start(); o.stop(audioCtx.currentTime + 0.18);
    } catch (e) { /* no audio */ }
  }
  function notify(title, body, tab) {
    beep();
    try {
      if (!('Notification' in window) || Notification.permission !== 'granted') return;
      const n = new Notification('Job Hunter — ' + title, { body: body, tag: 'jh-' + tab + '-' + Date.now() });
      n.onclick = () => { window.focus(); if (tab === 'portal') goTab('portal'); n.close(); };
    } catch (e) { /* notifications blocked */ }
  }

  /* ═════════════════════════ Run controls ═════════════════════════ */

  async function loadProfiles() {
    const d = await tryApi('/api/profiles', { quiet: true });
    S.profiles = (d && Array.isArray(d.profiles)) ? d.profiles : [];
    renderProfileView();
    return S.profiles;
  }
  function renderProfileView() {
    const sel = $('#view-profile'); if (!sel) return;
    const list = S.profiles || [];
    sel.hidden = list.length <= 1;
    sel.replaceChildren(...list.map(p => h('option', { value: p.id, selected: (S.viewProfile || 'akshay') === p.id ? true : null }, 'Viewing: ' + p.name)));
  }
  function switchView(pid) {
    S.viewProfile = pid || 'akshay';
    S.summary = null; S.portal = null;
    loadSummary(true); loadApps(); loadSkipped(true); loadPortal(true); loadQuestions(true); loadCharts();
    const p = (S.profiles || []).find(x => x.id === S.viewProfile);
    toast('Showing ' + (p ? p.name : 'Akshay') + '’s applications', 'info', 'viewprof');
  }
  // Run button: "Apply with which profile?" (Akshay / Ravi ...). One profile only -> no question.
  async function pickProfile() {
    const list = S.profiles && S.profiles.length ? S.profiles : await loadProfiles();
    if (!list || list.length <= 1) return 'akshay';
    return new Promise(resolve => {
      let done = false;
      const close = v => { if (done) return; done = true; wrap.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
      const onKey = e => { if (e.key === 'Escape') close(null); };
      const btns = list.map(p => h('button', { type: 'button', class: 'btn ' + (p.default ? 'btn-primary' : 'btn-ghost') + ' profile-pick', style: { justifyContent: 'flex-start', width: '100%' }, onclick: () => close(p.id) },
        h('b', null, p.name), p.email ? h('span', { class: 'muted', style: { marginLeft: '8px' } }, p.email) : null));
      const wrap = h('div', { class: 'modal', id: 'profile-modal' },
        h('div', { class: 'modal-card', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'profile-title', style: { width: 'min(480px, 100%)' } },
          h('div', { class: 'modal-head' }, h('h2', { id: 'profile-title' }, 'Apply with which profile?'),
            h('button', { type: 'button', class: 'btn btn-icon', 'aria-label': 'Cancel', onclick: () => close(null) }, '×')),
          h('div', { class: 'modal-body', style: { display: 'grid', gap: '8px', paddingTop: '16px' } }, btns,
            h('p', { class: 'help' }, 'Akshay: the full run you picked. Other profiles: LinkedIn Easy Apply with their own resume, answers and Chrome — filled and submitted by itself; no emails are sent in their name.'))));
      wrap.addEventListener('click', e => { if (e.target === wrap) close(null); });
      document.body.appendChild(wrap); document.addEventListener('keydown', onKey);
      const first = wrap.querySelector('.profile-pick'); if (first) first.focus();
    });
  }

  async function runNow() {
    if (S.running) return;
    const btn = $('#btn-run');
    const prof = await pickProfile();
    if (!prof) return;
    if (prof !== 'akshay') return runAsProfile(prof);
    if (!RS.lanes.size) { toast('Pick what to run first (LinkedIn posts, Easy Apply, …).', 'info', 'rbnone'); return; }
    btn.disabled = true;
    try {
      const all = RS.lanes.size === RUN_LANES.length && RS.regions.size === RUN_REGIONS.length;
      const body = { trigger: 'button' };
      if (!all) { body.lanes = RUN_LANES.map(x => x[0]).filter(l => RS.lanes.has(l)); body.regions = [...RS.regions]; }
      RS.locked = { lanes: [...RS.lanes], regions: [...RS.regions] };
      const d = await api('/api/run', { method: 'POST', body: body, label: 'Starting a run', quiet: true });
      if (d && d.run_id != null && num(d.run_id) !== num(S.runId)) {
        S.runId = num(d.run_id);
        resetLanes('queued');
        addDivider('Run #' + d.run_id + ' requested');
      }
      S.running = true; S.runStatus = 'running';
      if (!S.runStartedAt || (d && d.run_id != null)) S.runStartedAt = new Date().toISOString();
      renderTop(); renderLanes();
      setTimeout(() => loadSummary(true), 800);
    } catch (e) {
      if (e.status === 409) { toast('A run is already in progress.', 'info', 'run409'); loadSummary(true); }
      else apiErrorToast('Starting a run', e);
    } finally { btn.disabled = false; renderRunbar(); }
  }
  async function runAsProfile(pid) {
    const btn = $('#btn-run');
    const p = (S.profiles || []).find(x => x.id === pid) || { name: pid };
    btn.disabled = true;
    try {
      const d = await api('/api/run', { method: 'POST', body: { trigger: 'button', profile: pid }, label: 'Starting a run', quiet: true });
      if (d && d.run_id != null) { S.runId = num(d.run_id); resetLanes('queued'); addDivider('Run #' + d.run_id + ' — applying as ' + p.name); }
      S.running = true; S.runStatus = 'running'; S.runStartedAt = new Date().toISOString();
      toast('Applying as ' + p.name + ': LinkedIn Easy Apply, filled and submitted by itself.', 'ok', 'runprof');
      renderTop(); renderLanes();
      setTimeout(() => loadSummary(true), 800);
    } catch (e) {
      if (e.status === 409) { toast('A run is already in progress.', 'info', 'run409'); loadSummary(true); }
      else apiErrorToast('Starting a run', e);
    } finally { btn.disabled = false; renderRunbar(); }
  }
  async function stopRun() {
    if (!S.running || S.stopping) return;
    S.stopping = true; renderTop();
    try {
      await api('/api/stop', { method: 'POST', body: {}, label: 'Stopping the run', quiet: true });
      toast('Stopping — the current step finishes, then the engine shuts down.', 'info');
      setTimeout(() => loadSummary(true), 1500);
    } catch (e) { S.stopping = false; renderTop(); apiErrorToast('Stopping the run', e); }
  }

  /* ═════════════════════════ Wiring ═════════════════════════ */

  function isTyping(t) {
    if (!t) return false;
    const tag = (t.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || t.isContentEditable;
  }
  function bind() {
    $('#btn-run').addEventListener('click', runNow);
    $('#rb-all').addEventListener('click', () => setAllLanes(true));
    $('#apps-body').addEventListener('focusout', e => {
      const body = $('#apps-body');
      if (e.relatedTarget && body.contains(e.relatedTarget)) return;   // moving to Save / a chip: let the click land
      setTimeout(() => {
        if (qBusy()) return;
        if (S.qStale) { S.qStale = false; loadQuestions(true); }
        else if (S.qDirty) renderAppsBody();
      }, 0);
    });
    $('#rb-none').addEventListener('click', () => setAllLanes(false));
    $('#btn-stop').addEventListener('click', stopRun);
    $('#btn-theme').addEventListener('click', () => setTheme(currentTheme() === 'dark' ? 'light' : 'dark'));
    $('#btn-settings').addEventListener('click', () => openSettings());
    $('#drawer-close').addEventListener('click', closeDrawer);
    $('#scrim').addEventListener('click', closeDrawer);
    $('#modal-close').addEventListener('click', closeSettings);
    $('#settings-cancel').addEventListener('click', closeSettings);
    $('#modal').addEventListener('mousedown', e => { if (e.target.id === 'modal') closeSettings(); });

    const q = $('#q');
    const onQ = debounce(() => { S.q = q.value.trim(); S.shown = PAGE; renderTabs(); renderAppsBody(); }, 160);
    q.addEventListener('input', onQ);
    q.addEventListener('keydown', e => { if (e.key === 'Escape') { if (q.value) { q.value = ''; onQ(); } else q.blur(); e.stopPropagation(); } });
    $('#source').addEventListener('change', e => { S.source = e.target.value; S.shown = PAGE; renderTabs(); renderAppsBody(); });

    const feed = $('#feed');
    feed.addEventListener('mouseenter', () => { S.feedHover = true; });
    feed.addEventListener('mouseleave', () => { S.feedHover = false; if (S.feedPending) scrollFeedBottom(true); });
    let lastTop = 0;
    feed.addEventListener('scroll', () => {
      const atBottom = feedAtBottom();
      if (atBottom) { S.feedStick = true; S.feedPending = 0; $('#feed-resume').hidden = true; }
      else if (feed.scrollTop < lastTop - 2) S.feedStick = false; // user scrolled up
      lastTop = feed.scrollTop;
    });
    $('#feed-resume').addEventListener('click', () => scrollFeedBottom(true));
    $('#feed-debug').addEventListener('change', e => { S.feedDebug = e.target.checked; applyFeedFilter(); });

    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        if (closeSettings() || closeDrawer()) { e.preventDefault(); }
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target) || !$('#modal').hidden) return;
      if (e.key === '/') {
        e.preventDefault();
        const qq = S.view === 'jobs' ? $('#q') : $('#page-' + S.view + ' .ag-search');
        if (qq) { qq.focus(); qq.select(); qq.scrollIntoView({ block: 'nearest' }); }
      }
      else if ((e.key === 'r' || e.key === 'R') && S.view === 'jobs') { if (!S.running) { e.preventDefault(); runNow(); } }
    });

    if (typeof ResizeObserver !== 'undefined') {
      let w = 0;
      new ResizeObserver(entries => { const nw = Math.round(entries[0].contentRect.width); if (nw !== w) { w = nw; renderChart(); } }).observe($('#chart'));
    } else window.addEventListener('resize', debounce(renderChart, 150));

    setInterval(tickTimers, 1000);
    setInterval(() => { $$('[data-rel]').forEach(el => { el.textContent = rel(el.dataset.rel, el.dataset.relmode); }); }, 30000);
    window.addEventListener('error', e => { console.error('[jobhunter]', e.error || e.message); });
    window.addEventListener('unhandledrejection', e => { console.error('[jobhunter]', e.reason); });
  }

  async function init() {
    loadRunSel();
    bind();
    setTheme(currentTheme());
    renderTop(); renderKpis(); renderLanes(); renderChips(); renderTabs(); renderAppsBody(); renderChart(); renderFunnel(); renderReplies();
    ensureFeedEmpty();
    loadSettings();
    loadEnv();
    loadProfiles();
    const vp = $('#view-profile'); if (vp) vp.addEventListener('change', () => switchView(vp.value));
    const sum = await loadSummary(false);
    const runId = sum && sum.last_run && sum.last_run.id;
    if (runId != null) await loadBacklog(runId, false);
    connectSSE();
    loadApps(); loadSkipped(true); loadPortal(true); loadQuestions(true); loadCharts(); loadWatch(true);
    document.querySelectorAll('.vs-btn').forEach(b => b.addEventListener('click', () => setView(b.dataset.view, true)));
    const viewOf = () => ({ '#agencies': 'agencies', '#board': 'board' })[location.hash] || 'jobs';
    window.addEventListener('hashchange', () => setView(viewOf()));
    setView(viewOf());
    if (S.view !== 'board') loadBoard(true);      // the "Job board" button shows how many open jobs wait
    checkBuild();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();

  // Exposed for debugging and the UI tests.
  window.JH = { state: S, handleEvent: ev => handleEvent(ev, true), openSettings: openSettings, setTheme: setTheme };
})();
