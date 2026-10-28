// 시작: 화면 틀 → 각 기능 등록 → 상태 불러오기 → 주기적으로 새로 고침
import { guarded, toast } from './ui.js';
import { showView, statusItem, setStatus, setBadge, applyLayout, setLayout, getLayout, pollLogs, toggleLayoutMode, orderViews, on, emit } from './shell.js';
import { loadState, loadStudents } from './store.js';
import { initPasses } from './views/passes.js';
import { initInbox, collectNow } from './views/inbox.js';
import { initCalendar } from './views/calendar.js';
import './views/today.js';
import './views/search.js';
import { openSettings } from './views/settings.js';

orderViews(['today', 'inbox', 'calendar', 'passes', 'search', 'settings']);

statusItem('conn', { title: '업무 데스크 프로그램 연결 상태' });
statusItem('collect', { title: '누르면 지금 수집', onclick: collectNow });
statusItem('telegram', { title: '텔레그램 설정', onclick: () => { showView('settings'); openSettings(); } });
statusItem('new', { title: '새로 받은 자료', onclick: () => showView('inbox') });
statusItem('log', { right: true, title: '수집·오류 기록 보기/숨기기', onclick: () => setLayout({ bottomVisible: !getLayout().bottomVisible }) });
setStatus('log', '기록');
statusItem('layout', { title: '화면 배치 바꾸기 (영역 번호 보기)', onclick: toggleLayoutMode });
setStatus('layout', '화면 배치');

function updateStatus(s) {
    setStatus('conn', '● 연결됨');
    const last = s.last_run ? s.last_run.finished_at.slice(11, 16) : '없음';
    const watch = s.watch && s.watch.enabled && s.watch.running ? '실시간 감시 · ' : '';
    setStatus('collect', `${watch}마지막 수집 ${last} · 다음 ${s.next_collect || '-'}`);
    setStatus('telegram', `텔레그램 ${s.telegram.linked ? '연결됨' : s.settings.telegram_token_set ? '연결 대기' : '꺼짐'}`, s.telegram.status);
    setStatus('new', `새 자료 ${s.counts.new_items}`);
    setBadge('inbox', s.counts.new_items);
}
on('state', updateStatus);

let lastRunId = null;
let lastPass = null;
const tick = async () => {
    try {
        const s = await loadState();
        // 예약 수집·텔레그램 수집이 끝났거나 텔레그램으로 증을 발급했으면 목록을 새로 고침
        const passChanged = lastPass !== null && s.counts.passes !== lastPass;
        if ((s.last_run && lastRunId !== null && s.last_run.id !== lastRunId) || passChanged) emit('data-changed');
        if (passChanged) emit('passes-changed');
        lastRunId = s.last_run ? s.last_run.id : 0;
        lastPass = s.counts.passes;
    } catch (e) {
        setStatus('conn', '○ 연결 끊김 — start-desk.bat 실행');
    }
    pollLogs();
};

guarded(async () => {
    const s = await loadState();
    const saved = s.settings.layout || {};
    // 0.5.13: 아래 기록 패널은 기본으로 숨김 (예전 설정도 한 번 숨김, 다시 켜면 그대로)
    if (saved.v !== 2) applyLayout({ ...saved, bottomVisible: false, v: 2 });
    else applyLayout(saved, false);
    lastRunId = s.last_run ? s.last_run.id : 0;
    await Promise.all([loadStudents(), initPasses(), initInbox(), initCalendar()]);
    showView('today');
    if (!s.settings.school_name && !s.settings.edufine_folder && !s.settings.coolmessenger_name && !s.settings.coolmessenger_path) {
        toast('처음 오셨네요. ⚙ 설정에서 학교·학급과 수집 폴더를 입력하세요.', 'info', 8000);
    }
    pollLogs();
    setInterval(tick, 15000);
})();

window.addEventListener('keydown', e => {
    // Ctrl+K: 검색
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        showView('search');
    }
});
