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

// ---------------------------------------------------------------- AI 요약 보기
// 요약 글(마크다운 비슷함)을 칸으로 나눠 보여 줌: 칸마다 색, 날짜에 D-day, 자료 번호는 누르면 그 자료가 열림
const AI_KINDS = [
    { test: /할\s*일|마감/, cls: 'todo', icon: '⏰', title: '꼭 할 일' },
    { test: /행사|회의|일정/, cls: 'event', icon: '🗓', title: '다가오는 행사·회의' },
    { test: /.*/, cls: 'note', icon: '📌', title: '알아 둘 내용' },
];
const SHOW_FIRST = { todo: 10, event: 6, note: 8 };

function parseAi(text) {
    const sections = [];
    let cur = null;
    for (const raw of text.split('\n')) {
        const line = raw.replace(/\*\*/g, '').trim();
        if (!line) continue;
        const head = line.match(/^#{1,4}\s*(.+)/);
        if (head) {
            const kind = AI_KINDS.find(k => k.test.test(head[1]));
            cur = { ...kind, rows: [] };
            sections.push(cur);
            continue;
        }
        if (!cur) continue;   // 맨 앞 인사말 같은 것은 버림
        let body = line.replace(/^[-*•·]\s*/, '');
        if (/^없음\.?$/.test(body)) continue;
        const refs = [];
        body = body.replace(/\[(\d+(?:\s*[,，]\s*\d+)*)\]/g, (_, nums) => { refs.push(...nums.split(/[,，]/).map(Number)); return ''; }).trim();
        // '10/30(금) · 할 일' → 날짜와 내용
        let when = '';
        const m = body.match(/^([^·]{1,24}?)\s+·\s+(.+)$/);
        if (m && /\d|상시|매주|매일|즉시/.test(m[1])) { when = m[1].trim(); body = m[2].trim(); }
        cur.rows.push({ when, text: body.replace(/\s*·\s*$/, ''), refs });
    }
    return sections.filter(s => s.rows.length);
}

// '10/30(금)' · '11월 3일' → 오늘·내일·D-3·지남
function dday(when) {
    const m = when.match(/(\d{1,2})\s*[\/.월]\s*(\d{1,2})/);
    if (!m) return null;
    const today = new Date(todayIso() + 'T00:00:00');
    let d = new Date(today.getFullYear(), Number(m[1]) - 1, Number(m[2]));
    if (d - today < -180 * 86400000) d = new Date(today.getFullYear() + 1, Number(m[1]) - 1, Number(m[2]));
    const n = Math.round((d - today) / 86400000);
    if (n < 0) return { label: '지남', cls: 'past' };
    if (n === 0) return { label: '오늘', cls: 'soon' };
    if (n === 1) return { label: '내일', cls: 'soon' };
    return { label: `D-${n}`, cls: n <= 3 ? 'soon' : '' };
}

// 줄 하나의 이름표 (지운 줄 기억용): 칸·날짜·내용, 띄어쓰기는 무시
const rowKey = (s, r) => `${s.cls}|${r.when}|${r.text}`.replace(/\s+/g, '');

function aiText(text, ids = [], hiddenList = []) {
    const sections = parseAi(text);
    if (!sections.length) return el('div.ai-text', text);   // 형식이 다르면 글 그대로
    const hidden = new Set(hiddenList);
    let showHidden = false;
    const wrap = el('div');
    const setHidden = guarded(async (key, hide) => {
        await api.post('/api/ai/hidden', { key, hide });
        if (hide) hidden.add(key); else hidden.delete(key);
        draw();
    });
    const refChip = n => {
        const id = ids[n - 1];
        return id ? el('a.ai-ref', { href: '#', title: '이 자료 열기', onclick: e => { e.preventDefault(); openItem(id); } }, n)
                  : el('span.ai-ref', n);
    };
    const row = (s, r) => {
        const dd = r.when && dday(r.when);
        const key = rowKey(s, r);
        const gone = hidden.has(key);
        return el('li.ai-row', { className: gone ? 'ai-row gone' : 'ai-row' },
            r.when ? el('span.ai-when', r.when, dd && el('span.ai-dday', { className: `ai-dday ${dd.cls}` }, dd.label)) : null,
            el('span.ai-txt', r.text),
            r.refs.length ? el('span.ai-refs', r.refs.map(refChip)) : null,
            gone ? el('button.ai-x', { title: '되돌리기', onclick: () => setHidden(key, false) }, '↺')
                 : el('button.ai-x', { title: '이 줄 지우기 (끝났거나 필요 없음)', onclick: () => setHidden(key, true) }, '✕'));
    };
    const draw = () => {
        let hiddenHere = 0;
        const secs = sections.map(s => {
            const rows = s.rows.filter(r => {
                const gone = hidden.has(rowKey(s, r));
                hiddenHere += gone;
                return showHidden || !gone;
            });
            if (!rows.length) return null;
            const first = showHidden ? rows.length : (SHOW_FIRST[s.cls] ?? 6);
            const list = el('ul.ai-list', rows.slice(0, first).map(r => row(s, r)));
            const more = rows.length > first && el('button.btn.small.ai-more', { onclick: () => {
                list.append(...rows.slice(first).map(r => row(s, r)));
                more.remove();
            } }, `더 보기 (${rows.length - first})`);
            const left = rows.filter(r => !hidden.has(rowKey(s, r))).length;
            return el('section', { className: `ai-sec ${s.cls}` },
                el('h4', `${s.icon} ${s.title}`, el('span.muted', ` ${left}`)), list, more || null);
        });
        clear(wrap,
            el('div.ai-sections', secs.filter(Boolean).length ? secs : el('p.muted.small', '모두 지웠습니다.')),
            hiddenHere ? el('button.btn.small.ai-hidden-toggle', { onclick: () => { showHidden = !showHidden; draw(); } },
                showHidden ? '지운 줄 감추기' : `지운 줄 ${hiddenHere}개 보기`) : '');
    };
    draw();
    return wrap;
}

// 🤖 지난 1주일 AI 요약 (LM Studio): 저장된 요약을 보여 주고, 오늘 만든 것이 없으면 새로 만듦
const AUTO_GAP_MS = 10 * 60 * 1000;

// 요약으로 수신함의 분류·일정 후보를 고친 수 (달력에 넣지 않은 메시지만)
function appliedNote(res, updates) {
    const all = [res?.applied, ...updates.map(u => u.applied)].filter(Boolean);
    const items = all.reduce((n, a) => n + a.items, 0);
    const added = all.reduce((n, a) => n + a.added, 0);
    if (!items) return '';
    return el('p.small.muted', `🤖 요약에 맞춰 수신함 메시지 ${items}건의 분류를 정리하고 일정 후보 ${added}건을 더했습니다 (달력에 넣은 메시지는 그대로).`);
}

function aiCard() {
    const body = el('div', el('p.muted.small', '불러오는 중…'));
    const show = (r, note) => {
        const res = r.result;
        const updates = (res && res.updates) || [];
        clear(body,
            // 요약 뒤 새 자료 알림 (새 것만 요약하거나 전체 다시 요약)
            r.new_count && !r.running ? el('div.ai-new',
                el('span', `🆕 요약 뒤 새 자료 ${r.new_count}건`),
                el('button.btn.small.primary', { onclick: () => run('new') }, '🆕 새 것만 요약'),
                el('button.btn.small', { onclick: () => run('full') }, '🤖 전체 다시 요약')) : '',
            // 덧붙인 '새로 들어온 것' (최근 것 먼저)
            updates.map(u => el('div.ai-update',
                el('h4', `🆕 새로 들어온 것 · ${u.at.slice(11)} · ${u.used ?? u.count}건`),
                aiText(u.text, u.ids, r.hidden))),
            res && res.text ? aiText(res.text, res.ids, r.hidden) : el('p.muted.small', '아직 요약이 없습니다.'),
            res && res.at ? el('p.small.muted', `${res.at} 전체 요약 · 자료 ${res.used ?? res.count}건${res.count > (res.used ?? res.count) ? ` (전체 ${res.count}건 중 최근 것)` : ''}${res.model ? ' · ' + res.model : ''}`) : null,
            appliedNote(res, updates),
            note ? el('p.small', { style: 'color: var(--danger)' }, note) : null,
            el('div.row', el('button.btn.small', { onclick: () => run('full') }, '🤖 전체 다시 요약')));
    };
    // 요약은 업무 데스크가 뒤에서 만듦 (몇 분 걸림). 화면은 5초마다 끝났는지만 확인
    let timer = null;
    const waiting = st => {
        const sec = Math.max(0, Math.round((Date.now() - new Date(st.started_at.replace(' ', 'T'))) / 1000));
        const what = st.kind === 'new' ? '새로 들어온 자료를' : '지난 1주일 자료를';
        clear(body,
            el('p.small', `🤖 ${what} 요약하는 중… ${Math.floor(sec / 60)}분 ${sec % 60}초`),
            el('p.small.muted', st.kind === 'new'
                ? '새 자료만 읽으므로 보통 1분 안쪽입니다. 다른 화면을 써도 됩니다.'
                : 'LM Studio 가 글을 읽고 쓰는 동안 다른 화면을 써도 됩니다. 이 PC 에서 4~5분쯤 걸리고, 15분까지 기다립니다.'));
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
    // kind: 'auto' (브리핑을 열 때) · 'new' (새 것만) · 'full' (전체 다시)
    const run = async kind => {
        const st = await api.get('/api/ai/weekly');
        if (!st.enabled) {
            body.closest('.brief')?.classList.add('no-ai');   // AI 를 안 쓰면 카드가 화면 폭을 다 씀
            clear(body, el('p.small', '⚙ 설정 → 🤖 AI 요약에서 LM Studio 를 연결하면 지난 1주일 쿨메신저·업무포털 내용을 요약해 드립니다.'));
            return;
        }
        if (st.running) return poll();
        if (kind === 'auto') {
            const fresh = st.result && st.result.at && st.result.at.slice(0, 10) === todayIso();
            // 하루 처음엔 전체, 그 뒤엔 새 자료가 있을 때만 새 것만.
            // 쪽지가 잇달아 오면 LM Studio 가 쉬지 못하므로, 자동으로는 마지막 요약 10분 뒤부터
            const last = (st.result?.updates?.[0]?.at || st.result?.at || '').replace(' ', 'T');
            const recent = last && Date.now() - new Date(last) < AUTO_GAP_MS;
            if (fresh && (!st.new_count || recent)) return show(st, st.error);
            kind = fresh ? 'new' : 'full';
        }
        try {
            await api.post('/api/ai/weekly', { kind });
            await poll();
        } catch (e) {
            show(st, e.message);
        }
    };
    setTimeout(() => run('auto').catch(e => show({}, e.message)), 0);  // 카드가 화면에 붙은 뒤 시작
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
            // 넓은 화면: 왼쪽 AI 요약, 오른쪽 오늘·마감·조퇴 카드 (빈 곳 없이)
            el('div.brief', aiCard(), el('div.cards',
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
                    el('div', `텔레그램: ${st?.telegram.linked ? '연결됨' : '연결 안 됨'}`))))));
    })();
}

export function openToday() {
    openTab({ id: 'today', title: '📋 브리핑', wide: true, refresh: true, render: renderToday });
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
