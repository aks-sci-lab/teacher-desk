// 📋 브리핑: 지난 마감 · 오늘 일정 · 7일 안 마감 · 오늘 조퇴/외출 · 새로 받은 자료 · 수집 상태
import { api } from '../api.js';
import { el, clear, guarded, longDate, todayIso, eventLine, catBadge, srcBadge } from '../ui.js';
import { registerView, openTab, findTab, on } from '../shell.js';
import { store } from '../store.js';
import { openItem, openPaste, collectNow } from './inbox.js';
import { openForm, openPass } from './passes.js';
import { openCalendar } from './calendar.js';

const KIND = { early: '조퇴', out: '외출' };

function openEv(e) {
    if (e.item_id) openItem(e.item_id);
    else openCalendar();
}

function card(title, count, body, opts = {}) {
    return el('section.card', { className: `card${opts.alert ? ' alert' : ''}` },
        el('h3', el('span', title), count != null && el('span.muted', `${count}건`)),
        body);
}

// 요약 글(마크다운 비슷함) → 화면: '## 제목' 은 굵게, [3] 은 3번 자료를 여는 링크
function aiText(text, ids = []) {
    return el('div.ai-text', text.split('\n').map(line => {
        const head = line.match(/^#{1,4}\s*(.+)/);
        if (head) return el('h4', head[1].replace(/\*\*/g, ''));
        const parts = line.replace(/\*\*/g, '').split(/(\[\d+\])/);
        return el('div', parts.map(p => {
            const m = p.match(/^\[(\d+)\]$/);
            const id = m && ids[Number(m[1]) - 1];
            return id ? el('a', { href: '#', onclick: e => { e.preventDefault(); openItem(id); } }, p) : p;
        }));
    }));
}

// 🤖 지난 1주일 AI 요약 (LM Studio): 저장된 요약을 보여 주고, 오늘 만든 것이 없으면 새로 만듦
function aiCard() {
    const body = el('div', el('p.muted.small', '불러오는 중…'));
    const show = (r, note) => {
        const res = r.result;
        clear(body,
            res && res.text ? aiText(res.text, res.ids) : el('p.muted.small', '아직 요약이 없습니다.'),
            res && res.at ? el('p.small.muted', `${res.at} · 자료 ${res.used ?? res.count}건${res.count > (res.used ?? res.count) ? ` (전체 ${res.count}건 중 최근 것)` : ''}${res.model ? ' · ' + res.model : ''}`) : null,
            note ? el('p.small', { style: 'color: var(--danger)' }, note) : null,
            el('div.row', el('button.btn.small', { onclick: () => run(true) }, '🤖 다시 요약')));
    };
    // 요약은 업무 데스크가 뒤에서 만듦 (몇 분 걸림). 화면은 5초마다 끝났는지만 확인
    let timer = null;
    const waiting = st => {
        const sec = Math.max(0, Math.round((Date.now() - new Date(st.started_at.replace(' ', 'T'))) / 1000));
        clear(body,
            el('p.small', `🤖 지난 1주일 자료를 요약하는 중… ${Math.floor(sec / 60)}분 ${sec % 60}초`),
            el('p.small.muted', 'LM Studio 가 글을 읽고 쓰는 동안 다른 화면을 써도 됩니다. 이 PC 에서 4~5분쯤 걸리고, 15분까지 기다립니다.'));
    };
    const poll = async () => {
        clearTimeout(timer);
        if (!body.isConnected) return;   // 브리핑을 닫았거나 새로 그림
        const st = await api.get('/api/ai/weekly');
        if (st.running) {
            waiting(st);
            timer = setTimeout(() => poll().catch(() => {}), 5000);
        } else {
            show(st, st.error);
        }
    };
    const run = async force => {
        const st = await api.get('/api/ai/weekly');
        if (!st.enabled) {
            clear(body, el('p.small', '⚙ 설정 → 🤖 AI 요약에서 LM Studio 를 연결하면 지난 1주일 쿨메신저·업무포털 내용을 요약해 드립니다.'));
            return;
        }
        if (!st.running && !force && st.result && st.result.at && st.result.at.slice(0, 10) === todayIso()) {
            show(st, st.error);
            return;
        }
        try {
            if (!st.running) await api.post('/api/ai/weekly');
            await poll();
        } catch (e) {
            show(st, e.message);
        }
    };
    setTimeout(() => run(false).catch(e => show({}, e.message)), 0);  // 카드가 화면에 붙은 뒤 시작
    return card('🤖 지난 1주일 요약 (쿨메신저·업무포털)', null, body);
}

function renderToday(pane) {
    pane.append(el('p.muted', '불러오는 중…'));
    guarded(async () => {
        const s = await api.get('/api/today');
        clear(pane);
        const st = store.state;
        const evList = (evs, empty) => evs.length ? el('div', evs.map(e => eventLine(e, openEv))) : el('p.muted.small', empty);
        pane.append(
            el('h1', `${longDate(todayIso())}`),
            el('p.sub', `${store.settings.teacher_name ? store.settings.teacher_name + ' 선생님, ' : ''}오늘 할 일을 모았습니다.`),
            el('div.row', { style: 'margin-bottom: 16px' },
                el('button.btn', { onclick: openPaste }, '＋ 메시지 붙여넣기'),
                el('button.btn', { onclick: () => openForm({ kind: 'early' }) }, '🎫 조퇴증'),
                el('button.btn', { onclick: () => openForm({ kind: 'out' }) }, '🎫 외출증'),
                el('button.btn', { onclick: collectNow }, '⟳ 지금 수집')),
            aiCard(),
            el('div.cards',
                s.overdue.length ? card('⚠ 지난 마감 (끝냄 표시 안 됨)', s.overdue.length, evList(s.overdue), { alert: true }) : null,
                card('📌 오늘 일정', s.today.length, evList(s.today, '오늘 일정이 없습니다')),
                card('⏰ 7일 안 마감', s.week_deadlines.length, evList(s.week_deadlines, '다가오는 마감이 없습니다')),
                card('🗓 7일 안 일정', s.week_events.length, evList(s.week_events, '다가오는 일정이 없습니다')),
                card('🎫 오늘 조퇴·외출', s.passes.length, s.passes.length
                    ? el('ul.list', s.passes.map(p => el('li', { onclick: () => openPass(p) },
                        el('span.ttl', `${p.student_number != null ? p.student_number + '번 ' : ''}${p.student_name}`),
                        el('span.meta', KIND[p.kind], p.time_out || '', p.time_return ? `~${p.time_return}` : '',
                            p.kind === 'out' ? (p.status === 'returned' ? '✔ 복귀' : '복귀 전') : ''))))
                    : el('p.muted.small', '없습니다')),
                card('📥 새로 받은 자료', s.new_items.length, s.new_items.length
                    ? el('ul.list', s.new_items.map(it => el('li', { onclick: () => openItem(it.id) },
                        el('span.ttl', it.title), el('span.meta', srcBadge(it.source), catBadge(it.category), it.received_at.slice(5, 16)))))
                    : el('p.muted.small', '새 자료가 없습니다')),
                card('🔄 자동 수집', null, el('div.small',
                    el('div', `수집 시각: ${(store.settings.collect_times || []).join(', ') || '없음'}`),
                    el('div', `다음 수집: ${st?.next_collect || '-'}`),
                    el('div', `마지막 수집: ${s.last_run ? s.last_run.finished_at.slice(5, 16) : '아직 없음'}`),
                    el('div', `쿨메신저: ${store.settings.coolmessenger_path || store.settings.coolmessenger_name || '미설정'}${store.settings.coolmessenger_folder ? ' (+ 받은 파일 폴더)' : ''}`),
                    el('div', `에듀파인 폴더: ${store.settings.edufine_folder || '미설정'}`),
                    el('div', `텔레그램: ${st?.telegram.linked ? '연결됨' : '연결 안 됨'}`)))));
    })();
}

export function openToday() {
    openTab({ id: 'today', title: '📋 브리핑', refresh: true, render: renderToday });
}

registerView({
    id: 'today', icon: '📋', label: '브리핑',
    renderSide(body) {
        body.append(el('div.empty', '오른쪽에 오늘의 업무 브리핑이 있습니다.'),
            el('ul.list',
                el('li', { onclick: openToday }, el('span.ttl', '📋 브리핑 새로 고침')),
                el('li', { onclick: openCalendar }, el('span.ttl', '📅 달력 열기')),
                el('li', { onclick: openPaste }, el('span.ttl', '＋ 메시지·공문 붙여넣기'))));
    },
    open: openToday,
});

on('data-changed', () => findTab('today')?.rerender());
