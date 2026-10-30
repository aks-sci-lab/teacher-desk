// 업무 데스크 화면은 하나만: 나중에 연 창이 '쓰는 창'이 되고, 먼저 열린 창에는 안내가 덮여 저장·수집을 막음
import { api, CLIENT_ID, sendBye } from './api.js';
import { el } from './ui.js';

let overlay = null;

function hide() {
    if (overlay) { overlay.remove(); overlay = null; }
}

function show() {
    if (overlay) return;
    overlay = el('div.single-overlay', { role: 'alertdialog' },
        el('div.single-box',
            el('h2', '업무 데스크가 다른 창에서 열렸습니다'),
            el('p', '한 번에 한 창에서만 쓸 수 있습니다. 이 창은 닫아도 됩니다.'),
            el('div.row',
                el('button.btn.primary', { onclick: () => claim().then(() => window.dispatchEvent(new Event('desk-reclaimed'))) }, '이 창에서 쓰기'),
                el('button.btn', { onclick: () => { sendBye(); window.close(); } }, '이 창 닫기')),
            el('p.small.muted', '(닫기 단추로 안 닫히면 창의 X 를 눌러 주세요)')));
    document.body.append(overlay);
}

export async function claim() {
    if (!CLIENT_ID) return;
    await api.post('/api/ui/claim', { id: CLIENT_ID });
    hide();
}

// 서버가 알려 준 '쓰는 화면'을 보고 이 창이 써도 되는지 정함. 써도 되면 true
export function stillActive(ui) {
    if (!CLIENT_ID || !ui) return true;
    if (ui.active === null) {   // 서버가 다시 켜졌거나, 쓰던 창이 닫힘 → 이 창이 이어받음
        claim().catch(() => {});
        return true;
    }
    if (ui.active !== CLIENT_ID) { show(); return false; }
    hide();
    return true;
}

// 창이 닫히면 서버에 알리고(다음에 켤 때 새 창을 열도록), 뒤로 가기 캐시에서 돌아오면 다시 이어받음
export function watchWindow() {
    window.addEventListener('pagehide', sendBye);
    window.addEventListener('pageshow', e => { if (e.persisted) claim().catch(() => {}); });
}
