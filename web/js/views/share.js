// 학교 공통 설정 파일 (0.5.92): 다른 선생님께 업무 데스크를 나눠 줄 때 학교 공통값만 한 파일로 주고받기
// - 내보내기: 담기는 것 목록을 보여 주고 고른 것만. 학생·비밀값·개인 PC 값은 서버가 아예 담지 않음 (desk/schoolshare.py)
// - 불러오기: 파일 → 미리보기(무엇이 채워지는지·이미 있는 값) → 확인 후 적용 (이미 있는 값은 고른 것만 덮어씀)
import { api } from '../api.js';
import { el, clear, toast, guarded, modal, download } from '../ui.js';
import { loadState } from '../store.js';
import { emit } from '../shell.js';

const MAX_BYTES = 5 * 1024 * 1024;

function partLine(key, st) {
    const s = st[key] || {};
    if (key === 'school') return s.has ? [s.name, s.neis && `나이스: ${s.neis}`].filter(Boolean).join(' · ') : '학교 이름이 없습니다';
    if (key === 'periods') return s.has ? `${s.count}교시` : '교시 시간이 없습니다 (시간표 → 교시 시간)';
    if (key === 'schoolcal') return s.has ? `${s.years.join('·')}학년도 ${s.count}건` : '학사일정이 없습니다 (달력 → 학사일정 가져오기)';
    return s.has ? `선생님 ${s.teachers}명 · 학급 ${s.classes}개` : '시간표가 없습니다 (시간표 → 엑셀 불러오기)';
}

export const openShareExport = guarded(async () => {
    const st = await api.get('/api/share');
    const keys = Object.keys(st.labels);
    const boxes = {};
    const list = el('div.share-list', keys.map(k => {
        boxes[k] = el('input', { type: 'checkbox', checked: st[k]?.has, disabled: !st[k]?.has });
        return el('label.check.share-row', { className: `check share-row${st[k]?.has ? '' : ' off'}` }, boxes[k],
            el('span', el('b', st.labels[k]), el('span.hint', partLine(k, st))));
    }));
    const go = el('button.btn.primary', { type: 'button', onclick: guarded(async () => {
        const parts = keys.filter(k => boxes[k].checked);
        if (!parts.length) { toast('담을 항목을 하나 이상 고르세요', 'error'); return; }
        go.disabled = true;
        try {
            const r = await api.post('/api/share/export', { parts });
            download(new Blob([JSON.stringify(r.data, null, 2)], { type: 'application/json' }), r.filename);
            toast(`${r.filename} 을 받았습니다. 다른 선생님께 이 파일과 업무 데스크 ZIP 을 함께 드리세요.`, 'info', 8000);
            m.close();
        } finally {
            go.disabled = false;
        }
    }) }, '파일로 내보내기');
    const m = modal({
        title: '학교 공통 설정 내보내기',
        body: el('div.share-body',
            el('p', '같은 학교 선생님께 업무 데스크를 나눠 줄 때 쓰는 파일입니다. 받은 선생님은 설정 도우미 첫 단계에서 불러오면 됩니다.'),
            el('h3', '담기는 것 (고르세요)'), list,
            el('h3', '담기지 않는 것 (언제나)'),
            el('ul.small.share-never', st.never.map(t => el('li', t))),
            el('div.row', { style: 'justify-content: flex-end; margin-top: 12px' }, go)),
    });
});

// 파일 고르기 → 미리보기 → 적용. onDone(결과) 은 적용한 뒤 부름 (도우미가 다음 단계로 넘어가게)
export function openShareImport(onDone) {
    const body = el('div.share-body');
    const pick = el('input', { type: 'file', accept: '.json,.deskshare.json,application/json' });
    let m;
    const showPreview = (text, pv) => {
        const use = {}, over = {};
        clear(body,
            el('p', pv.school_name ? `${pv.school_name} 공통 설정` : '학교 공통 설정', pv.created_at ? ` · ${pv.created_at} 에 만든 파일` : ''),
            el('h3', '채워지는 것'),
            el('div.share-list', pv.items.map(it => {
                use[it.key] = el('input', { type: 'checkbox', checked: true });
                over[it.key] = it.exists ? el('input', { type: 'checkbox' }) : null;
                return el('div.share-row',
                    el('label.check', use[it.key], el('span', el('b', it.label), el('span.hint', it.what))),
                    it.exists && el('label.check.share-over', over[it.key],
                        el('span', `이미 있음 (${it.now}) — 파일 내용으로 바꾸기`, el('span.hint', '체크하지 않으면 지금 값을 그대로 둡니다.'))));
            })),
            el('p.small.muted', '학생 명단·비밀값(인증키·토큰)·쿨메신저 폴더 같은 개인 값은 이 파일에 없으니 직접 넣으세요.'),
            el('div.row', { style: 'justify-content: flex-end; margin-top: 12px' },
                el('button.btn', { type: 'button', onclick: () => m.close() }, '취소'),
                el('button.btn.primary', { type: 'button', onclick: guarded(async ev => {
                    const parts = pv.items.filter(it => use[it.key].checked).map(it => it.key);
                    if (!parts.length) { toast('불러올 항목을 하나 이상 고르세요', 'error'); return; }
                    ev.target.disabled = true;
                    try {
                        const res = await api.post('/api/share/apply', {
                            text, parts, overwrite: pv.items.filter(it => over[it.key]?.checked).map(it => it.key),
                        });
                        await loadState();
                        emit('data-changed');
                        const label = k => pv.items.find(it => it.key === k)?.label || k;
                        toast(res.applied.length
                            ? `불러왔습니다: ${res.applied.map(label).join(', ')}` + (res.skipped.length ? ` (그대로 둔 것: ${res.skipped.map(label).join(', ')})` : '')
                            : '바뀐 것이 없습니다 (이미 있는 값은 그대로 두었습니다)', 'info', 8000);
                        m.close();
                        onDone?.(res);
                    } finally {
                        ev.target.disabled = false;
                    }
                }) }, '불러오기')));
    };
    pick.onchange = guarded(async () => {
        const f = pick.files?.[0];
        if (!f) return;
        if (f.size > MAX_BYTES) { toast('파일이 너무 큽니다 (5MB 까지)', 'error'); return; }
        const text = await f.text();
        try {
            showPreview(text, await api.post('/api/share/preview', { text }));
        } catch (e) {
            clear(body, el('p.set-bad', e.message), el('p.small', '다른 파일을 고르려면 창을 닫고 다시 여세요.'));
        }
    });
    clear(body,
        el('p', '같은 학교 선생님께 받은 "…-공통설정.deskshare.json" 파일을 고르세요. 무엇이 채워지는지 먼저 보여 드리고, 확인을 누를 때만 적용합니다.'),
        el('label.field', el('span', '파일'), pick));
    m = modal({ title: '학교 공통 설정 불러오기', body });
    return m;
}

// 도우미·설정에서 쓰는 '이미 있는 것' (시간표·학사일정·교시 시간) — 실패해도 빈 값
export async function shareStatus() {
    try { return await api.get('/api/share'); } catch { return null; }
}

