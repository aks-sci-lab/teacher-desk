// 화면 글자 읽기·업무 데스크로 보내기 (단축키·팝업이 같이 씀)
// 읽은 글은 http://127.0.0.1(이 PC)의 업무 데스크로만 보냅니다. 다른 곳으로는 보내지 않습니다.

export const DEFAULTS = { base: 'http://127.0.0.1:8770', key: '' };

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
    // 입력 칸(제목·내용 입력란)은 innerText 에 안 들어가므로 따로 모음
    const fields = [...document.querySelectorAll('textarea, input[type="text"]')]
        .filter(e => e.value && e.value.trim().length > 1 && e.offsetParent !== null)
        .map(e => e.value.trim());
    return {
        top: window === window.top,
        url: location.href,
        title: document.title || '',
        selection: sel,
        text: body.trim(),
        fields,
    };
}

export async function readTab(tabId) {
    const res = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: readFrame });
    return res.map(r => r.result).filter(Boolean);
}

// 프레임들의 글을 하나로: 고른 글이 있으면 그것만, 없으면 모든 프레임의 글 (위쪽 프레임이 먼저)
export function combine(frames) {
    const picked = frames.find(f => f.selection && f.selection.length > 1);
    const top = frames.find(f => f.top) || frames[0] || {};
    if (picked) return { text: picked.selection, title: top.title || '', url: top.url || '', selected: true };
    const parts = [], seen = new Set();
    for (const f of [...frames].sort((a, b) => Number(b.top) - Number(a.top))) {
        for (const chunk of [f.text, ...(f.fields || [])]) {
            const key = (chunk || '').replace(/\s+/g, ' ').trim();
            if (key.length < 2 || seen.has(key)) continue;
            seen.add(key);
            parts.push(chunk.trim());
        }
    }
    return { text: parts.join('\n\n'), title: top.title || '', url: top.url || '', selected: false };
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

export async function sendTab(tabId) {
    const frames = await readTab(tabId);
    const c = combine(frames);
    if (!c.text) throw new Error('읽을 글이 없습니다 (이 화면은 읽을 수 없는 형태일 수 있습니다)');
    const result = await sendToDesk({ text: c.text, title: c.title, url: c.url });
    return { ...result, selected: c.selected, chars: c.text.length };
}
