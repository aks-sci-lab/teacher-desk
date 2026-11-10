// 학급 제출물 확인 · 상담 기록 (0.5.82)
// 상담 기록은 특히 민감: 이 PC 에만 저장, 검색·브리핑·AI·텔레그램에 쓰지 않음. 화면에서도 내용은 눌러야 펼쳐짐.
import { api } from '../api.js';
import { el, clear, guarded, toast, todayIso, ddayInfo, longDate, dayLabel } from '../ui.js';
import { refreshSide, findTab, openPopup } from '../shell.js';
import { store } from '../store.js';

const rerender = () => findTab('classroom')?.rerender();
let names = new Map();
const setStudents = list => { names = new Map((list || []).map(s => [s.number, s.name])); };
const who = n => `${n} ${names.get(n) || ''}`;
const classTitle = () => [store.settings?.grade && `${store.settings.grade}학년`, store.settings?.class_no && `${store.settings.class_no}반`].filter(Boolean).join(' ');

function dday(iso) {
    if (!iso) return null;
    const d = ddayInfo(iso);
    return el('span', { className: `dday ${d.cls}` }, d.label);
}

function printNode(node, landscape = false) {
    const area = document.getElementById('print-area');
    clear(area, landscape ? el('style', '@page { size: A4 landscape; margin: 10mm; }') : null, el('div.cl-print', node));
    window.print();
}

// ================================================================ 제출물
let taskData = null;
let taskSel = null;          // 왼쪽에서 고른 항목 (null: 전체 표)
let showClosed = false;

async function loadTasks() {
    taskData = await api.get(`/api/class/tasks${showClosed ? '?all=1' : ''}`);
    setStudents(taskData.students);
    if (taskSel != null && !taskData.tasks.some(t => t.id === taskSel)) taskSel = null;
}

export function renderTaskSide(body) {
    body.append(el('div.row.cl-side-p',
        el('button.btn.small', { className: `btn small${taskSel == null ? ' primary' : ''}`, onclick: () => { taskSel = null; refreshSide(); rerender(); } }, '전체 표'),
        el('label.check.small', { style: 'margin: 0' }, el('input', { type: 'checkbox', checked: showClosed, onchange: e => { showClosed = e.target.checked; taskData = null; rerender(); } }), '끝난 것도')));
    if (!taskData) return;
    if (!taskData.tasks.length) { body.append(el('div.empty', '제출물 항목이 없습니다')); return; }
    body.append(el('ul.list', taskData.tasks.map(t => el('li', {
        className: t.id === taskSel ? 'on' : '', onclick: () => { taskSel = t.id; refreshSide(); rerender(); },
    }, el('span.ttl', t.title, t.closed && el('span.muted.small', ' (끝남)')),
    el('span.meta', `미제출 ${t.missing.length} · 제출 ${t.done}/${t.total - t.exempt}`, t.due && ' · ', t.due && !t.closed && dday(t.due))))));
}

const MARK_NEXT = { undefined: 'done', done: 'exempt', exempt: null };
const markLabel = m => (m?.status === 'done' ? '✔' : m?.status === 'exempt' ? '면제' : '');

async function mark(t, numbers, status) {
    await api.post(`/api/class/tasks/${t.id}/marks`, { numbers, status });
    await loadTasks();
    refreshSide();
    rerender();
}

export function renderTasks(pane) {
    const body = el('div', el('p.muted', '불러오는 중…'));
    pane.append(body);
    guarded(async () => {
        if (!taskData) await loadTasks();
        refreshSide();
        if (!names.size) return clear(body, el('div.card.cl-empty', el('p', '학생 명단이 비어 있습니다. 출결 메뉴의 학생 명단을 먼저 넣으세요.')));
        const t = taskData.tasks.find(x => x.id === taskSel);
        clear(body, t ? taskDetail(t) : taskMatrix());
    })();
}

function newTaskForm() {
    const title = el('input', { type: 'text', placeholder: '예: 현장체험학습 동의서', maxLength: 60, style: 'min-width: 18em' });
    const due = el('input', { type: 'date', title: '마감 날짜 (없어도 됨)' });
    const add = guarded(async () => {
        const t = await api.post('/api/class/tasks', { title: title.value, due: due.value || null });
        toast(`'${t.title}' 을(를) 더했습니다`);
        await loadTasks();
        rerender();
    });
    title.addEventListener('keydown', e => { if (e.key === 'Enter') add(); });
    return el('div.row.cl-tools', title, el('span.muted.small', '마감'), due, el('button.btn.primary', { onclick: add }, '+ 항목 더하기'));
}

function taskMatrix() {
    const tasks = taskData.tasks;
    const nums = [...names.keys()];
    const head = el('tr', el('th.sticky', '번호 이름'), tasks.map(t => el('th.cl-mx-h', { title: t.note || t.title },
        el('button.linkish', { onclick: () => { taskSel = t.id; refreshSide(); rerender(); } }, t.title),
        el('div.small.muted', t.due ? `마감 ${t.due.slice(5).replace('-', '/')} ` : '마감 없음', t.due && !t.closed && dday(t.due)))));
    const rows = nums.map(n => el('tr', el('th.sticky', el('b', n), ' ', names.get(n)), tasks.map(t => {
        const m = taskData.marks[t.id]?.[n];
        const late = m?.status === 'done' && t.due && m.at.slice(0, 10) > t.due;
        return el('td.cl-mx-c', el('button', {
            className: `cl-mark ${m?.status || 'none'}${late ? ' late' : ''}`,
            title: `${who(n)} · ${t.title}: ${m?.status === 'done' ? `제출 ${m.at.slice(5, 16)}${late ? ' (마감 뒤)' : ''}` : m?.status === 'exempt' ? '면제' : '미제출'} — 누르면 바뀜 (미제출 → 제출 → 면제)`,
            'aria-label': `${n}번 ${t.title} ${m?.status === 'done' ? '제출' : m?.status === 'exempt' ? '면제' : '미제출'}`,
            onclick: guarded(() => mark(t, [n], MARK_NEXT[m?.status])),
        }, markLabel(m), late && el('small', '늦음')));
    })));
    const foot = el('tr.rest', el('th.sticky', '미제출'), tasks.map(t => el('td.cl-mx-c', el('b', { className: t.missing.length ? 'bad' : '' }, t.missing.length))));
    return el('div',
        el('div.cl-head', el('h1', '제출물 확인'), el('span.muted', '가정통신문 회신·서류 제출을 학생별로 표시합니다. 칸을 누르면 미제출 → ✔ 제출 → 면제 순서로 바뀝니다.')),
        newTaskForm(),
        tasks.length
            ? el('div.cl-mx-wrap', el('table.cl-table.cl-mx', el('thead', head), el('tbody', rows, foot)))
            : el('div.card.cl-empty', el('p', '위에서 제출받을 항목(예: 현장체험 동의서)을 더하세요.')),
        tasks.length > 0 && el('div.row.cl-tools', { style: 'margin-top: 12px' },
            el('button.btn', { onclick: () => printNode(el('div', el('h1', `${classTitle()} 제출물 확인표`, el('small', ` ${longDate(todayIso())}`)),
                el('table.cl-table', el('thead', el('tr', el('th', '번호 이름'), tasks.map(t => el('th', t.title)))),
                    el('tbody', nums.map(n => el('tr', el('th', who(n)), tasks.map(t => el('td', markLabel(taskData.marks[t.id]?.[n])))))))), true) }, '표 인쇄')));
}

function taskDetail(t) {
    const missing = t.missing;
    const save = change => guarded(async () => { await api.patch(`/api/class/tasks/${t.id}`, change); await loadTasks(); refreshSide(); rerender(); });
    const pick = el('input', { type: 'text', placeholder: '예: 3, 7, 12', style: 'width: 12em' });
    const list = missing.map(who).join(', ');
    return el('div',
        el('div.cl-head', el('h1', t.title), t.due && el('span.muted', `마감 ${dayLabel(t.due, false)} `, !t.closed && dday(t.due)),
            t.closed && el('span.tag', '끝남'), el('span.spacer'),
            el('button.btn.small', { onclick: () => { taskSel = null; refreshSide(); rerender(); } }, '← 전체 표')),
        el('div.cl-vote-now',
            el('div.card', el('h3', `미제출 ${missing.length}명`),
                missing.length ? el('div.cl-chips', missing.map(n => el('button.cl-chip', { title: '누르면 제출로 표시', onclick: guarded(() => mark(t, [n], 'done')) }, el('b', n), names.get(n))))
                    : el('p.muted', '모두 냈습니다 🎉'),
                missing.length > 0 && el('div.row', { style: 'margin-top: 10px' },
                    el('button.btn.small', { onclick: guarded(async () => {
                        await navigator.clipboard.writeText(`${t.title} 미제출: ${list}`);
                        toast('미제출 목록을 복사했습니다 (붙여넣기로 쓰세요)');
                    }) }, '미제출 목록 복사'),
                    el('button.btn.small', { onclick: guarded(() => confirm(`미제출 ${missing.length}명을 모두 제출로 표시할까요?`) && mark(t, missing, 'done')) }, '모두 제출로'))),
            el('div.card', el('h3', '번호로 한꺼번에'),
                el('div.row', pick,
                    el('button.btn.small.primary', { onclick: guarded(() => { const nums = pick.value.split(/[,\s]+/).map(Number).filter(n => names.has(n)); if (!nums.length) return toast('명단에 있는 번호를 넣으세요', 'error'); return mark(t, nums, 'done'); }) }, '제출'),
                    el('button.btn.small', { onclick: guarded(() => { const nums = pick.value.split(/[,\s]+/).map(Number).filter(n => names.has(n)); if (!nums.length) return toast('명단에 있는 번호를 넣으세요', 'error'); return mark(t, nums, 'exempt'); }) }, '면제'),
                    el('button.btn.small', { onclick: guarded(() => { const nums = pick.value.split(/[,\s]+/).map(Number).filter(n => names.has(n)); if (!nums.length) return toast('명단에 있는 번호를 넣으세요', 'error'); return mark(t, nums, null); }) }, '미제출로')),
                el('p.small.muted', `제출 ${t.done}명${t.late.length ? ` (마감 뒤 ${t.late.length}명)` : ''} · 면제 ${t.exempt}명 · 전체 ${t.total}명`))),
        el('details.cl-settings', el('summary', '항목 고치기 · 끝내기'),
            el('div.cl-set-grid',
                el('label.field', el('span', '이름'), el('input', { type: 'text', value: t.title, maxLength: 60, onchange: e => save({ title: e.target.value })() })),
                el('label.field', el('span', '마감 날짜'), el('input', { type: 'date', value: t.due || '', onchange: e => save({ due: e.target.value || null })() })),
                el('label.field', el('span', '메모'), el('input', { type: 'text', value: t.note || '', maxLength: 300, onchange: e => save({ note: e.target.value })() }))),
            el('div.row',
                el('button.btn', { onclick: save({ closed: !t.closed }) }, t.closed ? '다시 열기' : '끝내기 (목록에서 숨김)'),
                el('button.btn.danger', { onclick: guarded(async () => {
                    if (!confirm(`'${t.title}' 항목과 제출 표시를 모두 지울까요?`)) return;
                    await api.del(`/api/class/tasks/${t.id}`);
                    taskSel = null;
                    await loadTasks();
                    refreshSide();
                    rerender();
                }) }, '지우기'))));
}

// ================================================================ 상담 기록
const METHODS = ['대면', '전화', '문자', '온라인', '기타'];
const WITH = ['학생', '학부모', '학생·학부모', '기타'];
let counselData = null;
let counselSel = null;       // 고른 학생 번호 (null: 최근 상담)
let studentView = null;
let sideFilter = '';

let tgPending = null;   // 텔레그램으로 받은 초안 (0.5.84)
let counselYear = null; // 보는 학년도 (null: 이번 학년도). 지난 학년도는 보관함 — 읽기·인쇄만 (0.5.90)
const yq = () => (counselYear ? `?year=${counselYear}` : '');
async function loadCounsel() {
    counselData = await api.get(`/api/class/counsel${yq()}`);
    tgPending = counselData.readonly ? null : await api.get('/api/class/calllog/pending');
    setStudents(counselData.students);
    if (counselSel != null && !names.has(counselSel)) counselSel = null;
    studentView = counselSel != null ? await api.get(`/api/class/student/${counselSel}${yq()}`) : null;
}

export function renderCounselSide(body) {
    const ro = !!counselData?.readonly;
    if (counselData) body.append(el('div.cl-side-p.cl-year', el('label.small.muted', '학년도 '),
        el('select', { 'aria-label': '학년도 고르기', onchange: e => { counselYear = Number(e.target.value) === counselData.current_year ? null : Number(e.target.value); counselSel = null; rerender(); } },
            counselData.years.map(y => el('option', { value: y, selected: y === counselData.year }, y === counselData.current_year ? `${y}학년도 (이번)` : `${y}학년도 · 보관함`)))));
    body.append(el('div.row.cl-side-p',
        !ro && el('button.btn.primary.small', { onclick: () => openCounselForm({ number: counselSel }) }, '+ 상담 기록'),
        el('button.btn.small', { className: `btn small${counselSel == null ? ' ok' : ''}`, onclick: () => { counselSel = null; refreshSide(); rerender(); } }, '최근 상담')));
    if (!counselData) return;
    const filter = el('input', { type: 'search', placeholder: '번호·이름 찾기', value: sideFilter, style: 'margin: 8px 12px; width: calc(100% - 24px)',
        oninput: e => { sideFilter = e.target.value; for (const li of ul.children) li.hidden = sideFilter && !li.dataset.key.includes(sideFilter); } });
    const ul = el('ul.list', [...names.keys()].map(n => el('li', {
        className: n === counselSel ? 'on' : '', dataset: { key: `${n} ${names.get(n)}` }, hidden: sideFilter && !`${n} ${names.get(n)}`.includes(sideFilter),
        onclick: () => { counselSel = n; refreshSide(); rerender(); },
    }, el('span.ttl', who(n)), el('span.meta', counselData.counts[n] ? `상담 ${counselData.counts[n]}회 · 마지막 ${counselData.last[n].slice(5).replace('-', '/')}` : '상담 없음'))));
    body.append(filter, ul);
}

function counselLine(c, opts = {}) {
    const late = c.followup && !c.followup_done && c.followup_date && c.followup_date < todayIso();
    return el('details.cl-cs', { open: !!opts.open },
        el('summary',
            el('span.cl-cs-date', `${c.date.slice(2).replace(/-/g, '.')}${c.time ? ' ' + c.time : ''}`),
            opts.withName && el('b', `${c.number} ${c.student_name}`),
            el('span.tag', c.method), el('span.tag', c.with_whom),
            el('span.cl-cs-topic', c.topic),
            c.followup && !opts.readonly && el('span', { className: `tag ${c.followup_done ? 'ok' : late ? 'bad' : 'warn'}` }, c.followup_done ? '후속 끝' : '후속 남음'),
            el('span.spacer'),
            !opts.readonly && el('button.btn.small', { onclick: e => { e.preventDefault(); openCounselForm(c); } }, '고치기')),
        el('div.cl-cs-body',
            c.content ? el('p.cl-cs-text', c.content) : el('p.muted.small', '내용 없음'),
            c.followup && el('p', el('b', '후속 조치 '), c.followup, c.followup_date && el('span.muted', ` (${c.followup_date})`))));
}

export function renderCounsel(pane) {
    const body = el('div', el('p.muted', '불러오는 중…'));
    pane.append(body);
    guarded(async () => {
        await loadCounsel();
        refreshSide();
        if (!names.size) return clear(body, el('div.card.cl-empty', el('p', '학생 명단이 비어 있습니다. 출결 메뉴의 학생 명단을 먼저 넣으세요.')));
        const ro = !!counselData.readonly;
        const note = el('p.cl-private', '🔐 상담 기록은 이 PC 에만 저장되고, 검색·브리핑·AI·텔레그램에는 나오지 않습니다. 내용은 줄을 눌러야 펼쳐집니다 — 교실 TV 에 화면을 띄울 때 주의하세요.');
        const archive = ro && el('div.cl-archive', `📦 ${counselData.year}학년도 보관함 — 읽기·인쇄만 할 수 있습니다. 고치거나 지울 수 없고, 후속 조치 알림도 없습니다. 이름·번호는 그해 기록 그대로입니다(지금 명단과 잇지 않음).`,
            el('button.btn.small', { onclick: () => { counselYear = null; counselSel = null; rerender(); } }, `${counselData.current_year}학년도로 돌아가기`));
        if (studentView) {
            const s = studentView;
            return clear(body,
                el('div.cl-head', el('h1', `${s.number}번 ${s.name || '(명단에 없음)'}`), el('span.muted', `${s.year}학년도 상담 ${s.records.length}회`), el('span.spacer'),
                    !ro && el('button.btn.primary', { onclick: () => openCounselForm({ number: s.number }) }, '+ 이 학생 상담 기록'),
                    s.records.length > 0 && el('button.btn', { onclick: () => printNode(el('div',
                        el('h1', `상담 기록 — ${ro ? `${s.year}학년도 ` : classTitle()} ${s.number}번 ${s.name || ''}`, el('small', ` ${longDate(todayIso())} 인쇄`)),
                        el('table.cl-table', el('thead', el('tr', ['날짜', '방법·상대', '주제', '내용', '후속 조치'].map(h => el('th', h)))),
                            el('tbody', s.records.slice().reverse().map(c => el('tr', el('td', `${c.date}${c.time ? ' ' + c.time : ''}`), el('td', `${c.method} · ${c.with_whom}`),
                                el('td', c.topic), el('td.pre', c.content || ''), el('td.pre', c.followup ? `${c.followup}${c.followup_done ? ' (끝)' : ''}` : ''))))))) }, '상담 기록 인쇄')),
                archive, note,
                s.missing_tasks.length > 0 && el('div.cl-warn', el('b', '안 낸 제출물: '), s.missing_tasks.map(t => t.title + (t.due ? ` (마감 ${t.due.slice(5)})` : '')).join(' · ')),
                s.records.length ? el('div.cl-cs-list', s.records.map(c => counselLine(c, { readonly: ro }))) : el('div.card.cl-empty', el('p', ro ? '이 학년도에 이 학생의 상담 기록이 없습니다.' : '아직 상담 기록이 없습니다.')));
        }
        const d = counselData;
        clear(body,
            el('div.cl-head', el('h1', ro ? `상담 기록 · ${d.year}학년도 보관함` : '상담 기록'),
                el('span.muted', ro ? `${d.year}.3.1 ~ ${d.year + 1}.2.말` : `${d.year}학년도 (${d.year}.3.1부터) · 왼쪽에서 학생을 고르면 그 학생의 기록을 모아 봅니다. 지난 학년도는 왼쪽 위 '학년도'의 보관함에서 봅니다.`),
                el('span.spacer'),
                !ro && el('button.btn', { onclick: () => openCallImport(), title: '휴대폰 백업 앱이 내보낸 통화·문자 파일로 상담 기록 초안 만들기' }, '📱 통화·문자 불러오기'),
                !ro && el('button.btn.primary', { onclick: () => openCounselForm({}) }, '+ 상담 기록'),
                ro && d.records.length > 0 && el('button.btn', { onclick: () => printNode(el('div',
                    el('h1', `상담 기록 — ${d.year}학년도 (보관함)`, el('small', ` ${longDate(todayIso())} 인쇄`)),
                    el('table.cl-table', el('thead', el('tr', ['날짜', '학생', '방법·상대', '주제', '내용', '후속 조치'].map(h => el('th', h)))),
                        el('tbody', d.records.slice().reverse().map(c => el('tr', el('td', `${c.date}${c.time ? ' ' + c.time : ''}`), el('td', `${c.number} ${c.student_name}`),
                            el('td', `${c.method} · ${c.with_whom}`), el('td', c.topic), el('td.pre', c.content || ''), el('td.pre', c.followup || '')))))), true) }, '보관함 인쇄')),
            archive, note,
            tgPending?.drafts.length > 0 && el('div.card.cl-tg-pending',
                el('h3', `📱 텔레그램으로 받은 초안 ${tgPending.drafts.length}건`),
                el('p.small.muted', `확인해 저장하기 전에는 상담 기록이 아닙니다. 저장하지 않은 초안은 받은 날부터 ${tgPending.keep_days}일 뒤 지워집니다.`),
                el('div.row',
                    el('button.btn.primary', { onclick: () => openCallImport(tgPending) }, '확인하고 저장하기'),
                    el('button.btn.danger', { onclick: guarded(async () => {
                        if (!confirm('텔레그램으로 받은 초안을 모두 버릴까요? (상담 기록에는 영향 없음)')) return;
                        await api.post('/api/class/calllog/pending/drop', {});
                        rerender();
                    }) }, '모두 버리기'))),
            d.pending.length > 0 && el('div.card.cl-pending', el('h3', `후속 조치 남은 것 ${d.pending.length}`),
                el('ul.list', d.pending.map(p => el('li', { onclick: () => { counselSel = p.number; refreshSide(); rerender(); } },
                    el('span.ttl', `${p.number} ${p.student_name} · ${p.topic}`), el('span.meta', p.followup.slice(0, 40), p.followup_date && ' · ', p.followup_date && dday(p.followup_date)))))),
            el('h2', ro ? `${d.year}학년도 상담 (최근 100개)` : '최근 상담'),
            d.records.length ? el('div.cl-cs-list', d.records.map(c => counselLine(c, { withName: true, readonly: ro })))
                : el('div.card.cl-empty', el('p', ro ? '이 학년도에는 상담 기록이 없습니다.' : `${d.year}학년도 상담 기록이 아직 없습니다. [+ 상담 기록]으로 시작하세요.`)));
    })();
}

function openCounselForm(c) {
    const edit = !!c.id;
    openPopup({
        id: 'class-counsel', title: edit ? '상담 기록 고치기' : '상담 기록', refresh: true,
        render(pane, tab) {
            const number = el('select', [...names.keys()].map(n => el('option', { value: n, selected: n === c.number }, who(n))));
            if (c.number == null) number.prepend(el('option', { value: '', selected: true }, '학생 고르기'));
            const dateI = el('input', { type: 'date', value: c.date || todayIso() });
            const timeI = el('input', { type: 'time', value: c.time || '' });
            const seg = (list, cur) => {
                let val = cur;
                const box = el('span.seg', list.map(x => el('button', { type: 'button', className: x === val ? 'on' : '',
                    onclick: e => { val = x; for (const b of box.children) b.classList.toggle('on', b === e.currentTarget); } }, x)));
                box.value = () => val;
                return box;
            };
            const method = seg(METHODS, c.method || '대면');
            const withWhom = seg(WITH, c.with_whom || '학생');
            const topic = el('input', { type: 'text', value: c.topic || '', maxLength: 80, placeholder: '예: 교우 관계, 진로, 결석' });
            const content = el('textarea', { rows: 7, value: c.content || '', maxLength: 5000, placeholder: '상담 내용' });
            const follow = el('textarea', { rows: 2, value: c.followup || '', maxLength: 1000, placeholder: '예: 다음 주 학부모 다시 통화, 상담 교사 연계' });
            const followDate = el('input', { type: 'date', value: c.followup_date || '' });
            const followDone = el('input', { type: 'checkbox', checked: !!c.followup_done });
            const save = guarded(async () => {
                const data = { number: Number(number.value) || null, date: dateI.value, time: timeI.value || null, method: method.value(),
                    with_whom: withWhom.value(), topic: topic.value, content: content.value, followup: follow.value,
                    followup_date: followDate.value || null, followup_done: followDone.checked };
                if (!data.number) return toast('학생을 고르세요', 'error');
                if (edit) await api.patch(`/api/class/counsel/${c.id}`, data);
                else await api.post('/api/class/counsel', data);
                toast('저장했습니다');
                tab.close();
                rerender();
            });
            pane.append(el('div.cl-cs-form',
                el('div.cl-set-grid',
                    el('label.field', el('span', '학생'), number),
                    el('label.field', el('span', '날짜'), dateI),
                    el('label.field', el('span', '시각 (없어도 됨)'), timeI)),
                el('div.field', el('span', '방법'), method),
                el('div.field', el('span', '상대'), withWhom),
                el('label.field', el('span', '주제'), topic),
                el('label.field', el('span', '내용'), content),
                el('label.field', el('span', '후속 조치'), follow),
                el('div.row', el('span.muted.small', '후속 조치 날짜'), followDate, el('label.check', { style: 'margin: 0' }, followDone, '후속 조치 끝')),
                el('p.small.muted', '🔐 이 PC 에만 저장됩니다. 검색·브리핑·AI·텔레그램에는 쓰지 않습니다.'),
                el('div.row', { style: 'margin-top: 8px' },
                    el('button.btn.primary', { onclick: save }, '저장'),
                    el('button.btn', { onclick: () => tab.close() }, '취소'),
                    el('span.spacer'),
                    edit && el('button.btn.danger', { onclick: guarded(async () => {
                        if (!confirm('이 상담 기록을 지울까요? 되돌릴 수 없습니다.')) return;
                        await api.del(`/api/class/counsel/${c.id}`);
                        tab.close();
                        rerender();
                    }) }, '지우기'))));
            (c.number == null ? number : topic).focus();
        },
    });
}

// ================================================================ 통화·문자 내역 → 상담 초안 (0.5.83)
// 파일은 서버가 메모리에서만 읽고, 학부모 번호와 맞지 않는 것은 버림. 선생님이 고르고 고친 것만 저장.
function toBase64(buf) {
    const bytes = new Uint8Array(buf);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(bin);
}

function openCallImport(pending = null) {
    openPopup({
        id: 'class-calllog', title: pending ? '텔레그램으로 받은 초안' : '통화·문자 내역 불러오기', refresh: true,
        render(pane, tab) {
            if (pending) {   // 텔레그램으로 받아 둔 초안: 파일 고르기 없이 바로 확인
                setStudents(store.students);
                pane.append(el('div.cl-call', draftList({ drafts: pending.drafts, stats: null }, tab)));
                return;
            }
            const files = el('input', { type: 'file', multiple: true, accept: '.xml,.csv,.txt' });
            const since = el('input', { type: 'date', value: (() => { const d = new Date(); d.setDate(d.getDate() - 30); return d.toISOString().slice(0, 10); })() });
            const result = el('div');
            const load = guarded(async () => {
                if (!files.files.length) return toast('파일을 고르세요', 'error');
                if (files.files.length > 5) return toast('파일은 5개까지 고를 수 있습니다', 'error');
                clear(result, el('p.muted', '읽는 중…'));
                const payload = [];
                // 서버 요청 한도(20MB) 안에 들게 파일을 합쳐서 14MB 까지 (base64 로 보내면 4/3배)
                const total = [...files.files].reduce((a, f) => a + f.size, 0);
                if (total > 14 * 1024 * 1024) { clear(result); return toast('파일을 합쳐서 14MB 이하로 골라 주세요', 'error'); }
                for (const f of files.files) {
                    payload.push({ name: f.name, data: toBase64(await f.arrayBuffer()) });
                }
                try {
                    const r = await api.post('/api/class/calllog/parse', { files: payload, since: since.value || null });
                    setStudents(store.students);
                    clear(result, draftList(r, tab));
                } catch (e) {
                    clear(result);
                    throw e;
                }
            });
            pane.append(el('div.cl-call',
                el('p', '휴대폰 백업 앱이 내보낸 파일을 고르면, 학생 명단의 ', el('b', '학부모 연락처'), '와 번호가 같은 통화·문자로 상담 기록 초안을 만듭니다.'),
                el('ul.small.muted',
                    el('li', '읽는 파일: SMS Backup & Restore 의 calls.xml·sms.xml(UTF-8), 통화 기록 CSV(첫 줄에 전화번호·날짜 같은 제목) — 5개, 합쳐서 14MB 까지'),
                    el('li', '명단의 학부모 번호와 맞지 않는 번호는 버립니다 — 화면에 보이지도, 저장되지도 않습니다'),
                    el('li', '파일은 이 PC 안에서만 읽고 따로 저장하지 않습니다. 아래에서 고른 것만 상담 기록이 됩니다')),
                el('div.row', files, el('span.muted.small', '이 날짜부터'), since, el('button.btn.primary', { onclick: load }, '불러오기')),
                result));
        },
    });
}

function draftList(r, tab) {
    const s = r.stats;
    const drafts = r.drafts;
    const aiOn = !!store.settings?.ai_enabled;
    const rows = drafts.map(d => {
        const pick = el('input', { type: 'checkbox', checked: d.pick, disabled: !!d.already, 'aria-label': '저장할 것으로 고르기' });
        const topic = el('input', { type: 'text', value: d.topic, maxLength: 80 });
        const content = el('textarea', { rows: d.method === '문자' ? Math.min(6, d.content.split('\n').length + 1) : 2, value: d.content, maxLength: 5000 });
        const follow = el('input', { type: 'text', placeholder: '후속 조치 (없어도 됨)', maxLength: 1000 });
        const sum = aiOn && d.method === '문자' && !d.already && el('button.btn.small', { title: '이 PC 의 LM Studio 로 요약 (이름·번호는 가려서 보냄)', onclick: guarded(async e => {
            e.currentTarget.disabled = true;
            try {
                const res = await api.post('/api/class/calllog/summarize', { text: content.value });
                content.value = `요약: ${res.summary}\n\n--- 문자 원문 ---\n${content.value}`;
                content.rows = Math.min(10, content.value.split('\n').length + 1);
            } finally { e.currentTarget.disabled = false; }
        }) }, 'AI 요약 넣기');
        const card = el('div', { className: `cl-draft${d.already ? ' already' : ''}` },
            el('label.cl-draft-head', pick,
                el('span.cl-cs-date', `${d.date.slice(2).replace(/-/g, '.')} ${d.time}`),
                el('b', `${d.number} ${d.name}`), el('span.tag', d.method), el('span.tag', d.with_whom),
                d.already && el('span.tag.ok', '이미 저장함')),
            !d.already && el('div.cl-draft-body', el('label.field', el('span', '주제'), topic), el('label.field', el('span', '내용'), content),
                el('div.row', follow, sum)));
        return { d, card, pick, topic, content, follow };
    });
    const count = el('b');
    const update = () => { count.textContent = rows.filter(x => x.pick.checked && !x.d.already).length; };
    rows.forEach(x => x.pick.addEventListener('change', update));
    update();
    const save = guarded(async () => {
        const items = rows.filter(x => x.pick.checked && !x.d.already).map(x => ({
            number: x.d.number, date: x.d.date, time: x.d.time, method: x.d.method, with_whom: x.d.with_whom,
            topic: x.topic.value, content: x.content.value, followup: x.follow.value, duration: x.d.duration, import_key: x.d.import_key,
        }));
        if (!items.length) return toast('저장할 초안을 고르세요', 'error');
        const res = await api.post('/api/class/calllog/save', { items });
        toast(`상담 기록 ${res.saved}건을 저장했습니다${res.skipped ? ` (이미 있던 ${res.skipped}건은 건너뜀)` : ''}`);
        tab.close();
        rerender();
    });
    return el('div',
        s ? el('p.cl-call-stats', `통화 ${s.calls} · 문자 ${s.sms} 중 학부모 번호와 맞는 것 ${s.matched}건`,
            el('span.muted', ` · 맞지 않아 버림 ${s.discarded}`, s.old ? ` · 시작 날짜 전 ${s.old}` : '', s.unreadable ? ` · 날짜를 못 읽음 ${s.unreadable}` : '', s.already ? ` · 이미 저장함 ${s.already}` : ''))
            : el('p.cl-call-stats', `텔레그램으로 받은 초안 ${drafts.length}건`, el('span.muted', ' · 고른 것을 저장하면 목록에서 빠집니다')),
        drafts.length ? el('div.cl-drafts', rows.map(x => x.card))
            : el('div.card.cl-empty', el('p', '학부모 번호와 맞는 통화·문자가 없습니다. 학생 명단의 학부모 연락처와 시작 날짜를 확인하세요.')),
        drafts.length > 0 && el('div.row.cl-draft-foot',
            el('button.btn.small', { onclick: () => { rows.forEach(x => { if (!x.d.already) x.pick.checked = true; }); update(); } }, '모두 고르기'),
            el('button.btn.small', { onclick: () => { rows.forEach(x => { x.pick.checked = false; }); update(); } }, '모두 빼기'),
            el('span.spacer'),
            el('span', '고른 것 ', count, '건'),
            el('button.btn.primary', { onclick: save }, '고른 것 상담 기록으로 저장')));
}
