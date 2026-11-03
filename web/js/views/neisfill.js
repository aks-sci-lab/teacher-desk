// ✍ 나이스 입력 (시험 단계): 확장 프로그램이 보낸 나이스 입력 칸 구조(값 없음)에 값을 정해 [나이스에 채우기]
// 나이스 탭이 열린 브라우저의 확장 프로그램이 요청을 받아, 화면 위에서 확인을 물은 뒤 칸을 채움. 저장은 선생님이 직접.
import { api } from '../api.js';
import { el, clear, guarded, toast } from '../ui.js';
import { registerView, openTab, findTab } from '../shell.js';

let selected = null;                 // 고른 양식 id
const drafts = {};                   // 양식별로 입력 중인 값 (화면을 다시 그려도 남게)
const STATUS = { pending: '확장 프로그램이 가져가기를 기다리는 중', taken: '나이스 화면에서 확인을 기다리는 중', done: '채움', failed: '못 채움' };

function fieldInput(f, values) {
    const v = values[f.key] ?? '';
    const set = val => { values[f.key] = val; };
    if (f.type === 'select') {
        return el('select', { onchange: e => set(e.target.value) },
            el('option', { value: '' }, '(그대로 둠)'),
            f.options.map(o => el('option', { value: o, selected: o === v }, o)));
    }
    if (f.type === 'checkbox' || f.type === 'radio') {
        return el('select', { onchange: e => set(e.target.value) },
            [['', '(그대로 둠)'], ['선택', '선택'], ['해제', '해제']].map(([val, l]) => el('option', { value: val, selected: val === v }, l)));
    }
    if (f.type === 'textarea' || f.type === 'editable') {
        return el('textarea', { rows: 3, value: v, oninput: e => set(e.target.value) });
    }
    return el('input', { type: f.type === 'date' ? 'date' : f.type === 'number' ? 'number' : 'text', value: v, oninput: e => set(e.target.value) });
}

function jobLine(j) {
    const r = j.result;
    return el('li', el('span.ttl', `${j.created_at.slice(5)} · ${j.title} · ${j.entries.length}칸`),
        el('span.meta', el('b', { style: `color: var(${j.status === 'done' ? '--ok' : j.status === 'failed' ? '--danger' : '--warn'})` }, STATUS[j.status] || j.status),
            r && ` · ${r.filled}칸 채움`, r && r.missing.length ? ` · 못 찾음: ${r.missing.join(', ')}` : '', r && r.error ? ` · ${r.error}` : ''));
}

function renderNeis(pane, tab) {
    guarded(async () => {
        const st = await api.get('/api/neis-fill');
        clear(pane);
        const form = st.forms.find(f => f.id === selected) || st.forms[0];
        selected = form?.id || null;
        const busy = st.jobs.some(j => j.status === 'pending' || j.status === 'taken');
        if (busy) setTimeout(() => { if (tab.pane.isConnected) tab.rerender(); }, 3000);   // 결과가 오면 바로 보이게
        pane.append(...[   // 화면 요소만 (조건이 거짓인 것은 뺌)
            el('h1', '나이스 입력'),
            el('p.sub', '시험 단계 · 확장 프로그램이 보낸 나이스 화면의 입력 칸에 넣을 값을 정하고 [나이스에 채우기]를 누르면, 나이스 화면에 확인 창이 뜨고 칸이 채워집니다. 저장 단추는 누르지 않으니 나이스에서 확인하고 직접 저장하세요.'),
            !st.forms.length && el('div.card', { style: 'padding: 16px' },
                el('ol.small', { style: 'margin: 0; padding-left: 1.2em' },
                    el('li', '⚙ 설정의 "브라우저 단축키로 화면 보내기" 연결과 확장 프로그램(0.3.0 이상)을 먼저 준비합니다.'),
                    el('li', '나이스에서 값을 넣을 화면을 엽니다.'),
                    el('li', '확장 프로그램 아이콘 → [입력 칸 구조 보내기 (값 없음)]. 입력 칸의 이름표·종류·선택지만 오고, 입력된 값이나 학생 정보는 오지 않습니다.'),
                    el('li', '여기로 돌아오면 그 화면의 칸이 보입니다.'))),
            st.forms.length > 0 && el('div.row', { style: 'flex-wrap: wrap; gap: 6px; margin-bottom: 12px' },
                st.forms.map(f => el('button', { className: `btn small${f.id === selected ? ' primary' : ''}`, onclick: () => { selected = f.id; tab.rerender(); } },
                    `${f.title} (${f.fields.length}칸)`))),
            form && formBox(form, tab),
            st.jobs.length > 0 && el('section.card', { style: 'padding: 12px 16px; margin-top: 16px' },
                el('h3', '최근 채우기'), el('ul.list.compact', st.jobs.slice(0, 8).map(jobLine))),
        ].filter(Boolean));
    })();
}

function formBox(form, tab) {
    const values = drafts[form.id] || (drafts[form.id] = {});
    const send = guarded(async () => {
        const v = {};
        for (const [k, val] of Object.entries(values)) {
            if (val === '' || val == null) continue;
            v[k] = val === '선택' ? true : val === '해제' ? false : val;
        }
        await api.post('/api/neis-fill/jobs', { form_id: form.id, values: v });
        toast('채우기 요청을 보냈습니다. 나이스 화면에 확인 창이 뜨면 [확인]을 누르세요 (30초 안쪽, 바로 하려면 나이스에서 Alt+Shift+F)', 'info', 8000);
        tab.rerender();
    });
    return el('section.card', { style: 'padding: 12px 16px' },
        el('div.row', el('h3', { style: 'margin: 0' }, form.title), el('span.spacer'),
            el('span.small.muted', `${form.host}${form.frame_path} · 구조 받은 때 ${form.captured_at}`),
            el('button.btn.small.danger', { onclick: guarded(async () => {
                if (!confirm('이 양식을 지울까요? (나이스 화면에서 구조를 다시 보내면 다시 생깁니다)')) return;
                await api.del(`/api/neis-fill/forms/${form.id}`);
                selected = null;
                tab.rerender();
            }) }, '양식 지우기')),
        el('p.small.muted', '비워 둔 칸은 나이스에서 그대로 둡니다. 이름표가 이상하면 칸 위치(작은 글씨)를 보고 고르세요.'),
        el('table.tbl', el('tbody', form.fields.map(f => el('tr',
            el('th', { style: 'width: 34%' }, f.label, el('div.small.muted', { style: 'font-weight: 400; word-break: break-all' }, `${f.type} · ${f.key}`)),
            el('td', fieldInput(f, values)))))),
        el('div.row', { style: 'margin-top: 12px' },
            el('button.btn.primary', { onclick: send }, '나이스에 채우기'),
            el('button.btn', { onclick: () => { drafts[form.id] = {}; tab.rerender(); } }, '값 비우기')));
}

export function openNeisFill() {
    openTab({ id: 'neis-fill', title: '나이스 입력', menu: true, refresh: true, render: renderNeis });
}

registerView({ id: 'neis-fill', icon: '✍', label: '나이스 입력', noSide: true, renderSide() {}, open: openNeisFill });
