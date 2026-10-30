// 화면 부품 도우미 (요소 만들기, 알림, 날짜 표시)

// el('div.card#id', {onclick, title}, 자식...) — 글자는 textContent 로만 넣어 안전하게
export function el(spec, attrs, ...children) {
    const m = spec.match(/^([a-z0-9]+)?((?:[.#][^.#]+)*)$/i);
    const node = document.createElement(m[1] || 'div');
    for (const part of (m[2] || '').match(/[.#][^.#]+/g) || []) {
        if (part[0] === '.') node.classList.add(part.slice(1));
        else node.id = part.slice(1);
    }
    if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
        children.unshift(attrs);
        attrs = null;
    }
    for (const [k, v] of Object.entries(attrs || {})) {
        if (v == null || v === false) continue;
        if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
        else if (k === 'dataset') Object.assign(node.dataset, v);
        else if (k in node && k !== 'list' && k !== 'form') node[k] = v;
        else node.setAttribute(k, v === true ? '' : v);
    }
    append(node, children);
    return node;
}

function append(node, children) {
    for (const c of children) {
        if (c == null || c === false) continue;
        if (Array.isArray(c)) append(node, c);
        else node.append(c instanceof Node ? c : String(c));
    }
}

export function clear(node, ...children) {
    node.textContent = '';
    append(node, children);
    return node;
}

export function toast(message, type = 'info', ms = 3500) {
    const t = el('div.toast', message);
    if (type === 'error') t.classList.add('error');
    document.getElementById('toasts').append(t);
    setTimeout(() => t.remove(), ms);
}

// 오류를 알림으로 보여 주는 감싸기
export function guarded(fn) {
    return async (...args) => {
        try {
            return await fn(...args);
        } catch (e) {
            toast(e.message || String(e), 'error', 6000);
            return undefined;
        }
    };
}

export const WD = '일월화수목금토';

export function pad(n) {
    return String(n).padStart(2, '0');
}

export function isoDate(d) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function parseIso(s) {
    const [y, m, d] = s.split('-').map(Number);
    return new Date(y, m - 1, d);
}

export function todayIso() {
    return isoDate(new Date());
}

export function addDays(iso, n) {
    const d = parseIso(iso);
    d.setDate(d.getDate() + n);
    return isoDate(d);
}

// ---- 월별 목록 (조퇴·외출, 결석계 왼쪽 목록) ----
export function monthKey(iso) {
    return iso.slice(0, 7);
}

// 'YYYY-MM' 을 열 때 기준 날짜: 이번 달이면 오늘, 다른 달이면 그 달 1일 (새 증·결석계의 기본 날짜로도 씀)
export function dateForMonth(key) {
    const today = todayIso();
    return monthKey(today) === key ? today : `${key}-01`;
}

export function shiftMonth(iso, n) {
    const [y, m] = monthKey(iso).split('-').map(Number);
    const d = new Date(y, m - 1 + n, 1);
    return dateForMonth(`${d.getFullYear()}-${pad(d.getMonth() + 1)}`);
}

// ◀ [2026-10] ▶ [이번 달]  — onPick(기준 날짜)
export function monthNav(iso, onPick) {
    const input = el('input', { type: 'month', value: monthKey(iso), 'aria-label': '월 고르기',
        onchange: e => onPick(dateForMonth(e.target.value || monthKey(todayIso()))) });
    return el('div.filters',
        el('button.icon-btn', { title: '전달', onclick: () => onPick(shiftMonth(iso, -1)) }, '◀'),
        input,
        el('button.icon-btn', { title: '다음달', onclick: () => onPick(shiftMonth(iso, 1)) }, '▶'),
        monthKey(iso) !== monthKey(todayIso()) && el('button.chip', { onclick: () => onPick(todayIso()) }, '이번 달'));
}

// 날짜 묶음 제목 줄: '10/30(금) · 2건'
export function dayHead(label, count) {
    return el('li.day-head', el('span', label), el('span.cnt', `${count}건`));
}

// '11/3(화)' + 오늘/내일/모레
export function dayLabel(iso, withRel = true) {
    const d = parseIso(iso);
    const base = `${d.getMonth() + 1}/${d.getDate()}(${WD[d.getDay()]})`;
    if (!withRel) return base;
    const diff = Math.round((d - parseIso(todayIso())) / 86400000);
    const rel = { '-1': '어제', 0: '오늘', 1: '내일', 2: '모레' }[diff];
    return rel ? `${base} ${rel}` : base;
}

export function longDate(iso) {
    const d = parseIso(iso);
    return `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일 (${WD[d.getDay()]})`;
}

export function nowHm() {
    const d = new Date();
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export const SOURCE_NAMES = { cool: '쿨메신저', cool_sent: '쿨메신저(보냄)', edufine: '에듀파인', paste: '직접 입력', school: '학사일정' };
export const CATEGORIES = ['제출·마감', '회의', '연수', '행사', '수업·평가', '학사', '경조사', '참고'];
export const CAT_VARS = {
    '제출·마감': '--c-deadline', '회의': '--c-meeting', '연수': '--c-training',
    '행사': '--c-event', '수업·평가': '--c-class', '학사': '--c-school', '경조사': '--c-family', '참고': '--c-ref',
};

export function catBadge(cat) {
    return el('span.badge.cat', { className: `badge cat cat-${cat || '참고'}` }, cat || '참고');
}

export function srcBadge(src) {
    return el('span.badge', { className: `badge src-${src}` }, SOURCE_NAMES[src] || src);
}

// 일정 한 줄
export function eventLine(e, onOpen) {
    const when = e.end_date ? `${dayLabel(e.date, false)}~${dayLabel(e.end_date, false)}` : dayLabel(e.date)
        + (e.time ? ` ${e.time}` : '');
    const title = e.item_title || e.text;
    return el('div', { className: `ev ${e.kind}${e.done ? ' done' : ''}` },
        el('span.when', when),
        catBadge(e.category),
        onOpen ? el('a', { onclick: () => onOpen(e), title: e.text }, title) : el('span', { title: e.text }, title));
}

// 'D-3'·'오늘'·'지남' (날짜 칸 옆 작은 표시)
export function ddayInfo(iso) {
    const n = Math.round((parseIso(iso) - parseIso(todayIso())) / 86400000);
    if (n < 0) return { label: '지남', cls: 'past', n };
    if (n === 0) return { label: '오늘', cls: 'soon', n };
    if (n === 1) return { label: '내일', cls: 'soon', n };
    return { label: `D-${n}`, cls: n <= 3 ? 'soon' : '', n };
}

// 글 속 날짜·시각을 굵게 (눈에 띄게). 글자는 textContent 로만 넣음
const P_DATE_MARK = /(20\d{2}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*[.일]?\s*(?:\(\s*[월화수목금토일]\s*\))?|\d{1,2}\s*월\s*\d{1,2}\s*일\s*(?:\(\s*[월화수목금토일]\s*\))?|(?<![\d.])\d{1,2}\s*[./]\s*\d{1,2}\s*\.?\s*\(\s*[월화수목금토일]\s*\)|(?<![\d:])\d{1,2}:\d{2}(?![\d:]))(\s*까지)?/g;
export function markDates(text) {
    const out = [];
    let last = 0;
    for (const m of text.matchAll(P_DATE_MARK)) {
        if (m.index > last) out.push(text.slice(last, m.index));
        out.push(el('span.dt', m[0]));
        last = m.index + m[0].length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
}

// 팝업 창: modal({ title, body, actions }) → { close, box }. Esc·바깥을 누르면 닫힘
export function modal({ title, body, actions = [], wide = false, onClose }) {
    const prev = document.activeElement;
    const close = () => {
        back.remove();
        document.removeEventListener('keydown', onKey);
        onClose?.();
        prev?.focus?.();
    };
    const onKey = e => { if (e.key === 'Escape') close(); };
    const box = el('div.modal', { role: 'dialog', ariaModal: 'true', tabIndex: -1, className: `modal${wide ? ' wide' : ''}` },
        el('header.modal-head', el('h2', title), el('div.modal-actions', actions,
            el('button.icon-btn', { title: '닫기 (Esc)', onclick: () => close() }, '✕'))),
        el('div.modal-body', body));
    const back = el('div.modal-back', { onclick: e => { if (e.target === back) close(); } }, box);
    document.body.append(back);
    document.addEventListener('keydown', onKey);
    box.focus();
    return { close, box };
}

export function debounce(fn, ms = 300) {
    let t;
    return (...a) => {
        clearTimeout(t);
        t = setTimeout(() => fn(...a), ms);
    };
}

export function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = el('a', { href: url, download: filename });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
