// 출결 메뉴에서 출석부, 조퇴·외출, 결석계, 월별 통계를 함께 제공
import { api } from '../api.js';
import { el, clear, guarded, modal, monthNav, monthKey, toast, todayIso } from '../ui.js';
import { registerView, openTab, refreshSide, findTab, on, emit } from '../shell.js';
import { renderPassesSide, openForm, passesMonth, setPassesMonth } from './passes.js';
import { renderAbsenceSide, openAbsenceForm, openAbsenceById } from './absence.js';

const SECTIONS = [
    { id: 'register', label: '출석부' },
    { id: 'passes', label: '조퇴·외출' },
    { id: 'absences', label: '결석계' },
    { id: 'month', label: '월별 통계' },
];
let section = 'passes';
// 기준 월은 왼쪽 조퇴·외출 목록의 ◀ 월 ▶ 과 같은 값 (passes.js 가 가짐)
let latestReport = null;
let reportRequest = 0;

function openAttendance() {
    const selected = SECTIONS.find(item => item.id === section);
    openTab({
        id: 'attendance',
        menu: true,
        title: selected.label,
        refresh: true,
        render(pane) {
            pane.append(el('h1', selected.label));
            if (section === 'passes') {
                pane.append(
                    el('p.sub', '왼쪽 목록에서 발급 기록을 열고, 새 조퇴증·외출증을 작성할 수 있습니다.'),
                    el('div.row',
                        el('button.btn.primary', { onclick: () => openForm({ kind: 'early' }) }, '+ 조퇴증'),
                        el('button.btn.primary', { onclick: () => openForm({ kind: 'out' }) }, '+ 외출증')));
                return;
            }
            if (section === 'absences') {
                pane.append(
                    el('p.sub', '왼쪽 목록에서 결석계를 확인하고, 새 결석계를 작성할 수 있습니다.'),
                    el('div.row', el('button.btn.primary', { onclick: () => openAbsenceForm() }, '+ 결석계')));
                return;
            }
            if (section === 'register') return renderRegister(pane);
            renderMonthlyReport(pane);
        },
    });
}

function selectSection(id) {
    section = id;
    reportRequest++;
    refreshSide();
    openAttendance();
}

function renderSide(body, actions) {
    body.append(el('div.seg.attendance-sections', SECTIONS.map(item =>
        el('button', {
            className: item.id === section ? 'on' : '',
            type: 'button',
            onclick: () => item.id !== section && selectSection(item.id),
        }, item.label))));
    if (section === 'passes') renderPassesSide(body, actions);
    else if (section === 'absences') renderAbsenceSide(body, actions);
    else {
        if (section === 'register') body.append(el('p.small.muted.reg-legend',
            '칸을 눌러 결석·지각·조퇴·결과를 체크합니다. 결석은 결석계 초안이 함께 생깁니다(점선 = 작성 필요). ',
            '글자: 결 결석 · 지 지각 · 조 조퇴 · 과 결과 · 외 외출, 작은 글자는 종류(질 질병 · 인 인정 · 기 기타 · 미 미인정 · ? 미정).'));
        body.append(monthNav(`${passesMonth()}-01`, guarded(async iso => {
            await setPassesMonth(monthKey(iso));
            latestReport = null;
            reportRequest++;
            refreshSide();
            openAttendance();
        })));
    }
}

const typeLabel = type => (type === '미정' ? '종류 미정' : type);

function csvCell(value) {
    return `"${String(value ?? '').replace(/"/g, '""')}"`;
}

function downloadCsv(report) {
    const types = Object.keys(report.totals.absence_days);
    const header = ['번호', '이름', ...types.map(type => `${typeLabel(type)} 결석일`),
        '결석일 합계', '결석계 건수', '조퇴 횟수', '외출 횟수', '지각 횟수', '결과 횟수'];
    const lines = [
        header,
        ...report.rows.map(row => [
            row.student_number ?? '', row.student_name,
            ...types.map(type => row.absence_days[type]),
            row.total_absence_days, row.absence_forms, row.early_count, row.out_count,
            row.late_count, row.result_count,
        ]),
        [
            '합계', '',
            ...types.map(type => report.totals.absence_days[type]),
            report.totals.total_absence_days, report.totals.absence_forms,
            report.totals.early_count, report.totals.out_count,
            report.totals.late_count, report.totals.result_count,
        ],
    ].map(row => row.map(csvCell).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['\uFEFF', lines], { type: 'text/csv;charset=utf-8' }));
    const link = el('a', { href: url, download: `출결-${report.month}.csv` });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderReportRows(report) {
    const types = Object.keys(report.totals.absence_days);
    // 0 은 흐린 '–', 0 이 아니면 눌러서 날짜·사유를 보는 작은 단추 (숫자는 가운데 정렬)
    const numberCell = (tag, count, students, metric, label, type = null, cls = '') =>
        el(tag, { className: `num${cls ? ' ' + cls : ''}` }, count ? el('button.num-btn', {
            title: `${label} 날짜·사유 보기`,
            onclick: () => openDetails(report.month, students, metric, label, type),
        }, count) : el('span.zero', '–'));
    const cells = (tag, row, students) => [
        ...types.map(type => numberCell(tag, row.absence_days[type], students, 'absence_days', `${type} 결석`, type)),
        numberCell(tag, row.total_absence_days, students, 'absence_days', '결석 합계', null, 'sum-col'),
        numberCell(tag, row.absence_forms, students, 'absence_forms', '결석계', null, 'group-start'),
        numberCell(tag, row.early_count, students, 'early_count', '조퇴', null, 'group-start'),
        numberCell(tag, row.out_count, students, 'out_count', '외출'),
        numberCell(tag, row.late_count, students, 'late_count', '지각'),
        numberCell(tag, row.result_count, students, 'result_count', '결과'),
    ];
    return el('div.att-wrap', el('table.tbl.att-table',
        el('thead',
            el('tr',
                el('th', { rowSpan: 2, className: 'col-no' }, '번호'), el('th', { rowSpan: 2, className: 'col-name' }, '이름'),
                el('th', { colSpan: types.length + 1, className: 'group' }, '결석일 (일)'),
                el('th', { colSpan: 5, className: 'group group-start' }, '건수')),
            el('tr',
                types.map(type => el('th.num', typeLabel(type))),
                el('th.num.sum-col', '합계'),
                el('th.num.group-start', '결석계'), el('th.num.group-start', '조퇴'), el('th.num', '외출'),
                el('th.num', '지각'), el('th.num', '결과'))),
        el('tbody',
            report.rows.map(row => el('tr',
                el('td.col-no', row.student_number ?? '—'), el('td.col-name', row.student_name),
                cells('td', row, [row]))),
            el('tr.total',
                el('th.col-no', ''), el('th.col-name', '합계'),
                cells('th', report.totals, report.rows)))));
}

function openDetails(month, students, metric, label, type) {
    const details = students.flatMap(student => student.details[metric]
        .filter(detail => !type || detail.kind === type)
        .map(detail => ({ ...detail, number: student.student_number, name: student.student_name })))
        .sort((a, b) => a.date.localeCompare(b.date) || (a.number ?? 1000) - (b.number ?? 1000));
    const who = students.length === 1 ? students[0].student_name : '전체';
    // 탭이 아니라 팝업 (Esc·바깥을 누르면 닫힘)
    modal({
        title: `${who} · ${label}`, wide: true,
        body: el('div',
            el('p.sub', `${month} · ${details.length}건`),
            details.length
                ? el('table.tbl',
                    el('thead', el('tr', ['날짜', '번호', '이름', '종류', '사유'].map(text => el('th', text)))),
                    el('tbody', details.map(detail => el('tr',
                        el('td', `${detail.date}${detail.time ? ' ' + detail.time : ''}`),
                        el('td', detail.number ?? '—'), el('td', detail.name),
                        el('td', detail.kind), el('td', detail.reason || '기록 없음')))))
                : el('p.muted', '내역이 없습니다.')),
    });
}

function renderMonthlyReport(pane) {
    const reportBox = el('div');
    const exportButton = el('button.btn', {
        disabled: !latestReport,
        onclick: () => latestReport && downloadCsv(latestReport),
    }, 'CSV 내려받기');
    const reload = guarded(async () => {
        const selectedMonth = passesMonth();
        const request = ++reportRequest;
        exportButton.disabled = true;
        latestReport = null;
        clear(reportBox, el('p.muted', `${selectedMonth} 출결을 불러오는 중…`));
        const report = await api.get(`/api/attendance/month?month=${encodeURIComponent(selectedMonth)}`);
        if (request !== reportRequest || selectedMonth !== passesMonth()) return;
        latestReport = report;
        exportButton.disabled = false;
        clear(reportBox,
            el('p.sub', `${selectedMonth} · 결석은 주말·학사일정 공휴일·휴업일을 제외합니다. 취소된 증은 집계하지 않습니다. 숫자를 누르면 날짜·사유가 나옵니다.`),
            latestReport.rows.length ? renderReportRows(latestReport) : el('p.empty', '집계할 출결 기록이 없습니다.'));
    });
    pane.append(
        el('div.row', el('button.btn', { onclick: reload }, '새로 고침'), exportButton),
        reportBox);
    reload();
}

// ---------------------------------------------------------------- 출석부 (0.5.95)
const KIND_CHAR = { absent: '결', late: '지', early: '조', result: '과', out: '외' };
const KIND_NAME = { absent: '결석', late: '지각', early: '조퇴', result: '결과', out: '외출' };
const TYPE_CHAR = { '질병': '질', '출석인정': '인', '기타': '기', '미인정': '미', '미정': '?' };
const TYPE_NAME = { '질병': '질병', '출석인정': '인정', '기타': '기타', '미인정': '미인정', '미정': '미정' };
const WEEK = '일월화수목금토';
let registerRequest = 0;

function entryText(e) {
    if (e.src === 'absence') return `결석(${TYPE_NAME[e.type]}) · 결석계 ${e.serial}${e.draft ? ' · 작성 필요' : (e.status === 'printed' ? ' · 인쇄함' : ' · 작성 완료')}`;
    if (e.src === 'pass') return `${KIND_NAME[e.kind]}증 ${e.serial}${e.time ? ' ' + e.time : ''}`;
    return `${TYPE_NAME[e.type]} ${KIND_NAME[e.kind]}${e.periods ? ` (${e.periods}교시)` : ''}${e.no_pass ? ' · 증 없음' : ''}${e.note ? ' · ' + e.note : ''}`;
}

function cellNode(entries) {
    // 증이 있는 날의 조퇴 체크는 증 하나만 보여 줌
    const shown = entries.filter(e => !(e.src === 'mark' && e.kind === 'early' && !e.no_pass));
    return shown.map(e => el('span', {
        className: `reg-mark k-${e.kind}${e.draft ? ' draft' : ''}${e.no_pass ? ' no-pass' : ''}`,
    }, KIND_CHAR[e.kind], e.type ? el('sup', TYPE_CHAR[e.type]) : null, e.no_pass ? el('sup', '!') : null));
}

async function sendRegister(change) {
    try {
        await api.post('/api/attendance/register', change);
    } catch (e) {
        if (e.status !== 409) throw e;
        if (!window.confirm(`${e.message}\n계속할까요?`)) return false;
        await api.post('/api/attendance/register', { ...change, force: true });
    }
    emit('absences-changed');
    emit('passes-changed');
    return true;
}

function openCell(student, day, entries) {
    const d = new Date(`${day}T00:00:00`);
    const who = `${student.student_number != null ? student.student_number + '번 ' : ''}${student.student_name}`;
    const base = { student_number: student.student_number, student_name: student.student_name, date: day };
    const current = entries.find(e => e.src !== 'pass') || null;
    const pass = entries.find(e => e.src === 'pass' && e.kind === 'early');
    const state = {
        kind: current ? current.kind : (pass ? 'early' : 'absent'),
        type: current?.type || '미정',
        periods: current?.periods || '', note: current?.note || '',
    };
    const box = el('div.reg-pop');
    let dlg;
    const draw = () => {
        const seg = (items, key) => el('div.seg', items.map(([value, label]) => el('button', {
            type: 'button', className: state[key] === value ? 'on' : '',
            onclick: () => { state[key] = value; draw(); },
        }, label)));
        clear(box,
            entries.length
                ? el('ul.reg-now', entries.map(e => el('li', entryText(e))))
                : el('p.muted', '아직 기록이 없습니다 (출석).'),
            el('div.field', el('span', '출결'), seg([['absent', '결석'], ['late', '지각'], ['early', '조퇴'], ['result', '결과']], 'kind')),
            el('div.field', el('span', '종류 (나중에 정해도 됨)'), seg(Object.entries(TYPE_NAME), 'type')),
            state.kind === 'result' && el('label.field', el('span', '교시 (예: 3,4)'),
                el('input', { value: state.periods, maxLength: 20, oninput: e => { state.periods = e.target.value; } })),
            state.kind !== 'absent' && el('label.field', el('span', '메모 (선택)'),
                el('input', { value: state.note, maxLength: 200, oninput: e => { state.note = e.target.value; } })),
            state.kind === 'absent' && el('p.small.muted', '결석으로 저장하면 결석계 초안이 생기거나(이어진 날은 한 장으로) 늘어납니다. 사유는 결석계에서 씁니다.'),
            state.kind === 'early' && !pass && el('p.small.muted', '조퇴증 없이 기록만 남깁니다. 증이 필요하면 [조퇴증 발급]을 누르세요.'));
    };
    draw();
    const absence = entries.find(e => e.src === 'absence');
    dlg = modal({
        title: `${who} · ${d.getMonth() + 1}/${d.getDate()}(${WEEK[d.getDay()]})`,
        body: box,
        actions: [
            el('button.btn.primary', { onclick: guarded(async () => {
                const ok = await sendRegister({ ...base, kind: state.kind, type: state.type, periods: state.periods, note: state.note });
                if (ok) dlg.close();
            }) }, '저장'),
            entries.some(e => e.src !== 'pass') && el('button.btn', { onclick: guarded(async () => {
                if (await sendRegister({ ...base, kind: 'clear' })) dlg.close();
            }) }, '지우기'),
            absence && el('button.btn', { onclick: guarded(async () => { dlg.close(); await openAbsenceById(absence.id); }) }, '결석계 열기'),
            !pass && el('button.btn', { onclick: () => {
                dlg.close();
                openForm({ kind: 'early', student_name: student.student_name, student_number: student.student_number, date: day });
            } }, '조퇴증 발급'),
        ].filter(Boolean),
    });
}

function renderRegister(pane) {
    const box = el('div', el('p.muted', '출석부를 불러오는 중…'));
    pane.append(el('p.sub', '수업일만 보입니다 (주말·학사일정 공휴일·휴업일 제외). 칸을 눌러 체크하고, 결석은 결석계와, 조퇴는 조퇴증과 이어집니다.'), box);
    const selectedMonth = passesMonth();
    const request = ++registerRequest;
    guarded(async () => {
        const data = await api.get(`/api/attendance/register?month=${encodeURIComponent(selectedMonth)}`);
        if (request !== registerRequest) return;
        if (!data.students.length) return clear(box, el('p.empty', '학생 명단이 없습니다. 조퇴·외출 → 👥 학생 명단에서 넣으세요.'));
        if (!data.days.length) return clear(box, el('p.empty', '이 달에는 수업일이 없습니다.'));
        const today = todayIso();
        const keyOf = s => `${s.student_number ?? ''}|${s.student_name}`;
        clear(box, el('div.att-wrap.reg-wrap', el('table.tbl.reg-table',
            el('thead', el('tr',
                el('th.col-no', '번호'), el('th.col-name', '이름'),
                data.days.map(day => {
                    const d = new Date(`${day}T00:00:00`);
                    return el('th', { className: `reg-day${day === today ? ' today' : ''}`, title: day },
                        String(d.getDate()), el('small', WEEK[d.getDay()]));
                }))),
            el('tbody', data.students.map(s => {
                const row = data.cells[keyOf(s)] || {};
                return el('tr',
                    el('td.col-no', s.student_number ?? '—'), el('td.col-name', s.student_name),
                    data.days.map(day => {
                        const entries = row[day] || [];
                        return el('td', { className: `reg-cell${day === today ? ' today' : ''}` }, el('button', {
                            type: 'button', title: `${s.student_name} ${day}${entries.length ? ' · ' + entries.map(entryText).join(' / ') : ''}`,
                            onclick: () => openCell(s, day, entries),
                        }, cellNode(entries)));
                    }));
            })))));
    })();
}

registerView({
    id: 'attendance', icon: '☑', label: '출결',
    renderSide,
    open: openAttendance,
});

on('passes-changed', () => {
    if (section === 'month' || section === 'register') {
        latestReport = null;
        reportRequest++;
        if (findTab('attendance')) findTab('attendance').rerender();
    }
});

on('absences-changed', () => {
    if (section === 'month' || section === 'register') {
        latestReport = null;
        reportRequest++;
        if (findTab('attendance')) findTab('attendance').rerender();
    }
});
