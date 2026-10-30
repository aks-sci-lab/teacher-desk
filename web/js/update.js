// 화면에서 새 버전으로 업데이트 (프로그램을 따로 끄지 않아도 됨)
import { api } from './api.js';
import { el, toast, guarded } from './ui.js';
import { store } from './store.js';
import { emit } from './shell.js';

// 새 버전을 확인하고 결과 글을 돌려줌
export async function checkUpdate() {
    const st = await api.post('/api/update/check', {});
    store.state = { ...store.state, update: st };
    emit('state', store.state);   // 아래 막대의 ⬆ 를 바로 갱신
    return st;
}

// 확인창 → 업데이트 창을 띄우고 업무 데스크가 꺼졌다 켜질 때까지 안내 → 새로 고침
export const startUpdate = guarded(async () => {
    const st = store.state?.update;
    if (!st?.available) return toast('새 버전이 없습니다. 설정에서 [새 버전 확인] 을 눌러 보세요.');
    if (!window.confirm(`새 버전 ${st.latest} 으로 업데이트할까요? (지금 ${st.current})\n\n` +
        '업무 데스크가 1~2분 동안 꺼졌다가 저절로 다시 켜집니다. 그동안 수집·텔레그램·알림이 멈추고, ' +
        '학생 명단·기록·설정은 그대로 남습니다.')) return;
    let force = false;
    for (;;) {
        try {
            await api.post('/api/update/apply', { force });
            break;
        } catch (e) {
            if (!String(e.message).includes('중입니다') || force) throw e;
            if (!window.confirm(`${e.message}\n\n그래도 지금 업데이트할까요? (하던 일이 중단됩니다)`)) return;
            force = true;
        }
    }
    waitForRestart(st.latest);
});

function waitForRestart(version) {
    const msg = el('p', '업데이트 창이 열렸습니다. 새 버전을 받아 바꾸는 중입니다…');
    const box = el('div.update-overlay', el('div.update-box',
        el('h2', `${version} 으로 업데이트하는 중`), msg,
        el('p.small.muted', '이 화면은 업데이트가 끝나면 저절로 새로 고쳐집니다. 열린 검은 업데이트 창은 닫지 마세요.')));
    document.body.append(box);
    let wentDown = false;
    const began = Date.now();
    const timer = setInterval(async () => {
        try {
            const r = await fetch('/', { cache: 'no-store' });
            if (r.ok && wentDown) { clearInterval(timer); location.reload(); return; }
        } catch (e) {
            wentDown = true;
            msg.textContent = '업무 데스크가 다시 켜지기를 기다리는 중…';
        }
        if (Date.now() - began > 5 * 60 * 1000) {
            clearInterval(timer);
            msg.textContent = '5분이 지나도 다시 켜지지 않았습니다. 바탕 화면의 업무 데스크(start-desk.bat)를 직접 켜 주세요. 업데이트 기록: data\\update.log';
        }
    }, 2000);
}
