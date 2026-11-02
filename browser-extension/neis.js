// 나이스 입력 칸 채우기 (시험 단계): 입력 칸 구조 보내기(값 없음) · 업무 데스크의 채우기 요청 받아 채우기
// 저장·제출 단추는 절대 누르지 않습니다. 채운 칸은 노랗게 표시하고, 저장은 선생님이 확인하고 직접 누릅니다.
import { loadSettings, checkBase } from './capture.js';

export const NEIS_URL = 'https://*.neis.go.kr/*';
export const isNeis = host => /(^|\.)neis\.go\.kr$/i.test(host || '');

// ---- 각 프레임에서 실행: 입력 칸 구조 (값은 읽지 않음) ----
export function scanFields() {
    const esc = s => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/[^\w-]/g, '\\$&'));
    const uniq = sel => { try { return document.querySelectorAll(sel).length === 1; } catch { return false; } };
    const pathOf = el => {
        const parts = [];
        for (let n = el; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
            if (n.id && uniq('#' + esc(n.id))) { parts.unshift('#' + esc(n.id)); break; }
            let i = 1;
            for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) i++;
            parts.unshift(`${n.tagName.toLowerCase()}:nth-of-type(${i})`);
        }
        return parts.join(' > ');
    };
    const keyOf = el => {
        if (el.id && uniq('#' + esc(el.id))) return '#' + esc(el.id);
        if (el.name) {
            const s = `${el.tagName.toLowerCase()}[name="${String(el.name).replace(/"/g, '\\"')}"]`;
            if (uniq(s)) return s;
        }
        return pathOf(el);
    };
    const txt = n => (n ? (n.innerText || n.textContent || '') : '').replace(/\s+/g, ' ').trim();
    const short = t => (t && t.length <= 40 ? t : '');
    const labelOf = el => {
        if (el.id) { const l = document.querySelector(`label[for="${esc(el.id)}"]`); if (txt(l)) return txt(l); }
        const wrap = el.closest('label');
        if (wrap && txt(wrap)) return txt(wrap);
        const own = el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder');
        if (own && own.trim()) return own.trim();
        const by = el.getAttribute('aria-labelledby');
        if (by) { const t = by.split(/\s+/).map(id => txt(document.getElementById(id))).join(' ').trim(); if (t) return t; }
        const cell = el.closest('td');
        if (cell) {
            for (let p = cell.previousElementSibling; p; p = p.previousElementSibling) if (short(txt(p))) return txt(p);
            const th = cell.parentElement && cell.parentElement.querySelector('th');
            if (short(txt(th))) return txt(th);
        }
        let p = el.previousElementSibling;
        for (let i = 0; p && i < 3; i++, p = p.previousElementSibling) if (short(txt(p))) return txt(p);
        return short(txt(el.parentElement)) || el.name || el.id || '';
    };
    const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
    const fields = [];
    for (const el of document.querySelectorAll('input, textarea, select, [contenteditable="true"]')) {
        const tag = el.tagName.toLowerCase();
        let type = tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea'
            : tag !== 'input' && el.isContentEditable ? 'editable' : String(el.type || 'text').toLowerCase();
        if (['hidden', 'password', 'submit', 'button', 'image', 'reset', 'file'].includes(type)) continue;
        if (el.disabled || !visible(el)) continue;
        if (['search', 'tel', 'email'].includes(type)) type = 'text';
        if (!['text', 'textarea', 'select', 'checkbox', 'radio', 'date', 'number', 'editable'].includes(type)) type = 'other';
        fields.push({ key: keyOf(el), label: labelOf(el).slice(0, 80), type,
            options: type === 'select' ? [...el.options].map(o => o.text.trim()).filter(Boolean).slice(0, 100) : [] });
        if (fields.length >= 300) break;
    }
    return { top: window === window.top, host: location.host, path: location.pathname, title: document.title || '', fields };
}

// ---- 한 프레임에서 실행: 칸 채우기 (저장 단추는 누르지 않음) ----
// 저장된 이름표와 지금 화면의 이름표가 다르면 그 칸은 건드리지 않음 (엉뚱한 칸을 채우지 않게)
export function fillFields(entries) {
    const res = { filled: 0, missing: [] };
    const setValue = (el, v) => {   // 화면 프로그램(프레임워크)이 바뀐 값을 알아채게 원래 값 넣는 법으로
        const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
            : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        const d = Object.getOwnPropertyDescriptor(proto, 'value');
        if (d && d.set) d.set.call(el, v); else el.value = v;
    };
    const fire = el => ['keydown', 'keyup', 'input', 'change', 'blur'].forEach(t => el.dispatchEvent(new Event(t, { bubbles: true })));
    const sameLabel = (el, want) => {   // 저장한 이름표가 지금 칸 주변에 있는지 (이름표가 이름·번호뿐이었으면 그 값으로 비교)
        if (!want || el.name === want || el.id === want) return true;
        const txt = n => (n ? (n.innerText || n.textContent || '') : '');
        const near = [el.closest('label'), el.closest('td'), el.closest('tr'), el.parentElement, el.previousElementSibling,
            el.id ? document.querySelector(`label[for="${el.id}"]`) : null].map(txt).join(' ').replace(/\s+/g, ' ');
        return near.includes(String(want).replace(/\s+/g, ' ').slice(0, 15));
    };
    for (const e of entries) {
        let el = null;
        try { el = document.querySelector(e.key); } catch { el = null; }
        if (!el) { res.missing.push(e.label); continue; }
        if (!sameLabel(el, e.label)) { res.missing.push(`${e.label} (이름표가 달라 건너뜀)`); continue; }
        if (e.type === 'checkbox' || e.type === 'radio') {
            if (el.checked !== !!e.value) el.click();
        } else if (e.type === 'select') {
            const want = String(e.value).trim();
            const opt = [...el.options].find(o => o.text.trim() === want || o.value === want);
            if (!opt) { res.missing.push(`${e.label} (선택지에 '${want}' 없음)`); continue; }
            el.focus();
            setValue(el, opt.value);
            fire(el);
        } else if (e.type === 'editable') {
            el.focus();
            el.textContent = String(e.value);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
        } else {
            el.focus();
            setValue(el, String(e.value));
            fire(el);
        }
        el.style.outline = '3px solid #f5c400';
        el.style.backgroundColor = '#fff8cc';
        res.filled++;
    }
    return res;
}

async function desk(path, opts = {}) {
    const s = await loadSettings();
    if (!s.key) throw new Error('연결 키가 없습니다. 확장 프로그램 옵션에서 키를 넣으세요');
    let r;
    try {
        r = await fetch(checkBase(s.base) + path, { ...opts, headers: { 'Content-Type': 'application/json', 'X-Desk-Capture-Key': s.key } });
    } catch {
        throw new Error('업무 데스크에 연결하지 못했습니다 (켜져 있는지, 옵션의 주소가 맞는지 확인)');
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `업무 데스크가 거절했습니다 (${r.status})`);
    return data;
}

// 팝업 [입력 칸 구조 보내기]: 입력 칸이 있는 프레임마다 양식으로 저장
export async function sendForms(tabId) {
    const res = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: scanFields });
    const frames = res.map(r => r.result).filter(f => f && isNeis(f.host) && f.fields.length);
    if (!frames.length) throw new Error('이 화면에서 입력 칸을 찾지 못했습니다 (나이스 화면에서 눌러 주세요)');
    const top = res.map(r => r.result).find(f => f && f.top) || {};
    let fields = 0;
    for (const f of frames) {
        const title = [top.title, f.top ? '' : f.path.split('/').pop()].filter(Boolean).join(' · ');
        const r = await desk('/capture/form', { method: 'POST', body: JSON.stringify({ host: f.host, frame_path: f.path, title, fields: f.fields }) });
        fields += r.fields;
    }
    return { forms: frames.length, fields };
}

// 업무 데스크의 채우기 요청을 채움. 가져가기 전에 확인하므로 나이스 탭이 앞에 없으면 아무것도 바꾸지 않음.
// auto: 30초마다 자동 확인 (나이스 탭이 앞에 있을 때만 채움, 탭·창을 바꾸지 않음)
// 수동: Alt+Shift+F 나 팝업 단추 (나이스 탭이 앞에 없으면 안내만)
export async function fillNow({ auto = false, toast = null } = {}) {
    const { job } = await desk('/capture/fill?peek=1');
    if (!job) return null;
    if (!isNeis(job.host)) throw new Error('나이스 화면만 채웁니다');
    const tabs = await chrome.tabs.query({ url: `https://${job.host}/*` });
    let target = null;
    for (const tab of tabs) {
        const fr = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: () => location.pathname })
            .catch(() => []);
        const hit = fr.find(r => r.result === job.frame_path);
        if (hit) { target = { tab, frameId: hit.frameId }; break; }
    }
    const front = target && target.tab.active && (await chrome.windows.get(target.tab.windowId)).focused;
    if (!front) {   // 가져가지 않음: 요청은 그대로 기다림
        if (!auto && toast) {
            const here = (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
            if (here) await toast(here.id, target
                ? `「${job.title}」 나이스 화면을 앞에 두고 다시 누르세요`
                : `나이스에서 「${job.title}」 화면을 연 뒤 다시 누르세요`, false);
        }
        return { waiting: true };
    }
    const { job: taken } = await desk('/capture/fill?wait=0');   // 이제 가져감
    if (!taken) return null;
    const report = result => desk('/capture/fill/result', { method: 'POST', body: JSON.stringify({ id: taken.id, ...result }) });
    try {
        // 채우기 전에 화면 위에서 반드시 물어봄 (칸을 모두 보여 줌)
        const lines = taken.entries.map(e => `· ${e.label}: ${typeof e.value === 'boolean' ? (e.value ? '선택' : '해제') : String(e.value).slice(0, 40)}`);
        const ok = await chrome.scripting.executeScript({ target: { tabId: target.tab.id },
            args: [`업무 데스크에서 「${taken.title}」 ${taken.entries.length}칸을 채울까요?\n저장 단추는 누르지 않습니다. 채운 뒤 내용을 확인하고 직접 저장하세요.\n\n${lines.join('\n')}`],
            func: msg => window.confirm(msg) }).then(r => r[0]?.result === true);
        if (!ok) {
            await report({ filled: 0, missing: [], error: '선생님이 취소함' });
            return { cancelled: true };
        }
        const [r] = await chrome.scripting.executeScript({ target: { tabId: target.tab.id, frameIds: [target.frameId] }, func: fillFields, args: [taken.entries] });
        const result = r?.result || { filled: 0, missing: taken.entries.map(e => e.label) };
        await report(result);
        if (toast) await toast(target.tab.id, `${result.filled}칸을 채웠습니다${result.missing.length ? ` · 못 채운 칸 ${result.missing.length}` : ''}. 내용을 확인하고 직접 저장하세요`, result.missing.length === 0);
        return result;
    } catch (e) {
        await report({ filled: 0, missing: [], error: e.message }).catch(() => {});
        throw e;
    }
}
