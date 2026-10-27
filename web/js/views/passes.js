// 🎫 조퇴증·외출증: 명단 → 작성 → 미리보기 → 인쇄 → 기록 → 학생 공유(QR)
import { api } from '../api.js';
import { el, clear, toast, guarded, todayIso, addDays, dayLabel, longDate, nowHm, pad } from '../ui.js';
import { registerView, openTab, refreshSide, findTab, emit, on } from '../shell.js';
import { store, loadStudents } from '../store.js';

const KIND = { early: '조퇴', out: '외출' };
const REASONS = {
    early: ['병원 진료', '몸이 아픔 (보건실 확인)', '가정 사정', '학교 대표 활동'],
    out: ['병원 진료', '개인 용무', '가정 사정', '학교 행사 준비'],
};
let sideDate = todayIso();
let dayPasses = [];

// ---------------------------------------------------------------- QR (jsdelivr 에서 필요할 때만 불러옴)
let qrLoading;
function loadQr() {
    if (window.qrcode) return Promise.resolve(true);
    qrLoading ||= new Promise(resolve => {
        const s = document.createElement('script');
        s.src = 'https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js';
        s.onload = () => resolve(true);
        s.onerror = () => { qrLoading = null; resolve(false); };
        document.head.append(s);
    });
    return qrLoading;
}

export async function qrDataUrl(text) {
    if (!(await loadQr())) return null;
    const qr = window.qrcode(0, 'M');
    qr.addData(text, 'Byte');
    qr.make();
    return qr.createDataURL(6, 2);
}

export function shareUrl(p) {
    const base = store.settings.pass_page_url || '';
    // pass 폴더가 teacher-desk 옆에 없으면 공유 링크를 만들 수 없음 (README 1-2 참고)
    return base && window.PassCodec ? window.PassCodec.shareUrl(base, p, store.settings) : '';
}

// ---------------------------------------------------------------- 증 양식
function hm(t) {
    if (!t) return '';
    const [h, m] = t.split(':').map(Number);
    return `${h < 12 ? '오전' : '오후'} ${h % 12 || 12}시 ${pad(m)}분`;
}

export function slipNode(p, copyLabel, qr) {
    const s = store.settings;
    const cls = [s.grade && `${s.grade}학년`, s.class_no && `${s.class_no}반`].filter(Boolean).join(' ');
    const rows = [
        ['학 반', `${cls || '　학년　반'}  ${p.student_number != null ? p.student_number + '번' : ''}`],
        ['성 명', p.student_name || ''],
        p.kind === 'out'
            ? ['외출 시간', `${longDate(p.date)}  ${hm(p.time_out)} ~ ${p.time_return ? hm(p.time_return) : '　　'}`]
            : ['조퇴 시간', `${longDate(p.date)}  ${hm(p.time_out)}`],
        ['사 유', p.reason || ''],
        ['보호자 확인', p.guardian_ok ? '☑ 연락(동의) 확인' : '☐ 연락(동의) 확인'],
    ];
    if (p.note) rows.push(['비 고', p.note]);
    return el('div.slip',
        copyLabel && el('div.copy', copyLabel),
        el('div.school', s.school_name || ''),
        el('h1', `${KIND[p.kind]}증`),
        el('table', el('tbody', rows.map(([k, v]) => el('tr', el('th', k), el('td', v))))),
        qr && el('img.qr', { src: qr, alt: '' }),
        el('div.sign',
            el('div.date', `발급 ${p.serial ? `No. ${p.serial}` : ''}`),
            el('div.who', `${longDate(p.date)}`, el('br'), `담임  ${s.teacher_name || '　　　　'}`, el('span.stamp', '(인)'))));
}

async function printPass(p, copies = 2) {
    const area = document.getElementById('print-area');
    const url = shareUrl(p);
    const qr = url ? await qrDataUrl(url) : null;
    clear(area);
    area.append(slipNode(p, copies > 1 ? '학생 보관용' : null, qr));
    if (copies > 1) area.append(el('hr.slip-cut'), slipNode(p, '담임 보관용', null));
    // 이미지가 다 그려진 뒤 인쇄
    await Promise.all([...area.querySelectorAll('img')].map(img => img.complete ? null : new Promise(r => { img.onload = img.onerror = r; })));
    window.print();
}

// ---------------------------------------------------------------- 목록 패널
async function loadDay() {
    dayPasses = await api.get(`/api/passes?date=${sideDate}`);
}

function renderSide(body, actions) {
    actions.append(
        el('button.icon-btn', { title: '학생 명단', onclick: openRoster }, '👥'),
        el('button.icon-btn', { title: '새로 고침', onclick: guarded(async () => { await loadDay(); refreshSide(); }) }, '⟳'));
    const date = el('input', { type: 'date', value: sideDate, onchange: guarded(async e => {
        sideDate = e.target.value || todayIso();
        await loadDay();
        refreshSide();
    }) });
    const move = n => guarded(async () => { sideDate = addDays(sideDate, n); await loadDay(); refreshSide(); });
    body.append(
        el('div.filters', el('button.icon-btn', { onclick: move(-1), title: '전날' }, '◀'), date,
            el('button.icon-btn', { onclick: move(1), title: '다음날' }, '▶'),
            sideDate !== todayIso() && el('button.chip', { onclick: guarded(async () => { sideDate = todayIso(); await loadDay(); refreshSide(); }) }, '오늘')),
        el('div.row', { style: 'padding: 10px 12px' },
            el('button.btn.primary', { onclick: () => openForm({ kind: 'early' }) }, '+ 조퇴증'),
            el('button.btn.primary', { onclick: () => openForm({ kind: 'out' }) }, '+ 외출증')));
    if (!store.students.length) {
        body.append(el('div.empty', '학생 명단이 없습니다. ', el('a', { href: '#', onclick: e => { e.preventDefault(); openRoster(); } }, '명단 붙여넣기')));
    }
    const list = el('ul.list');
    if (!dayPasses.length) list.append(el('li.empty', `${dayLabel(sideDate)} 발급 기록이 없습니다`));
    for (const p of dayPasses) {
        const st = { issued: '발급', returned: '복귀', cancelled: '취소' }[p.status];
        list.append(el('li', { onclick: () => openPass(p), className: findTab(`pass-${p.id}`) ? 'on' : '' },
            el('span.ttl', `${p.student_number != null ? p.student_number + '번 ' : ''}${p.student_name}`),
            el('span.meta', el('span.badge', { className: `badge cat ${p.kind === 'out' ? 'cat-수업·평가' : 'cat-행사'}` }, KIND[p.kind]),
                `${p.time_out || ''}${p.time_return ? '~' + p.time_return : ''}`,
                el('span.badge', { style: p.status === 'cancelled' ? 'text-decoration: line-through' : '' }, st))));
    }
    body.append(list);
}

// ---------------------------------------------------------------- 작성 화면
function roundedNow() {
    const [h, m] = nowHm().split(':').map(Number);
    const total = Math.ceil((h * 60 + m) / 5) * 5;
    return `${pad(Math.floor(total / 60) % 24)}:${pad(total % 60)}`;
}

function plusHour(t) {
    const [h, m] = t.split(':').map(Number);
    return `${pad(Math.min(h + 1, 23))}:${pad(m)}`;
}

export function openForm(init = {}) {
    const editing = !!init.id;
    const p = {
        kind: init.kind || 'early',
        student_name: '', student_number: null,
        date: sideDate,
        time_out: roundedNow(),
        time_return: '',
        reason: '', note: '', guardian_ok: false, share_reason: false,
        ...init,
    };
    if (p.kind === 'out' && !p.time_return) p.time_return = plusHour(p.time_out);
    const id = editing ? `pass-edit-${init.id}` : 'pass-new';
    openTab({
        id, refresh: !editing, title: editing ? `✎ ${init.student_name}` : `🎫 새 ${KIND[p.kind]}증`,
        render(pane, tab) {
            const preview = el('div.preview-box');
            const redraw = () => {
                clear(preview, slipNode({ ...p, serial: p.serial || '(저장하면 번호가 붙습니다)' }, null, null));
                tab.setTitle(editing ? `✎ ${p.student_name}` : `🎫 새 ${KIND[p.kind]}증`);
            };
            const set = (k, v) => { p[k] = v; redraw(); };

            const kindSeg = el('div.seg', ['early', 'out'].map(k => el('button', {
                className: p.kind === k ? 'on' : '',
                onclick: () => {
                    p.kind = k;
                    if (k === 'out' && !p.time_return) p.time_return = plusHour(p.time_out || roundedNow());
                    tab.rerender();
                },
            }, `${KIND[k]}증`)));

            // 학생 고르기: 명단이 있으면 선택, 없거나 '직접 입력'이면 글자 입력
            const nameInput = el('input', { type: 'text', value: p.student_name, placeholder: '이름', oninput: e => set('student_name', e.target.value) });
            const numInput = el('input', { type: 'text', inputMode: 'numeric', value: p.student_number ?? '', placeholder: '번호', style: 'width: 70px',
                oninput: e => set('student_number', /^\d+$/.test(e.target.value) ? Number(e.target.value) : null) });
            let studentField;
            if (store.students.length) {
                const sel = el('select', { onchange: e => {
                    const s = store.students.find(x => String(x.id) === e.target.value);
                    if (s) { p.student_name = s.name; p.student_number = s.number; nameInput.value = s.name; numInput.value = s.number; }
                    manual.hidden = e.target.value !== 'manual';
                    redraw();
                } },
                el('option', { value: '' }, '— 학생 선택 —'),
                store.students.map(s => el('option', { value: s.id, selected: s.name === p.student_name && s.number === p.student_number }, `${s.number}번 ${s.name}`)),
                el('option', { value: 'manual' }, '직접 입력…'));
                const known = store.students.some(s => s.name === p.student_name && s.number === p.student_number);
                const manual = el('div.row', { hidden: !(p.student_name && !known) }, numInput, nameInput);
                if (p.student_name && !known) sel.value = 'manual';
                studentField = el('div', sel, manual);
                setTimeout(() => sel.focus(), 0);
            } else {
                studentField = el('div.row', numInput, nameInput);
                setTimeout(() => nameInput.focus(), 0);
            }

            const reasonInput = el('input', { type: 'text', value: p.reason, placeholder: '사유', oninput: e => set('reason', e.target.value) });
            const save = async printAfter => {
                if (!p.student_name.trim()) return toast('학생을 고르거나 이름을 입력하세요', 'error');
                const body = {
                    kind: p.kind, student_name: p.student_name.trim(), student_number: p.student_number,
                    date: p.date, time_out: p.time_out || null, time_return: p.kind === 'out' ? (p.time_return || null) : null,
                    reason: p.reason, note: p.note, guardian_ok: p.guardian_ok, share_reason: p.share_reason,
                };
                const saved = editing ? await api.patch(`/api/passes/${init.id}`, body) : await api.post('/api/passes', body);
                toast(`${KIND[saved.kind]}증 ${editing ? '수정' : '발급'} (${saved.serial})`);
                sideDate = saved.date;
                await loadDay();
                refreshSide();
                emit('data-changed');
                tab.close();
                openPass(saved, true);
                if (printAfter) await printPass(saved);
            };

            pane.append(
                el('h1', editing ? `${KIND[p.kind]}증 고치기` : `${KIND[p.kind]}증 쓰기`),
                el('p.sub', '오른쪽 미리보기가 그대로 인쇄됩니다. A4 한 장에 학생 보관용·담임 보관용 2장이 나옵니다.'),
                el('div.pass-layout',
                    el('div',
                        el('div.field', el('span', '종류'), kindSeg),
                        el('div.field', el('span', '학생'), studentField),
                        el('div.grid2',
                            el('label.field', el('span', '날짜'), el('input', { type: 'date', value: p.date, onchange: e => set('date', e.target.value) })),
                            el('label.field', el('span', p.kind === 'out' ? '나가는 시각' : '조퇴 시각'),
                                el('input', { type: 'time', value: p.time_out, onchange: e => set('time_out', e.target.value) }))),
                        p.kind === 'out' && el('label.field', el('span', '복귀 예정 시각'),
                            el('input', { type: 'time', value: p.time_return, onchange: e => set('time_return', e.target.value) })),
                        el('div.field', el('span', '사유'), reasonInput,
                            el('div.row', REASONS[p.kind].map(r => el('button.chip', { onclick: () => { reasonInput.value = r; set('reason', r); } }, r)))),
                        el('label.check', el('input', { type: 'checkbox', checked: p.guardian_ok, onchange: e => set('guardian_ok', e.target.checked) }), '보호자 연락(동의) 확인'),
                        el('label.field', el('span', '비고 (선택)'), el('input', { type: 'text', value: p.note, onchange: e => set('note', e.target.value) })),
                        el('label.check', el('input', { type: 'checkbox', checked: p.share_reason, onchange: e => set('share_reason', e.target.checked) }),
                            '학생 공유 링크에 사유 포함', el('span.hint', '(기본: 빼기 — 링크가 다른 사람에게 전달될 수 있어서)')),
                        el('div.row',
                            el('button.btn.primary', { onclick: guarded(() => save(true)) }, editing ? '💾 저장하고 인쇄' : '🖨 발급하고 인쇄'),
                            el('button.btn', { onclick: guarded(() => save(false)) }, editing ? '저장' : '발급만'))),
                    preview));
            redraw();
        },
    });
}

// ---------------------------------------------------------------- 발급 기록 화면
export function openPass(p, refresh = false) {
    openTab({
        id: `pass-${p.id}`, refresh, title: `🎫 ${p.student_name} ${KIND[p.kind]}`,
        render(pane, tab) {
            const url = shareUrl(p);
            const qrBox = el('div.qr-box');
            const update = async change => {
                const saved = await api.patch(`/api/passes/${p.id}`, change);
                Object.assign(p, saved);
                await loadDay();
                refreshSide();
                emit('data-changed');
                tab.rerender();
            };
            const statusText = { issued: '발급됨', returned: `복귀 확인 (${(p.returned_at || '').slice(11, 16)})`, cancelled: '발급 취소됨' }[p.status];
            pane.append(
                el('h1', `${KIND[p.kind]}증 · ${p.student_name}`),
                el('p.sub', `발급 번호 ${p.serial} · ${dayLabel(p.date)} · `, el('b', statusText)),
                el('div.row',
                    el('button.btn.primary', { onclick: guarded(() => printPass(p, 2)) }, '🖨 인쇄 (2장)'),
                    el('button.btn', { onclick: guarded(() => printPass(p, 1)) }, '1장만'),
                    p.kind === 'out' && p.status === 'issued' && el('button.btn', { onclick: guarded(() => update({ status: 'returned' })) }, '✔ 복귀 확인'),
                    p.status !== 'issued' && el('button.btn', { onclick: guarded(() => update({ status: 'issued' })) }, '↺ 발급 상태로'),
                    el('button.btn', { onclick: () => { tab.close(); openForm({ ...p }); } }, '✎ 고치기'),
                    p.status === 'issued' && el('button.btn.danger', { onclick: guarded(async () => {
                        if (confirm('이 증의 발급을 취소할까요? (기록은 남습니다)')) await update({ status: 'cancelled' });
                    }) }, '발급 취소')),
                el('h2', '📱 학생에게 공유'),
                url ? el('div',
                    el('p.small.muted', 'QR 을 학생 폰으로 찍게 하거나 링크를 보내세요. 증 내용은 링크 안에만 있고 서버에 저장되지 않습니다.' +
                        (p.share_reason ? '' : ' (사유는 빠져 있습니다)')),
                    qrBox,
                    el('div.row', { style: 'margin-top: 8px' },
                        el('button.btn', { onclick: guarded(async () => { await navigator.clipboard.writeText(url); toast('링크를 복사했습니다'); }) }, '🔗 링크 복사'),
                        el('a.btn', { href: `/pass/#${url.split('#')[1]}`, target: '_blank', rel: 'noopener' }, '👀 학생 화면 미리보기')))
                    : el('p.muted', '⚙ 설정에서 공유 페이지 주소를 입력하면 QR 이 만들어집니다.'),
                el('h2', '미리보기'),
                el('div.preview-box', slipNode(p, null, null)));
            if (url) {
                qrBox.append(el('span.muted', 'QR 만드는 중…'));
                qrDataUrl(url).then(src => {
                    clear(qrBox, src ? el('img', { src, alt: '공유 QR 코드' }) : el('span.muted', 'QR 을 만들 수 없습니다 (인터넷 연결 확인). 링크 복사를 쓰세요.'),
                        el('div.linkbox', url));
                });
            }
        },
    });
}

// ---------------------------------------------------------------- 학생 명단
export function openRoster() {
    openTab({
        id: 'roster', title: '👥 학생 명단',
        render(pane, tab) {
            let parsed = null;
            const area = el('textarea', { rows: 12, placeholder: '엑셀에서 번호·이름 두 칸을 복사해 붙여넣으세요\n예)\n1\t김가온\n2\t나하늘' });
            const previewBox = el('div');
            const showPreview = guarded(async () => {
                parsed = await api.post('/api/students/parse', { text: area.value });
                clear(previewBox,
                    el('p', `${parsed.length}명을 읽었습니다. 맞으면 저장하세요 (기존 명단을 바꿉니다).`),
                    el('table.tbl', el('thead', el('tr', el('th', '번호'), el('th', '이름'))),
                        el('tbody', parsed.map(r => el('tr', el('td', r.number), el('td', r.name))))),
                    el('div.row', { style: 'margin-top: 8px' }, el('button.btn.primary', { disabled: !parsed.length, onclick: guarded(async () => {
                        await api.put('/api/students', { rows: parsed });
                        await loadStudents();
                        toast(`명단 ${parsed.length}명 저장`);
                        tab.rerender();
                        refreshSide();
                    }) }, '💾 명단 저장')));
            });
            const s = store.settings;
            pane.append(
                el('h1', '학생 명단'),
                el('p.sub', `${[s.school_name, s.grade && s.grade + '학년', s.class_no && s.class_no + '반'].filter(Boolean).join(' ') || '학교·학급 정보는 ⚙ 설정에서 입력하세요'}`),
                el('div.field', el('span', '붙여넣기'), area),
                el('div.row', el('button.btn', { onclick: showPreview }, '읽어 보기')),
                previewBox,
                el('h2', `지금 명단 (${store.students.length}명)`),
                store.students.length
                    ? el('table.tbl', el('tbody', store.students.map(r => el('tr', el('td', `${r.number}번`), el('td', r.name)))))
                    : el('p.muted', '아직 없습니다.'));
        },
    });
}

registerView({
    id: 'passes', icon: '🎫', label: '조퇴·외출',
    async renderSide(body, actions) { renderSide(body, actions); },
});

on('students', () => refreshSide());

export async function initPasses() {
    await loadDay();
}

