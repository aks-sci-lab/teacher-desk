// 출결 메뉴에서 조퇴·외출, 결석계, 월별 통계를 함께 제공
import { api } from '../api.js';
import { el, clear, guarded, todayIso } from '../ui.js';
import { registerView, openTab, refreshSide, findTab, on } from '../shell.js';
import { renderPassesSide, openForm } from './passes.js';
import { renderAbsenceSide, openAbsenceForm } from './absence.js';

const SECTIONS = [
    { id: 'passes', label: '조퇴·외출' },
    { id: 'absences', label: '결석계' },
    { id: 'month', label: '월별 통계' },
];
let section = 'passes';
let month = todayIso().slice(0, 7);
let latestReport = null;
let reportRequest = 0;

function openAttendance() {
    const selected = SECTIONS.find(item => item.id === section);
    openTab({
        id: 'attendance',
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
        body.append(el('div.filters',
            el('input', {
                type: 'month',
                value: month,
                'aria-label': '통계 기준 월',
                onchange: e => {
                    month = e.target.value || todayIso().slice(0, 7);
                    latestReport = null;
                    reportRequest++;
                    openAttendance();
                },
            })));
    }
}

function csvCell(value) {
    return `"${String(value ?? '').replace(/"/g, '""')}"`;
}

function downloadCsv(report) {
    const types = Object.keys(report.totals.absence_days);
    const header = ['번호', '이름', ...types.map(type => `${type} 결석일`),
        '결석일 합계', '결석계 건수', '조퇴 횟수', '외출 횟수'];
    const lines = [
        header,
        ...report.rows.map(row => [
            row.student_number ?? '', row.student_name,
            ...types.map(type => row.absence_days[type]),
            row.total_absence_days, row.absence_forms, row.early_count, row.out_count,
        ]),
        [
            '합계', '',
            ...types.map(type => report.totals.absence_days[type]),
            report.totals.total_absence_days, report.totals.absence_forms,
            report.totals.early_count, report.totals.out_count,
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
    const numberCell = (tag, count, students, metric, label, type = null) =>
        el(tag, count ? el('button.btn.small', {
            title: `${label} 날짜·사유 보기`,
            onclick: () => openDetails(report.month, students, metric, label, type),
        }, count) : '0');
    const cells = (tag, row, students) => [
        ...types.map(type => numberCell(tag, row.absence_days[type], students, 'absence_days', `${type} 결석`, type)),
        numberCell(tag, row.total_absence_days, students, 'absence_days', '결석 합계'),
        numberCell(tag, row.absence_forms, students, 'absence_forms', '결석계'),
        numberCell(tag, row.early_count, students, 'early_count', '조퇴'),
        numberCell(tag, row.out_count, students, 'out_count', '외출'),
    ];
    const table = el('table.tbl',
        el('thead', el('tr',
            el('th', '번호'), el('th', '이름'),
            types.map(type => el('th', `${type} 결석일`)),
            el('th', '결석 합계'), el('th', '결석계'), el('th', '조퇴'), el('th', '외출'))),
        el('tbody',
            report.rows.map(row => el('tr',
                el('td', row.student_number ?? '—'), el('td', row.student_name),
                cells('td', row, [row]))),
            el('tr',
                el('th', '합계'), el('th', ''),
                cells('th', report.totals, report.rows))));
    return table;
}

function openDetails(month, students, metric, label, type) {
    const details = students.flatMap(student => student.details[metric]
        .filter(detail => !type || detail.kind === type)
        .map(detail => ({ ...detail, number: student.student_number, name: student.student_name })))
        .sort((a, b) => a.date.localeCompare(b.date) || (a.number ?? 1000) - (b.number ?? 1000));
    const who = students.length === 1 ? students[0].student_name : '전체';
    openTab({
        id: 'attendance-details', title: `${who} ${label}`, refresh: true,
        render(pane) {
            pane.append(
                el('h1', `${who} · ${label}`),
                el('p.sub', `${month} · ${details.length}건`),
                el('table.tbl',
                    el('thead', el('tr', ['날짜', '번호', '이름', '종류', '사유'].map(text => el('th', text)))),
                    el('tbody', details.map(detail => el('tr',
                        el('td', `${detail.date}${detail.time ? ' ' + detail.time : ''}`),
                        el('td', detail.number ?? '—'), el('td', detail.name),
                        el('td', detail.kind), el('td', detail.reason || '기록 없음'))))));
        },
    });
}

function renderMonthlyReport(pane) {
    const reportBox = el('div');
    const exportButton = el('button.btn', {
        disabled: !latestReport,
        onclick: () => latestReport && downloadCsv(latestReport),
    }, 'CSV 내려받기');
    const reload = guarded(async () => {
        const selectedMonth = month;
        const request = ++reportRequest;
        exportButton.disabled = true;
        latestReport = null;
        clear(reportBox, el('p.muted', `${selectedMonth} 출결을 불러오는 중…`));
        const report = await api.get(`/api/attendance/month?month=${encodeURIComponent(selectedMonth)}`);
        if (request !== reportRequest || selectedMonth !== month) return;
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

registerView({
    id: 'attendance', icon: '☑', label: '출결',
    renderSide,
    open: openAttendance,
});

on('passes-changed', () => {
    if (section === 'month') {
        latestReport = null;
        reportRequest++;
        if (findTab('attendance')) findTab('attendance').rerender();
    }
});

on('absences-changed', () => {
    if (section === 'month') {
        latestReport = null;
        reportRequest++;
        if (findTab('attendance')) findTab('attendance').rerender();
    }
});
