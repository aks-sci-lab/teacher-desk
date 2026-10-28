// 📥 학사일정 가져오기: 파일(엑셀·한글·PDF·CSV·글) / 붙여넣기 / 나이스 → 미리보기에서 고치기 → 달력에 넣기
import { api } from '../api.js';
import { el, clear, toast, guarded, catBadge, CATEGORIES } from '../ui.js';
import { openTab, emit } from '../shell.js';
import { store, saveSettings, loadState } from '../store.js';

const MODE_NAMES = { grid: '달력표', list: '목록', text: '글', neis: '나이스' };

function defaultYear() {
    const d = new Date();
    return d.getMonth() + 1 >= 3 ? d.getFullYear() : d.getFullYear() - 1;
}

function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).split(',', 2)[1] || '');
        r.onerror = () => reject(new Error('파일을 읽을 수 없습니다'));
        r.readAsDataURL(file);
    });
}

export function openSchoolCal() {
    openTab({
        id: 'schoolcal', title: '학사일정 가져오기',
        render(pane) {
            let year = defaultYear();
            let source = 'file';
            let rows = [];       // 미리보기 일정 (선택 여부 포함)
            let origin = '';

            const yearSel = el('select', { onchange: e => { year = Number(e.target.value); clear(preview); } },
                [year - 1, year, year + 1].map(y => el('option', { value: y, selected: y === year }, `${y}학년도 (${y}.3 ~ ${y + 1}.2)`)));
            const preview = el('div');
            const sourceBox = el('div');

            const showPreview = r => {
                rows = r.events.map(e => ({ ...e, on: true }));
                const head = el('p', `${rows.length}건을 찾았습니다 · 읽은 방식: ${MODE_NAMES[r.mode] || r.mode}`
                    + (r.sheet ? ` · 시트 "${r.sheet}"` : '')
                    + (r.skipped ? ` · 학년도 밖 날짜 ${r.skipped}건 뺌` : ''));
                if (!rows.length) {
                    clear(preview, head, el('p.muted', '날짜가 있는 일정을 찾지 못했습니다. 다른 방법(붙여넣기·나이스)을 써 보시거나, 파일을 개발 담당에게 보내 주세요.'));
                    return;
                }
                const count = el('b');
                const update = () => { count.textContent = rows.filter(x => x.on).length; };
                const replace = el('input', { type: 'checkbox', checked: true });
                const table = el('table.tbl',
                    el('thead', el('tr',
                        el('th', el('input', { type: 'checkbox', checked: true, title: '모두 고르기', onchange: e => {
                            rows.forEach(x => { x.on = e.target.checked; });
                            table.querySelectorAll('tbody input[type=checkbox]').forEach(c => { c.checked = e.target.checked; });
                            update();
                        } })),
                        ['날짜', '끝 날짜', '내용', '분류'].map(h => el('th', h)))),
                    el('tbody', rows.map(x => el('tr',
                        el('td.c', el('input', { type: 'checkbox', checked: true, onchange: e => { x.on = e.target.checked; update(); } })),
                        el('td', el('input', { type: 'date', value: x.date, onchange: e => { x.date = e.target.value; } })),
                        el('td', el('input', { type: 'date', value: x.end_date || '', onchange: e => { x.end_date = e.target.value || null; } })),
                        el('td', el('input', { type: 'text', value: x.text, onchange: e => { x.text = e.target.value; } })),
                        el('td', el('select', { onchange: e => { x.category = e.target.value; } },
                            CATEGORIES.map(c => el('option', { value: c, selected: c === x.category }, c))))))));
                update();
                clear(preview,
                    el('h2', '미리보기'), head,
                    el('p.small.muted', '틀린 날짜·이름은 바로 고치고, 넣지 않을 줄은 체크를 끄세요.'),
                    el('div', { style: 'overflow-x: auto; max-height: 55vh' }, table),
                    el('label.check', { style: 'margin-top: 12px' }, replace, `${year}학년도에 전에 가져온 학사일정은 지우고 이것으로 바꾸기`),
                    el('div.row', el('button.btn.primary', { onclick: guarded(async () => {
                        const events = rows.filter(x => x.on).map(({ on, ...e }) => e);
                        if (!events.length) return toast('넣을 일정을 고르세요', 'error');
                        const res = await api.post('/api/schoolcal/save', { school_year: year, events, origin, replace: replace.checked });
                        toast(`학사일정 ${res.count}건을 달력에 넣었습니다${res.replaced ? ' (이전 것은 바꿈)' : ''}`);
                        emit('data-changed');
                    }) }, '', count, '건 달력에 넣기')));
            };

            const renderSource = () => {
                if (source === 'file') {
                    const input = el('input', { type: 'file', accept: '.xlsx,.csv,.hwp,.hwpx,.pdf,.txt,.html,.htm' });
                    input.addEventListener('change', guarded(async () => {
                        const f = input.files[0];
                        if (!f) return;
                        clear(preview, el('p.muted', '읽는 중…'));
                        origin = f.name;
                        try {
                            showPreview(await api.post('/api/schoolcal/preview', { school_year: year, filename: f.name, data: await fileToBase64(f) }));
                        } catch (e) {
                            clear(preview, el('p', { style: 'color: var(--danger)' }, e.message));
                        }
                    }));
                    clear(sourceBox,
                        el('p.small', '학교 홈페이지·업무포털에서 받은 학사일정 파일을 고르세요. 엑셀(.xlsx·.csv), 한글(.hwp·.hwpx), PDF, 글(.txt) 모두 됩니다.'),
                        el('p.small.muted', '달력 모양 표(머리줄에 3월·4월…, 옆에 1~31일), 날짜·행사 목록, 줄마다 날짜가 있는 글을 알아서 구별합니다.'),
                        input);
                } else if (source === 'paste') {
                    const area = el('textarea', { rows: 10, placeholder: '예)\n3. 2.(월) 입학식\n4월 22일 ~ 24일 1학기 중간고사\n7월 20일 ~ 8월 16일 여름방학\n\n엑셀에서 표를 복사해 붙여넣어도 됩니다.' });
                    clear(sourceBox, area, el('div.row', { style: 'margin-top: 8px' }, el('button.btn', { onclick: guarded(async () => {
                        origin = '붙여넣기';
                        showPreview(await api.post('/api/schoolcal/preview', { school_year: year, text: area.value }));
                    }) }, '읽어 보기')));
                } else {
                    const sch = store.settings.neis_school || {};
                    const results = el('div');
                    const q = el('input', { type: 'text', placeholder: '학교 이름 (예: 한빛고등학교)', value: sch.name || store.settings.school_name || '' });
                    clear(sourceBox,
                        el('p.small', '나이스에는 모든 학교의 학사일정이 있어 파일 양식과 상관없이 가져올 수 있습니다.'),
                        !store.settings.neis_key_set && el('p.small', { style: 'color: var(--warn)' },
                            '나이스 인증키가 없으면 시험용으로 몇 건만 받습니다. open.neis.go.kr 에서 무료 인증키를 받아 ⚙ 설정에 넣으세요.'),
                        el('div.row', q, el('button.btn', { onclick: guarded(async () => {
                            const list = await api.get(`/api/neis/schools?q=${encodeURIComponent(q.value)}`);
                            clear(results, list.length ? el('ul.list', list.map(s => el('li', { onclick: guarded(async () => {
                                await saveSettings({ neis_school: { name: s.name, atpt: s.atpt, code: s.code, office: s.office || '' } });
                                await loadState();
                                toast(`${s.name} 을(를) 골랐습니다`);
                                renderSource();
                            }) }, el('span.ttl', s.name), el('span.meta', s.office || '', s.address || '')))) : el('p.muted', '찾은 학교가 없습니다'));
                        }) }, '학교 찾기')),
                        results,
                        sch.code && el('p', '고른 학교: ', el('b', sch.name), ` (${sch.office || sch.atpt})`),
                        sch.code && el('button.btn', { onclick: guarded(async () => {
                            origin = `나이스 ${sch.name}`;
                            clear(preview, el('p.muted', '나이스에서 받는 중…'));
                            try {
                                const r = await api.post('/api/schoolcal/neis', { school_year: year });
                                showPreview(r);
                                if (r.sample) toast('인증키가 없어 일부만 받았습니다', 'error', 6000);
                            } catch (e) {
                                clear(preview, el('p', { style: 'color: var(--danger)' }, e.message));
                            }
                        }) }, '나이스 학사일정 불러오기'));
                }
            };

            const seg = el('div.seg', [['file', '파일'], ['paste', '붙여넣기'], ['neis', '나이스']].map(([k, label]) =>
                el('button', { className: k === source ? 'on' : '', onclick: e => {
                    source = k;
                    seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b === e.currentTarget));
                    clear(preview);
                    renderSource();
                } }, label)));

            pane.append(
                el('h1', '학사일정 가져오기'),
                el('p.sub', '학교마다 양식이 달라도 됩니다. 읽은 결과를 먼저 보여 드리고, 고친 뒤 달력에 넣습니다.'),
                el('div.grid2', el('label.field', el('span', '학년도'), yearSel), el('div.field', el('span', '가져올 곳'), seg)),
                el('section.card', sourceBox),
                preview,
                el('p.small.muted', { style: 'margin-top: 16px' }, '가져온 일정은 달력에 ', catBadge('학사'), ' 색으로 표시되고, 수신함에는 "학사일정" 항목 하나로 묶여 들어갑니다.'));
            renderSource();
        },
    });
}
