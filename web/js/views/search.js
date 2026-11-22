// 🔍 검색 (0.6.20 한 곳으로): 위쪽 큰 검색창 하나에서 받은 자료·일정·조퇴/외출·학생·업무 폴더 파일을 찾고,
// [AI 로 찾기] 를 누르면 이 PC 의 AI(LM Studio)가 자료를 읽고 답함 (텔레그램 'AI 검색'과 같은 길, 학생 이름 가림)
import { api } from '../api.js';
import { el, clear, guarded, debounce, dayLabel, catBadge, srcBadge, eventLine, toast } from '../ui.js';
import { icon } from '../icons.js';
import { registerView, openTab } from '../shell.js';
import { store } from '../store.js';
import { openItem } from './inbox.js';
import { openPass, openRoster } from './passes.js';
import { openCalendar } from './calendar.js';
import { openDocuments } from './documents.js';
import { openAI } from './ai.js';

let query = '';
let kind = 'all';
let aiBox = null;      // AI 답 (같은 검색어 동안 유지)
let aiFor = '';

const KINDS = [['all', '전체'], ['items', '받은 자료'], ['events', '일정'], ['passes', '조퇴·외출'], ['students', '학생'], ['files', '폴더 파일']];

// 찾은 낱말을 노란 칠로 (글자 그대로 넣고 칠만 더함)
function hl(text, words) {
    const s = String(text ?? '');
    if (!words.length || !s) return s;
    const re = new RegExp(`(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
    const out = [];
    let last = 0;
    s.replace(re, (m, _g, i) => { out.push(s.slice(last, i), el('mark', m)); last = i + m.length; return m; });
    out.push(s.slice(last));
    return out.filter(x => x !== '');
}

function group(title, n, body) {
    return el('section.card.sr-group', el('h3', el('span', title), el('span.count', n)), body);
}

async function runAi(box) {
    const q = query.trim();
    if (!q) return toast('찾을 내용을 먼저 쓰세요', 'info');
    aiFor = q;
    clear(box, el('p', el('span.spin'), ' 이 PC 의 AI 가 자료를 찾아 읽는 중… (1~2분)'));
    try {
        const r = await api.post('/api/ai/search', { q });
        if (aiFor !== q) return;
        clear(box,
            el('div.sr-ai-text', r.text),
            r.items.length > 0 && el('ul.list.compact', r.items.map(it => el('li', { onclick: () => openItem(it.id) },
                el('span.ttl', `[${it.n}] ${it.title}`), el('span.meta', srcBadge(it.source))))));
    } catch (e) {
        clear(box, el('p.err', e.message));
    }
}

function renderSearch(pane, tab) {
    const results = el('div.sr-results');
    const input = el('input.sr-input', { type: 'search', value: query, placeholder: '찾을 낱말 (예: 연말정산, 협의회, 학생 이름)', 'aria-label': '검색어',
        oninput: e => { query = e.target.value; run(); } });
    const chips = el('div.sr-kinds', { role: 'group', 'aria-label': '찾을 곳' }, KINDS.map(([k, label]) =>
        el('button', { type: 'button', className: `chip${kind === k ? ' on' : ''}`, onclick: () => { kind = k; for (const b of chips.children) b.classList.toggle('on', b.dataset.k === k); run(); }, dataset: { k } }, label)));
    if (!aiBox) aiBox = el('div');
    const aiOn = !!store.settings?.ai_enabled;
    const aiCard = el('section.card.sr-ai',
        el('div.row', el('b', icon('spark'), ' AI 로 찾기'),
            el('span.small.muted', aiOn ? '이 PC 의 AI 가 받은 자료를 읽고 답합니다. 학생 이름은 가려서 보냅니다.' : 'AI 가 꺼져 있습니다 (설정 → AI 에서 켜기)'),
            el('span.spacer'),
            el('button.btn.small.primary', { disabled: !aiOn, onclick: () => runAi(aiBox) }, 'AI 로 찾기')),
        aiBox);
    const run = debounce(guarded(async () => {
        const q = query.trim();
        if (aiFor && aiFor !== q) { aiFor = ''; clear(aiBox); }
        if (!q) return clear(results, el('div.empty', '받은 쪽지·공문(제목·내용·요약·문서번호), 일정, 조퇴·외출 기록, 학생 명단, 업무 폴더 파일(색인한 것)에서 찾습니다. 띄어 쓰면 모든 낱말이 들어간 것만 보여 줍니다.'));
        const words = q.split(/\s+/).filter(Boolean);
        const want = k => kind === 'all' || kind === k;
        const [r, files] = await Promise.all([
            api.get(`/api/search?q=${encodeURIComponent(q)}`),
            want('files') ? api.get(`/api/folderdoc/search?q=${encodeURIComponent(q)}`).then(x => x.files || [], () => []) : Promise.resolve([]),
        ]);
        if (query.trim() !== q) return;   // 그사이 더 쳤으면 버림
        const groups = [
            want('items') && r.items.length > 0 && group('받은 자료', r.items.length, el('ul.list', r.items.map(it => el('li', { onclick: () => openItem(it.id) },
                el('span.ttl', hl(it.title, words)), el('span.meta', srcBadge(it.source), catBadge(it.category), it.received_at.slice(0, 10)))))),
            want('events') && r.events.length > 0 && group('일정', r.events.length, el('div',
                r.events.map(e => eventLine(e, ev => (ev.item_id ? openItem(ev.item_id) : openCalendar()))))),
            want('passes') && r.passes.length > 0 && group('조퇴·외출', r.passes.length, el('ul.list', r.passes.map(p => el('li', { onclick: () => openPass(p) },
                el('span.ttl', hl(`${p.student_name} ${p.kind === 'out' ? '외출' : '조퇴'}`, words)), el('span.meta', dayLabel(p.date), p.reason || ''))))),
            want('students') && r.students.length > 0 && group('학생', r.students.length, el('ul.list', r.students.map(st => el('li', { onclick: openRoster },
                el('span.ttl', hl(`${st.number}번 ${st.name}`, words)))))),
            want('files') && files.length > 0 && group('업무 폴더 파일', files.length, el('ul.list', files.map(f => el('li', { title: '누르면 경로를 복사합니다', onclick: guarded(async () => { await navigator.clipboard.writeText(f.path); toast('파일 경로를 복사했습니다'); }) },
                el('span.ttl', hl(f.path.split(/[\\/]/).pop(), words)), el('span.meta', el('span', f.path), f.summary || f.snippet ? el('span', hl(f.summary || f.snippet, words)) : null)))),
                el('p.small.muted', el('a', { href: '#', onclick: e => { e.preventDefault(); openDocuments(); } }, '문서 작성'), '에서 이 파일들로 문서를 만들 수 있습니다.')),
        ].filter(Boolean);
        clear(results, groups.length ? groups : el('div.empty', '찾은 것이 없습니다. 낱말을 줄이거나 [AI 로 찾기]를 눌러 보세요.'));
    }), 250);
    pane.append(
        el('div.sr-head', el('h1', '검색'), el('span.small.muted', '어느 화면에서나 Ctrl+K')),
        el('div.sr-box', icon('search'), input),
        chips, aiCard, results,
        el('p.small.muted', '공개 자료로 하는 일반 질문은 ', el('a', { href: '#', onclick: e => { e.preventDefault(); openAI(); } }, 'AI 질문'), ' 메뉴에서 합니다.'));
    run();
    setTimeout(() => input.focus(), 0);
    tab.focusInput = () => { input.focus(); input.select(); };
}

export function openSearch() {
    const tab = openTab({ id: 'search', title: '검색', menu: true, wide: true, render: renderSearch });
    tab.focusInput?.();
}

registerView({ id: 'search', icon: '🔍', label: '검색', noSide: true, renderSide() {}, open: openSearch });
