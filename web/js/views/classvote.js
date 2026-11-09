// 학급 투표 (0.5.81): 교실 PC 화면(TV)에 띄우고 학생이 한 명씩 나와 비밀 투표 · 손들기·종이 결과 직접 입력
// 인터넷·학생 폰 없이 이 PC 안에서만. 누가 무엇을 골랐는지는 저장하지 않음 (서버 desk/classvote.py).
import { api } from '../api.js';
import { el, clear, guarded, toast, longDate, todayIso } from '../ui.js';
import { refreshSide, findTab } from '../shell.js';
import { store } from '../store.js';

let selected = null;      // 고른 투표 id (null: 새 투표 만들기)
let votes = [];
let names = new Map();
const rerender = () => findTab('classroom')?.rerender();
const setStudents = list => { names = new Map((list || []).map(s => [s.number, s.name])); };
const MODE = { secret: '교실 화면 비밀 투표', manual: '손들기·종이 결과 입력' };

async function loadList() {
    const r = await api.get('/api/class/votes');
    votes = r.votes;
    setStudents(r.students);
}

export function renderVoteSide(body) {
    body.append(el('div.row.cl-side-p', el('button.btn.primary.small', { onclick: () => { selected = null; refreshSide(); rerender(); } }, '+ 새 투표')));
    if (!votes.length) { body.append(el('div.empty', '아직 투표가 없습니다')); return; }
    body.append(el('ul.list', votes.map(v => el('li', {
        className: v.id === selected ? 'on' : '', onclick: () => { selected = v.id; refreshSide(); rerender(); },
    }, el('span.ttl', v.title), el('span.meta', v.status === 'open' ? (v.mode === 'secret' ? `투표 중 · ${v.voted}명` : '결과 입력 전') : '끝남',
        ` · ${v.created_at.slice(5, 16)}`)))));
}

export function renderVotes(pane) {
    const body = el('div', el('p.muted', '불러오는 중…'));
    pane.append(body);
    guarded(async () => {
        await loadList();
        refreshSide();   // 왼쪽 목록에 투표 목록·진행 수
        if (selected != null && !votes.some(v => v.id === selected)) selected = null;
        if (selected == null) return clear(body, newVoteForm());
        const v = await api.get(`/api/class/votes/${selected}`);
        setStudents(v.students);
        clear(body, voteDetail(v));
    })();
}

// ---------------------------------------------------------------- 새 투표 / 선택지 고치기
function newVoteForm(edit = null) {
    const title = el('input', { type: 'text', value: edit?.title || '', placeholder: '예: 2학기 반장 선거, 체험학습 장소', maxLength: 60 });
    const opts = el('textarea', { rows: 6, value: (edit?.options || []).join('\n'), placeholder: '한 줄에 하나씩\n예) 놀이공원\n과학관\n박물관' });
    const pick = el('input', { type: 'text', placeholder: '예: 3, 7, 12', style: 'width: 16em' });
    const abstain = el('input', { type: 'checkbox', checked: !!edit?.abstain });
    let mode = edit?.mode || 'secret';
    const modeSeg = el('span.seg', Object.entries(MODE).map(([k, label]) => el('button', {
        type: 'button', className: k === mode ? 'on' : '', onclick: e => {
            mode = k;
            for (const b of modeSeg.children) b.classList.toggle('on', b === e.currentTarget);
        },
    }, label)));
    const save = guarded(async () => {
        const options = opts.value.split('\n').map(s => s.trim()).filter(Boolean);
        const data = { title: title.value, options, mode, abstain: abstain.checked };
        const v = edit ? await api.patch(`/api/class/votes/${edit.id}`, data) : await api.post('/api/class/votes', data);
        selected = v.id;
        toast(edit ? '고쳤습니다' : '투표를 만들었습니다');
        refreshSide();
        rerender();
    });
    return el('div.cl-vote-form',
        el('div.cl-head', el('h1', edit ? '투표 고치기' : '새 투표'),
            el('span.muted', '교실 PC 화면(TV)에서 학생이 한 명씩 나와 투표합니다. 인터넷·학생 폰은 쓰지 않습니다.')),
        el('div.card',
            el('label.field', el('span', '제목'), title),
            el('div.field', el('span', '방식'), modeSeg,
                el('span.hint', '비밀 투표: 학생이 화면에서 자기 번호를 누르고 고름(누른 뒤 화면이 가려짐) · 직접 입력: 손들기·종이로 센 표 수를 넣음')),
            el('label.field', el('span', '후보·선택지 (한 줄에 하나, 2~30개)'), opts),
            el('div.field', el('span', '학생 명단에서 후보 넣기 (번호)'),
                el('div.row', pick, el('button.btn.small', { type: 'button', onclick: () => {
                    const have = new Set(opts.value.split('\n').map(s => s.trim()).filter(Boolean));
                    const add = pick.value.split(/[,\s]+/).map(Number).filter(n => names.has(n)).map(n => names.get(n)).filter(n => !have.has(n));
                    if (!add.length) return toast('명단에 있는 번호를 넣으세요', 'error');
                    opts.value = [...have, ...add].join('\n');
                    pick.value = '';
                } }, '넣기'))),
            el('label.check', abstain, '\'기권\' 칸 두기', el('span.hint', '기권 표는 1등 계산에서 뺍니다')),
            el('div.row', el('button.btn.primary', { onclick: save }, edit ? '저장' : '투표 만들기'),
                edit && el('button.btn', { onclick: rerender }, '취소'))));
}

// ---------------------------------------------------------------- 투표 하나 보기
function voteDetail(v) {
    const post = (path, ask) => guarded(async () => {
        if (ask && !confirm(ask)) return;
        const r = await api.post(`/api/class/votes/${v.id}/${path}`, {});
        if (r.id && r.id !== v.id) selected = r.id;   // 결선 투표로 이동
        refreshSide();
        rerender();
    });
    const head = el('div.cl-head', el('h1', v.title),
        el('span.muted', `${MODE[v.mode]} · ${v.status === 'open' ? '진행 중' : `끝남 ${v.closed_at?.slice(5, 16) || ''}`}`),
        el('span.spacer'),
        el('button.btn.danger.small', { onclick: guarded(async () => {
            if (!confirm(`'${v.title}' 투표를 지울까요? 결과도 함께 지워집니다.`)) return;
            await api.del(`/api/class/votes/${v.id}`);
            selected = null;
            refreshSide();
            rerender();
        }) }, '지우기'));
    const optionList = el('ol.cl-vote-opts', v.options.map(o => el('li', o)), v.abstain && el('li.muted', '기권'));
    if (v.status === 'closed') return el('div', head, resultView(v, post));
    if (v.mode === 'manual') return el('div', head, manualForm(v));
    const voted = new Set(v.voters);
    const all = [...names.keys()];
    return el('div', head,
        el('div.cl-vote-now',
            el('div.card', el('h3', '후보·선택지'), optionList,
                !v.voters.length && el('button.btn.small', { onclick: () => clear(findTab('classroom').pane, newVoteForm(v)) }, '선택지 고치기')),
            el('div.card', el('h3', `투표한 학생 ${v.voters.length} / ${all.length}명`),
                el('div.cl-vote-grid.small-grid', all.map(n => el('span', { className: voted.has(n) ? 'done' : '', title: names.get(n) }, n))),
                el('p.small.muted', '누가 무엇을 골랐는지는 저장하지 않습니다. 투표가 끝날 때까지 몇 표인지도 보이지 않습니다.'))),
        el('div.row.cl-tools',
            el('button.btn.primary', { onclick: () => castScreen(v) }, '▶ 교실 화면에서 투표 받기'),
            el('button.btn', { onclick: post('close', v.voters.length < all.length ? `아직 ${all.length - v.voters.length}명이 투표하지 않았습니다. 끝내고 결과를 볼까요?` : null) }, '투표 끝내고 결과 보기'),
            el('button.btn', { disabled: !v.voters.length, onclick: post('reset', '모든 표를 지우고 처음부터 다시 할까요?') }, '처음부터 다시')),
        el('p.small.muted', '[교실 화면에서 투표 받기]를 누르면 화면 전체가 투표 화면이 됩니다. 학생이 자기 번호 → 이름 확인 → 선택 → 한 번 더 확인 순서로 누르고, 누른 뒤에는 화면이 가려집니다. 선생님은 오른쪽 위 [선생님: 끝내기] 또는 Esc 로 나옵니다.'));
}

function manualForm(v) {
    const labels = [...v.options, ...(v.abstain ? ['기권'] : [])];
    const inputs = labels.map(() => el('input.cl-num', { type: 'number', min: 0, max: 1000, value: 0 }));
    const sum = el('b', '0');
    const update = () => { sum.textContent = inputs.reduce((a, i) => a + (+i.value || 0), 0); };
    inputs.forEach(i => i.addEventListener('input', update));
    return el('div.card.cl-vote-manual',
        el('h3', '손들기·종이 투표로 센 표 수'),
        el('table.cl-table', el('tbody', labels.map((o, k) => el('tr', el('th', o), el('td', inputs[k], ' 표'))),
            el('tr.rest', el('th', '합계'), el('td', sum, ' 표')))),
        el('div.row', { style: 'margin-top: 12px' }, el('button.btn.primary', { onclick: guarded(async () => {
            await api.post(`/api/class/votes/${v.id}/tally`, { counts: inputs.map(i => Math.max(0, Math.floor(+i.value || 0))) });
            refreshSide();
            rerender();
        }) }, '결과 저장'),
        !v.voters?.length && el('button.btn', { onclick: () => clear(findTab('classroom').pane, newVoteForm(v)) }, '선택지 고치기')));
}

// ---------------------------------------------------------------- 결과 (막대그래프·동점)
function bars(v, big = false) {
    const r = v.result;
    const max = Math.max(1, ...r.rows.map(x => x.count));
    const win = new Set(v.drawn ? [v.drawn.winner] : r.winners);
    return el('div', { className: `cl-bars${big ? ' big' : ''}` }, r.rows.map(x => el('div', {
        className: `cl-bar${win.has(x.option) && !(r.tie && !v.drawn) ? ' win' : ''}${r.tie && r.winners.includes(x.option) && !v.drawn ? ' tie' : ''}${x.option === '기권' && v.abstain ? ' abstain' : ''}`,
    }, el('span.lbl', x.option), el('span.track', el('span.fill', { style: `width: ${(100 * x.count / max).toFixed(1)}%` })),
    el('span.val', `${x.count}표`, el('small', ` ${x.pct}%`)))));
}

function verdict(v) {
    const r = v.result;
    if (!r.total) return '표가 없습니다';
    if (v.drawn) return `동점(${v.drawn.from.join(' · ')})이라 제비뽑기로 '${v.drawn.winner}' 이(가) 뽑혔습니다`;
    if (r.tie) return `동점: ${r.winners.join(' · ')} (각 ${r.rows.find(x => x.option === r.winners[0]).count}표)`;
    return `1등: ${r.winners[0]}`;
}

function resultView(v, post) {
    const r = v.result;
    const t = [store.settings?.grade && `${store.settings.grade}학년`, store.settings?.class_no && `${store.settings.class_no}반`].filter(Boolean).join(' ');
    return el('div',
        el('div.card.cl-result',
            el('h2', { className: `cl-verdict${r.tie && !v.drawn ? ' tie' : ''}` }, verdict(v)),
            el('p.muted.small', `전체 ${r.total}표${v.mode === 'secret' ? ` · 투표한 학생 ${v.voters.length}명 / ${v.eligible}명` : ''}`),
            bars(v)),
        el('div.row.cl-tools',
            el('button.btn.primary', { onclick: () => resultScreen(v) }, '▶ 교실 화면에 결과 보이기'),
            r.tie && !v.drawn && el('button.btn', { onclick: post('runoff', `동점인 ${r.winners.join(' · ')} 만으로 결선 투표를 새로 만들까요?`) }, '동점자 결선 투표'),
            r.tie && !v.drawn && el('button.btn', { onclick: post('draw', '동점인 것 중 하나를 제비뽑기로 뽑을까요? (뽑은 결과가 남습니다)') }, '제비뽑기'),
            v.mode === 'secret' && el('button.btn', { onclick: post('reopen', '투표를 다시 받을까요? 지금까지 표는 그대로 이어집니다.\n주의: 결과를 이미 보았으므로, 다시 끝냈을 때 늦게 투표한 학생이 무엇을 골랐는지 짐작할 수 있습니다. 비밀이 중요하면 [처음부터 다시]를 쓰세요.') }, '투표 다시 받기'),
            v.mode === 'manual' && el('button.btn', { onclick: post('reset', '넣은 표 수를 지우고 다시 넣을까요?') }, '표 수 다시 넣기'),
            el('span.spacer'),
            el('button.btn', { onclick: () => {
                const area = document.getElementById('print-area');
                clear(area, el('div.cl-print', el('h1', `${t ? t + ' ' : ''}${v.title}`, el('small', ` ${MODE[v.mode]}`)),
                    el('h2', verdict(v)), bars(v), el('p.cl-print-foot', `${longDate(todayIso())} · 전체 ${r.total}표`)));
                window.print();
            } }, '결과 인쇄')));
}

// ---------------------------------------------------------------- 교실 화면 (전체 화면 덮개)
function overlay(onExit) {
    const box = el('div.cl-cast', { role: 'dialog', 'aria-modal': 'true', tabIndex: -1 });
    const stage = el('div.cl-cast-stage');
    const exit = () => {
        document.removeEventListener('keydown', onKey, true);
        box.remove();
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
        onExit?.();
    };
    const onKey = e => {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (confirm('선생님: 투표 화면을 닫을까요?')) exit(); }
    };
    box.append(el('button.cl-cast-exit', { type: 'button', onclick: () => { if (confirm('선생님: 투표 화면을 닫을까요?')) exit(); } }, '선생님: 끝내기'), stage);
    document.body.append(box);
    document.addEventListener('keydown', onKey, true);
    document.documentElement.requestFullscreen?.().catch(() => {});   // 안 되면 창 안에서만 덮음
    box.focus();
    return { stage, exit };
}

function castScreen(v0) {
    let v = v0;
    const all = [...names.keys()];
    const { stage, exit } = overlay(() => rerender());
    const title = () => el('div.cl-cast-title', v.title);
    const show = (...nodes) => clear(stage, title(), ...nodes);
    const voted = new Set(v.voters);

    function pickNumber() {
        if (voted.size >= all.length) {
            return show(el('h2', '모두 투표했습니다 🎉'), el('p', '선생님이 결과를 확인합니다.'),
                el('button.cl-big', { onclick: exit }, '투표 화면 닫기'));
        }
        show(el('h2', '자기 번호를 누르세요'),
            el('p.cl-cast-sub', `${voted.size} / ${all.length}명 투표함`),
            el('div.cl-vote-grid', all.map(n => el('button', {
                type: 'button', disabled: voted.has(n), className: voted.has(n) ? 'done' : '',
                onclick: () => confirmWho(n),
            }, voted.has(n) ? `${n} ✓` : n))));
    }
    function confirmWho(n) {
        show(el('h2', `${n}번 ${names.get(n) || ''} 학생 맞나요?`),
            el('div.cl-cast-row', el('button.cl-big.primary', { onclick: () => choose(n) }, '네, 맞아요'),
                el('button.cl-big', { onclick: pickNumber }, '아니에요')));
    }
    function choose(n) {
        const labels = [...v.options, ...(v.abstain ? ['기권'] : [])];
        show(el('h2', '하나를 고르세요'), el('p.cl-cast-sub', `${n}번 ${names.get(n) || ''}`),
            el('div.cl-cast-opts', labels.map((o, k) => el('button', { type: 'button', className: o === '기권' && v.abstain && k === labels.length - 1 ? 'abstain' : '', onclick: () => confirmChoice(n, k, o) }, o))));
    }
    function confirmChoice(n, k, label) {
        show(el('h2', `'${label}'(으)로 투표할까요?`), el('p.cl-cast-sub', '누르면 바꿀 수 없습니다'),
            el('div.cl-cast-row',
                el('button.cl-big.primary', { onclick: guarded(async () => {
                    stage.textContent = '';   // 고른 것이 화면에 남지 않게 바로 가림
                    try {
                        await api.post(`/api/class/votes/${v.id}/ballot`, { number: n, choice: k });
                    } catch (e) {
                        show(el('h2', '투표하지 못했습니다'), el('p.cl-cast-sub', e.message), el('button.cl-big', { onclick: pickNumber }, '처음으로'));
                        return;
                    }
                    voted.add(n);
                    done();
                }) }, '투표하기'),
                el('button.cl-big', { onclick: () => choose(n) }, '다시 고르기')));
    }
    function done() {
        show(el('h2', '투표했습니다 ✔'), el('p.cl-cast-sub', '자리로 돌아가고, 다음 사람을 불러 주세요.'),
            el('button.cl-big.primary', { onclick: pickNumber }, '다음 사람'));
    }
    pickNumber();
}

function resultScreen(v) {
    const { stage } = overlay(() => rerender());
    clear(stage, el('div.cl-cast-title', v.title), el('h2', verdict(v)), bars(v, true));
}
