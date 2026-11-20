// 사용 기록 (0.6.15): 화면에서 연 메뉴·누른 단추·오류·느린 요청을 모아 서버의 사용 기록 파일(data/logs)로 보냄.
// 내용은 보내지 않음: 단추는 짧은 이름(24자까지)만, 탭은 번호를 # 로, 자료 제목·입력한 글은 보내지 않음.
// 학생 이름·전화번호는 서버(desk/applog.py)가 한 번 더 가림.
const TOKEN = document.querySelector('meta[name="desk-token"]')?.content || '';
const OFF = new URLSearchParams(location.search).has('silent-print');   // 자동 인쇄 창은 남기지 않음
const queue = [];
let view = '';
const FLUSH_MS = 15000;
const MAX_QUEUE = 100;
const LABEL_MAX = 24;

export function logUse(kind, what, extra = {}) {
    if (OFF) return;
    queue.push({ kind, what: String(what ?? '').replace(/\s+/g, ' ').trim().slice(0, 80), view, ...extra });
    if (queue.length > MAX_QUEUE) queue.splice(0, queue.length - MAX_QUEUE);
    if (kind === 'error') flush();
}

export function setUseView(id) {
    view = id || '';
    logUse('view', id);
}

export function flush(keepalive = false) {
    if (!queue.length || OFF) return;
    const events = queue.splice(0, queue.length);
    try {
        fetch('/api/log/client', {
            method: 'POST', keepalive,
            headers: { 'X-Desk-Token': TOKEN, 'Content-Type': 'application/json' },
            body: JSON.stringify({ events }),
        }).catch(() => {});
    } catch (e) { /* 꺼지는 중 */ }
}

function buttonLabel(b) {
    if (b.classList.contains('tab')) return '탭 바꾸기';   // 탭 제목은 자료 제목이라 남기지 않음
    if (b.closest('#tab-list')) return '탭 닫기';
    const text = (b.getAttribute('aria-label') || b.textContent || b.getAttribute('title') || '').replace(/\s+/g, ' ').trim();
    if (!text) return '(이름 없는 단추)';
    return text.length > LABEL_MAX ? '(긴 단추)' : text;
}

if (!OFF) {
    document.addEventListener('click', e => {
        const b = e.target.closest?.('button, input[type="checkbox"], [role="button"]');
        if (!b) return;
        const label = b.matches('input') ? `체크 ${b.dataset.key || ''}` : buttonLabel(b);
        logUse('click', label);
    }, { capture: true, passive: true });
    window.addEventListener('error', e => {
        const where = e.filename ? `${e.filename.split('/').pop()}:${e.lineno}` : '';
        logUse('error', e.message || '화면 오류', { where });
    });
    window.addEventListener('unhandledrejection', e => {
        const r = e.reason;
        logUse('error', `처리하지 못한 오류: ${r?.message || r || ''}`, { where: (r?.stack || '').split('\n')[1]?.trim().split('/').pop() || '' });
    });
    setInterval(() => flush(), FLUSH_MS);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush(true); });
    window.addEventListener('pagehide', () => flush(true));
}
