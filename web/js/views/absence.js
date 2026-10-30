// 결석계: 작성 → 미리보기 → 인쇄 → 기록
import { api } from '../api.js';
import { el, clear, toast, guarded, todayIso, dayLabel, longDate, monthNav, monthKey, dayHead } from '../ui.js';
import { openTab, refreshSide, findTab, on, emit } from '../shell.js';
import { store } from '../store.js';

const TYPE_LABELS = { '질병': '병결', '출석인정': '인정결', '기타': '기타결', '미인정': '미인정결' };
let absences = [];
let selectedDate = todayIso();

async function loadDay() {
    absences = await api.get(`/api/absences?month=${monthKey(selectedDate)}`);
}

function absenceNode(a) {
    const s = store.settings;
    const cls = [s.grade && `${s.grade}학년`, s.class_no && `${s.class_no}반`].filter(Boolean).join(' ');
    const range = a.start_date === a.end_date
        ? longDate(a.start_date)
        : `${longDate(a.start_date)} ~ ${longDate(a.end_date)}`;
    return el('div.absence-paper',
        el('div.school', s.school_name || ''),
        el('h1', '결 석 계'),
        el('p.absence-note', '기본 양식 · 학교에서 별도로 정한 공식 서식이 있으면 해당 서식을 확인해 주세요.'),
        el('table',
            el('tbody',
                el('tr', el('th', '학 반'), el('td', `${cls || '　학년　반'}  ${a.student_number != null ? a.student_number + '번' : ''}`)),
                el('tr', el('th', '성 명'), el('td', a.student_name)),
                el('tr', el('th', '결석 기간'), el('td', range)),
                el('tr', el('th', '결석 종류'), el('td', TYPE_LABELS[a.absence_type] || a.absence_type)),
                a.dates && el('tr', el('th', '실제 결석일'), el('td',
                    `${a.dates.join(', ') || '해당 없음'} (총 ${a.dates.length}일, 주말·학사일정 공휴일·휴업일 제외)`)),
                el('tr', el('th', '결석 사유'), el('td.absence-reason', a.reason)),
                a.note && el('tr', el('th', '비 고'), el('td', a.note)))),
        el('p.absence-confirm', '위와 같이 결석하였음을 알려 드립니다.'),
        el('div.absence-sign',
            el('div', `보호자 ${a.guardian_name || ''}  (서명)`),
            el('div', `${longDate(a.end_date)}`, el('br'), `담임 ${s.teacher_name || '　　　　　　'}  (인)`)),
        el('div.absence-school', `${s.school_name || ''} 귀중`),
        a.serial && el('div.absence-serial', `작성 번호 ${a.serial}`));
}

function printAbsence(a) {
    const area = document.getElementById('print-area');
    clear(area, absenceNode(a));
    window.print();
}

export function renderAbsenceSide(body, actions) {
    actions.append(el('button.icon-btn', {
        title: '새로 고침', onclick: guarded(async () => { await loadDay(); refreshSide(); }),
    }, '⟳'));
    const pick = guarded(async iso => { selectedDate = iso; await loadDay(); refreshSide(); });
    const first = `${monthKey(selectedDate)}-01`;
    const monthNo = Number(monthKey(selectedDate).slice(5));
    body.append(
        monthNav(selectedDate, pick),
        el('p.small.muted', { style: 'padding: 6px 12px 0; margin: 0' }, `${monthNo}월 · 결석계 ${absences.length}건`),
        el('div.row', { style: 'padding: 10px 12px' },
            el('button.btn.primary', { onclick: () => openAbsenceForm() }, '+ 결석계')));
    const list = el('ul.list');
    if (!absences.length) list.append(el('li.empty', `${monthNo}월 결석계가 없습니다`));
    // 시작 날짜별로 묶음 (전달에서 이어진 결석은 '전달부터' 묶음)
    const groupOf = a => (a.start_date < first ? 'prev' : a.start_date);
    let lastGroup = null;
    for (const a of absences) {
        const g = groupOf(a);
        if (g !== lastGroup) {
            lastGroup = g;
            list.append(dayHead(g === 'prev' ? '전달부터 이어짐' : dayLabel(g, false), absences.filter(x => groupOf(x) === g).length));
        }
        list.append(el('li', {
            className: findTab(`absence-${a.id}`) ? 'on' : '',
            onclick: () => openAbsence(a),
        },
        el('span.ttl', `${a.student_number != null ? a.student_number + '번 ' : ''}${a.student_name}`),
        el('span.meta', `${a.absence_type} · ${a.start_date === a.end_date ? a.start_date : `${a.start_date}~${a.end_date}`}`)));
    }
    body.append(list);
}

export function openAbsenceForm(initial = {}) {
    const editing = !!initial.id;
    const a = {
        student_name: '',
        student_number: null,
        start_date: selectedDate,
        end_date: selectedDate,
        absence_type: '질병',
        reason: '',
        guardian_name: '',
        note: '',
        ...initial,
    };
    openTab({
        id: editing ? `absence-edit-${a.id}` : 'absence-new',
        refresh: !editing,
        title: editing ? `${a.student_name} 결석계 수정` : '새 결석계',
        render(pane, tab) {
            const preview = el('div.preview-box');
            const redraw = () => clear(preview, absenceNode(a));
            let dateRequest = 0;
            const updateDates = guarded(async () => {
                const request = ++dateRequest;
                a.dates = null;
                redraw();
                const result = await api.post('/api/absences/preview', {
                    start_date: a.start_date, end_date: a.end_date,
                });
                if (request !== dateRequest) return;
                a.dates = result.dates;
                redraw();
            });
            const student = store.students.length
                ? el('select', { value: a.student_number == null ? '' : String(a.student_number), onchange: e => {
                    const selected = e.target.value === 'manual'
                        ? null
                        : store.students.find(s => String(s.number) === e.target.value);
                    if (selected) {
                        a.student_name = selected.name;
                        a.student_number = selected.number;
                        name.value = selected.name;
                        number.value = selected.number;
                    } else if (e.target.value === 'manual') {
                        a.student_name = '';
                        a.student_number = null;
                        name.value = '';
                        number.value = '';
                    }
                    redraw();
                } },
                    el('option', { value: '' }, '— 학생 선택 —'),
                    store.students.map(s => el('option', {
                        value: s.number, selected: s.number === a.student_number,
                    }, `${s.number}번 ${s.name}`)),
                    el('option', { value: 'manual' }, '직접 입력…'))
                : null;
            const number = el('input', {
                type: 'text', inputMode: 'numeric', value: a.student_number ?? '',
                placeholder: '번호', 'aria-label': '학생 번호',
                oninput: e => { a.student_number = /^\d+$/.test(e.target.value) ? Number(e.target.value) : null; redraw(); },
            });
            const name = el('input', {
                type: 'text', value: a.student_name, placeholder: '학생 이름',
                oninput: e => { a.student_name = e.target.value; redraw(); },
            });
            const reason = el('textarea', {
                rows: 4, value: a.reason, placeholder: '결석 사유를 입력하세요',
                oninput: e => { a.reason = e.target.value; redraw(); },
            });
            const field = (label, key, type = 'text') => el('label.field', el('span', label),
                el('input', {
                    type, value: a[key], onchange: e => {
                        a[key] = e.target.value;
                        if (type === 'date') updateDates();
                        else redraw();
                    },
                }));
            const save = async printAfter => {
                if (!a.student_name.trim()) return toast('학생을 고르거나 이름을 입력하세요', 'error');
                if (!a.reason.trim()) return toast('결석 사유를 입력하세요', 'error');
                const payload = {
                    student_name: a.student_name.trim(), student_number: a.student_number,
                    start_date: a.start_date, end_date: a.end_date, absence_type: a.absence_type,
                    reason: a.reason.trim(), guardian_name: a.guardian_name, note: a.note,
                };
                const saved = editing
                    ? await api.patch(`/api/absences/${a.id}`, payload)
                    : await api.post('/api/absences', payload);
                toast(`결석계 ${editing ? '저장' : '작성'} 완료 (${saved.serial})`);
                selectedDate = saved.start_date;
                await loadDay();
                refreshSide();
                emit('absences-changed', saved);
                tab.close();
                openAbsence(saved);
                if (printAfter) printAbsence(saved);
            };
            const manual = el('div.student-row', number, name);
            const studentField = student ? el('div.student-row', student, manual) : manual;
            const type = el('div.seg', Object.keys(TYPE_LABELS).map(value => el('button', {
                type: 'button', className: a.absence_type === value ? 'on' : '',
                onclick: () => { a.absence_type = value; redraw(); tab.rerender(); },
            }, TYPE_LABELS[value])));
            pane.append(
                el('h1', editing ? '결석계 고치기' : '결석계 작성'),
                el('p.sub', '작성 내용을 미리 보고 인쇄합니다. 이 기본 양식은 학교의 공식 서식과 다를 수 있습니다.'),
                el('div.pass-layout',
                    el('div.pass-form',
                        el('div.field', el('span', '학생'), studentField),
                        el('div.pass-times',
                            field('결석 시작일', 'start_date', 'date'),
                            field('결석 종료일', 'end_date', 'date')),
                        el('div.field', el('span', '결석 종류'), type),
                        el('label.field', el('span', '결석 사유'), reason),
                        field('보호자 이름 (선택)', 'guardian_name'),
                        field('비고 (선택)', 'note'),
                        el('p.hint.small', '학교에서 별도 공식 결석계를 사용한다면 그 양식으로 제출해야 합니다.'),
                        el('div.row.pass-actions',
                            el('button.btn.primary', { onclick: guarded(() => save(true)) }, '저장하고 인쇄'),
                            el('button.btn', { onclick: guarded(() => save(false)) }, '저장만'))),
                    el('div.pass-preview', preview)));
            redraw();
            updateDates();
        },
    });
}

export function openAbsence(a) {
    openTab({
        id: `absence-${a.id}`, title: `${a.student_name} 결석계`,
        render(pane) {
            pane.append(
                el('h1', '결석계'),
                el('p.sub', `${a.student_number != null ? a.student_number + '번 ' : ''}${a.student_name} · ${a.start_date}~${a.end_date} · ${a.absence_type} · ${a.serial}`),
                el('div.row',
                    el('button.btn.primary', { onclick: guarded(() => printAbsence(a)) }, '인쇄'),
                    el('button.btn', { onclick: () => openAbsenceForm(a) }, '고치기')),
                el('div.preview-box.wide', absenceNode(a)));
        },
    });
}

on('absences-changed', guarded(async () => { await loadDay(); refreshSide(); }));

export async function initAbsences() {
    await loadDay();
}
