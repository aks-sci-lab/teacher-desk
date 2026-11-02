// 화면 글자 읽기·업무 데스크로 보내기 (단축키·팝업·옵션이 같이 씀)
// 읽은 글은 http://127.0.0.1(이 PC)의 업무 데스크로만 보냅니다. 다른 곳으로는 보내지 않습니다.

// 어느 화면에서나 빼는 줄 (에듀파인 바깥 틀의 안내문 등). 옵션에서 더할 수 있음
export const DEFAULT_EXCLUDE = ['접근성모드로 전환을 원하시면'];

// mode: auto(보고 있는 목록 자동) · pick(한 번 고른 칸) · selection(고른 글만) · all(전체)
export const DEFAULTS = {
    base: 'http://127.0.0.1:8770', key: '',
    mode: 'auto', skipTop: true, inputs: false, preview: false,
    exclude: DEFAULT_EXCLUDE.join('\n'),
    picked: {},   // { 사이트: 고른 칸의 주소 경로 }
};
export const MODE_NAMES = {
    auto: '보고 있는 목록 자동', pick: '한 번 고른 칸', selection: '마우스로 고른 글만', all: '화면 전체',
};

export async function loadSettings() {
    const got = await chrome.storage.local.get(DEFAULTS);
    return { ...DEFAULTS, ...got };
}

// 업무 데스크 주소는 이 PC 만 허용
export function checkBase(base) {
    let u;
    try { u = new URL(base); } catch { throw new Error('업무 데스크 주소가 잘못되었습니다'); }
    if (u.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(u.hostname)) {
        throw new Error('업무 데스크 주소는 http://127.0.0.1:포트 형태여야 합니다 (이 PC 로만 보냅니다)');
    }
    return u.origin;
}

// ---- 각 프레임에서 실행되는 함수 (페이지 안에서 돈다: 바깥 변수를 쓰지 말 것) ----
function readFrame() {
    const sel = String(window.getSelection ? window.getSelection() : '').trim();
    const body = document.body ? document.body.innerText || '' : '';
    // 입력 칸(제목·내용 입력란)은 innerText 에 안 들어가므로 따로 모음 (옵션에서 켤 때만 보냄)
    const fields = [...document.querySelectorAll('textarea, input[type="text"]')]
        .filter(e => e.value && e.value.trim().length > 1 && e.offsetParent !== null)
        .map(e => e.value.trim());
    return {
        top: window === window.top,
        url: location.href,
        host: location.host,
        path: location.pathname,
        title: document.title || '',
        selection: sel,
        text: body.trim(),
        fields,
        focused: document.hasFocus(),                       // 마지막으로 누른 칸
        visible: window.innerWidth > 0 && window.innerHeight > 0,   // 숨은 칸(display:none)은 0
    };
}

export async function readTab(tabId) {
    const res = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: readFrame });
    return res.map(r => r.result).filter(Boolean);
}

export function excludeList(s) {
    return String(s.exclude ?? '').split('\n').map(x => x.trim()).filter(Boolean);
}

// 빼기 목록의 문구가 든 줄을 지우고, 빈 줄 묶음을 줄임
export function filterLines(text, exclude) {
    const out = [];
    for (const line of text.split('\n')) {
        if (exclude.some(w => line.includes(w))) continue;
        if (!line.trim() && (!out.length || !out[out.length - 1].trim())) continue;
        out.push(line);
    }
    return out.join('\n').trim();
}

// 어느 칸(프레임)을 보낼지 고름. pickPath 를 주면 그 칸만 (팝업의 [이 칸만 보내기])
export function chooseFrames(frames, s, pickPath = null) {
    const top = frames.find(f => f.top) || frames[0] || {};
    const shown = frames.filter(f => f.visible !== false);
    const pool = (shown.length ? shown : frames).filter(f => f.text || (s.inputs && f.fields?.length));
    const want = pickPath || (s.mode === 'pick' ? s.picked?.[top.host] : null);
    if (pickPath || s.mode === 'pick') {
        const f = pool.find(x => x.path === want);
        if (!f) throw new Error(want
            ? '고른 칸이 이 화면에 없습니다. 확장 프로그램 아이콘을 눌러 보낼 칸을 다시 고르세요'
            : '보낼 칸을 아직 고르지 않았습니다. 확장 프로그램 아이콘을 눌러 [이 칸만 보내기]로 한 번 고르세요');
        return [f];
    }
    const inner = pool.filter(f => !f.top);
    if (s.mode === 'all') {
        const list = s.skipTop && inner.length ? inner : pool;
        return [...list].sort((a, b) => Number(b.top) - Number(a.top));
    }
    // auto: 바깥 틀(맨 위)은 빼고, 마지막으로 누른 칸 → 없으면 글이 가장 많은 칸 하나
    if (!inner.length) return top.text ? [top] : [];
    return [inner.find(f => f.focused) || inner.reduce((a, b) => (b.text.length > a.text.length ? b : a))];
}

// 보낼 글 만들기: 고른 글이 있으면 그것만, 없으면 방식에 맞는 칸의 글
export function combine(frames, s = DEFAULTS, pickPath = null) {
    const top = frames.find(f => f.top) || frames[0] || {};
    const exclude = excludeList(s);
    const picked = frames.find(f => f.selection && f.selection.length > 1);
    if (picked && !pickPath) {
        return { text: filterLines(picked.selection, exclude), title: top.title || '', url: top.url || '',
            frame_url: picked.url, selected: true, paths: [picked.path] };
    }
    if (s.mode === 'selection' && !pickPath) throw new Error('먼저 보낼 글을 마우스로 끌어서 고르세요 (옵션: 마우스로 고른 글만)');
    const chosen = chooseFrames(frames, s, pickPath);
    const parts = [], seen = new Set();
    for (const f of chosen) {
        for (const chunk of [f.text, ...(s.inputs ? f.fields || [] : [])]) {
            const key = (chunk || '').replace(/\s+/g, ' ').trim();
            if (key.length < 2 || seen.has(key)) continue;
            seen.add(key);
            parts.push(chunk.trim());
        }
    }
    return { text: filterLines(parts.join('\n\n'), exclude), title: top.title || '', url: top.url || '',
        frame_url: chosen[0]?.url || '', selected: false, paths: chosen.map(f => f.path) };
}

export async function sendToDesk(payload) {
    const s = await loadSettings();
    if (!s.key) throw new Error('연결 키가 없습니다. 확장 프로그램 옵션에서 업무 데스크 설정의 키를 붙여 넣으세요');
    const base = checkBase(s.base);
    let r;
    try {
        r = await fetch(base + '/capture', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Desk-Capture-Key': s.key },
            body: JSON.stringify(payload),
        });
    } catch {
        throw new Error('업무 데스크에 연결하지 못했습니다. 업무 데스크가 켜져 있는지, 옵션의 주소(포트)가 맞는지 확인하세요');
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `업무 데스크가 거절했습니다 (${r.status})`);
    if (data.ok === false) throw new Error(data.error || '보내지 못했습니다');
    return data;
}

export async function ping() {
    const s = await loadSettings();
    const base = checkBase(s.base);
    let r;
    try {
        r = await fetch(base + '/capture/ping', { headers: { 'X-Desk-Capture-Key': s.key } });
    } catch {
        throw new Error('업무 데스크에 연결하지 못했습니다 (켜져 있는지, 주소·포트가 맞는지 확인)');
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `거절됨 (${r.status})`);
    return data;
}

// 탭의 글을 골라 만든 뒤(prepare) 보냄(send). confirm 을 주면 보내기 전에 물어봄 (미리 보기)
export async function prepareTab(tabId, pickPath = null) {
    const s = await loadSettings();
    const c = combine(await readTab(tabId), s, pickPath);
    if (!c.text) throw new Error('보낼 글이 없습니다 (이 화면은 글자를 읽을 수 없는 형태일 수 있습니다)');
    return { c, s };
}

export async function sendTab(tabId, { pickPath = null, confirm = null } = {}) {
    const { c, s } = await prepareTab(tabId, pickPath);
    if (s.preview && confirm && !(await confirm(c.text))) return { cancelled: true };
    const result = await sendToDesk({ text: c.text, title: c.title, url: c.url, frame_url: c.frame_url });
    return { ...result, selected: c.selected, chars: c.text.length };
}

// 팝업에서 [이 칸만 보내기]: 이 사이트에서는 다음부터 이 칸만 (방식을 '한 번 고른 칸'으로)
export async function rememberPick(host, path) {
    const s = await loadSettings();
    await chrome.storage.local.set({ mode: 'pick', picked: { ...s.picked, [host]: path } });
}
