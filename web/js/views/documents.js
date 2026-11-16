// 📝 문서 작성 (0.6.1): 업무 폴더 색인(누를 때만) → 할 작업으로 관련 파일 찾기 → 새 문서 또는 학교 서식 그대로 채우기
import { api } from '../api.js';
import { el, clear, guarded, toast, download } from '../ui.js';
import { registerView, openTab } from '../shell.js';
import { saveSettings } from '../store.js';

let poll = null;

function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).split(',', 2)[1] || '');
        r.onerror = () => reject(new Error('파일을 읽을 수 없습니다'));
        r.readAsDataURL(file);
    });
}

async function saveOutput(name) {
    const res = await api.get(`/api/folderdoc/output?name=${encodeURIComponent(name)}`);
    download(await res.blob(), name);
}

export function openDocuments() {
    return openTab({ id: 'documents', title: '문서 작성', menu: true, wide: true,
        onClose: () => { clearInterval(poll); poll = null; },
        render(pane) {
            let state = null;
            let picked = new Set();
            let found = [];
            let previewUrl = null, previewKey = null;

            // ① 업무 폴더와 색인
            const folder = el('input', { type: 'text', style: 'flex: 1', placeholder: '예: D:\\업무\\2026 과학과' });
            const browse = el('button.btn.small', { type: 'button', onclick: guarded(async () => {
                browse.disabled = true;
                toast('찾아보기 창을 열었습니다. 작업 표시줄에서 창을 확인하세요.');
                try {
                    const r = await api.post('/api/pick', { purpose: 'folderdoc' });
                    if (r.path) { folder.value = r.path; await saveFolder(); }
                } finally { browse.disabled = false; }
            }) }, '찾아보기');
            const saveFolder = guarded(async () => {
                await saveSettings({ folderdoc_folder: folder.value });
                toast('폴더를 저장했습니다. [색인 만들기]를 누르면 읽기 시작합니다.');
                await refresh();
            });
            folder.addEventListener('change', saveFolder);
            const withAI = el('input', { type: 'checkbox' });
            const indexInfo = el('p.small');
            const indexBtn = el('button.btn.primary', { onclick: guarded(async () => {
                await api.post('/api/folderdoc/index', { ai: withAI.checked });
                toast(withAI.checked ? '색인을 시작했습니다. AI 요약은 파일당 30초~1분 걸립니다.' : '색인을 시작했습니다.');
                await refresh();
            }) }, '색인 만들기');
            const stopBtn = el('button.btn.danger', { hidden: true, onclick: guarded(async () => {
                await api.post('/api/folderdoc/stop');
                toast('지금 읽는 파일까지 마치고 멈춥니다.');
            }) }, '멈추기');
            const jobLine = el('p.small', { role: 'status', 'aria-live': 'polite' });

            // ② 할 작업과 관련 파일
            const task = el('textarea', { rows: 3, maxLength: 300,
                placeholder: '예: 2학기 과학과 협의회 회의록 작성 (10월 15일 과학실 2, 참석 5명)' });
            const results = el('div');
            const findBtn = el('button.btn', { onclick: guarded(async () => {
                if (!task.value.trim()) return toast('할 작업을 먼저 적어 주세요', 'error');
                const r = await api.get(`/api/folderdoc/search?q=${encodeURIComponent(task.value)}`);
                found = r.files;
                picked = new Set(found.slice(0, 3).map(f => f.path));
                renderResults();
            }) }, '관련 파일 찾기');
            const renderResults = () => clear(results,
                !found.length && el('div.empty', state && state.index.files ? '관련 파일을 찾지 못했습니다. 작업 내용을 다르게 적어 보세요.'
                    : '아직 색인이 없습니다. 위에서 [색인 만들기]를 먼저 누르세요.'),
                found.length > 0 && el('p.small', '체크한 파일만 참고합니다 (최대 8개). 파일에서 작업과 관계있는 줄만 골라 AI 에 보냅니다.'),
                found.length > 0 && el('ul.list', found.map(f => {
                    const cb = el('input', { type: 'checkbox', checked: picked.has(f.path),
                        onchange: e => { e.target.checked ? picked.add(f.path) : picked.delete(f.path); } });
                    return el('li', el('label.check', cb, el('span.ttl', f.path)),
                        el('span.meta', f.summary || f.snippet));
                })));

            // ③ 서식 (선택)
            const formInfo = el('div');
            const formInput = el('input', { type: 'file', accept: '.hwp,.hwpx,.docx', onchange: guarded(async e => {
                const file = e.target.files[0];
                if (!file) return;
                if (file.size > 10 * 1024 * 1024) return toast('서식 파일이 너무 큽니다 (10MB 초과)', 'error');
                await api.post('/api/folderdoc/form', { name: file.name, data: await fileToBase64(file) });
                e.target.value = '';
                await refresh();
            }) });
            const fmt = el('select', el('option', { value: 'docx' }, '워드 .docx (권장)'), el('option', { value: 'hwpx' }, '한글 .hwpx'));
            const renderForm = async () => {
                const f = state.form;
                if (!f) return clear(formInfo, el('p.small', '서식을 올리지 않으면 새 문서로 만듭니다: '), fmt);
                const key = `${f.name}|${f.fields.length}`;
                if (f.preview && previewKey !== key) {   // 2초마다 새로 고칠 때 그림을 다시 받지 않게
                    const res = await api.get('/api/folderdoc/form/preview');
                    if (previewUrl) URL.revokeObjectURL(previewUrl);
                    previewUrl = URL.createObjectURL(await res.blob());
                    previewKey = key;
                }
                const img = f.preview ? el('img', { src: previewUrl, alt: '서식 첫 쪽 미리보기', style: 'max-width: 260px; border: 1px solid var(--line); display: block; margin: 8px 0' }) : null;
                clear(formInfo,
                    el('p', el('b', f.name), ` · 채울 자리 ${f.fields.length}곳 `,
                        el('button.btn.small', { onclick: guarded(async () => { await api.del('/api/folderdoc/form'); await refresh(); }) }, '서식 빼기')),
                    img || el('p.small', '이 서식에는 미리보기 그림이 없습니다 (한글에서 저장하면 생깁니다).'),
                    el('p.small', '서식의 글꼴·색·표는 그대로 두고 아래 자리의 글만 바꿉니다. 지금 글은 작년 내용(본보기)입니다.'),
                    el('ul.list', f.fields.map(x => el('li', el('span.ttl', `${x.id} [${x.label}]`), el('span.meta', x.text || '(빈칸)')))));
            };

            // ④ 만들기와 결과
            const makeBtn = el('button.btn.primary', { onclick: guarded(async () => {
                if (!task.value.trim()) return toast('할 작업을 먼저 적어 주세요', 'error');
                if (!picked.size) return toast('참고할 파일을 하나 이상 체크하세요', 'error');
                await api.post('/api/folderdoc/draft', { task: task.value, files: [...picked].slice(0, 8),
                    use_form: !!state.form, format: fmt.value });
                toast('문서를 만드는 중입니다. 1~3분 걸릴 수 있습니다.');
                await refresh();
            }) }, '문서 만들기');
            const resultBox = el('div');
            const outputs = el('div');

            const renderJob = () => {
                const j = state.job;
                const running = !!j.running;
                indexBtn.disabled = running; makeBtn.disabled = running;
                stopBtn.hidden = j.running !== 'index';
                const p = j.progress;
                jobLine.textContent = running
                    ? (j.running === 'index' ? `색인 중… ${p && p.total ? `${p.done}/${p.total} ${p.file}` : ''}` : '문서를 만드는 중… (AI 가 쓰는 중)')
                    : j.error ? `⚠ ${j.error}` : '';
                const r = j.result;
                if (r && r.kind === 'draft' && !running) {
                    clear(resultBox, el('div.card',
                        el('h3', '만든 문서'),
                        el('p', el('b', r.file), ' ', el('button.btn.small.primary', { onclick: guarded(() => saveOutput(r.file)) }, '받기')),
                        el('p.small', `AI 에 보낸 글 ${r.prompt_chars.toLocaleString()}자 (참고 자료 ${r.materials_chars.toLocaleString()}자)`),
                        r.filled && el('ul.list', r.filled.map(x => el('li', el('span.ttl', `${x.id} [${x.label}]`), el('span.meta', x.text)))),
                        r.not_filled && r.not_filled.length > 0 && el('p.small', `채우지 못한 칸: ${r.not_filled.join(', ')}`),
                        r.text && el('pre.text', { style: 'white-space: pre-wrap; max-height: 320px; overflow: auto' }, r.text),
                        el('p.small', '날짜·숫자·이름은 원본과 꼭 맞춰 보세요. 한글에서 열어 모양을 확인한 뒤 저장하세요.')));
                } else if (j.error && !running && j.failed === 'draft') {
                    clear(resultBox, el('p.small', { style: 'color: var(--danger)' }, `⚠ ${j.error}`));
                } else if (r && r.kind === 'index' && !running) {
                    clear(resultBox, el('p.small', `색인 끝: 파일 ${r.files}개 (새로 읽음 ${r.new}, 그대로 ${r.unchanged}, AI 요약 ${r.summarized}, 못 읽음 ${r.errors})`
                        + (r.limit ? ' · 파일이 너무 많아 앞 3,000개만 읽었습니다' : '') + (r.stopped ? ' · 중간에 멈춤' : '')));
                }
                if (running && !poll) poll = setInterval(guarded(refresh), 2000);
                if (!running && poll) { clearInterval(poll); poll = null; }
            };

            const refresh = async () => {
                state = await api.get('/api/folderdoc');
                if (document.activeElement !== folder) folder.value = state.folder || '';
                const ix = state.index;
                indexInfo.textContent = ix.files
                    ? `색인된 파일 ${ix.files}개 · AI 요약 ${ix.summarized}개 · 못 읽은 파일 ${ix.errors}개 · 마지막 ${ix.last}`
                    : '아직 색인이 없습니다. 폴더를 고른 뒤 [색인 만들기]를 누르세요 (자동으로 돌지 않습니다).';
                withAI.disabled = !state.ai_enabled;
                renderJob();
                await renderForm();
                clear(outputs, state.outputs.length ? el('ul.list', state.outputs.map(o => el('li',
                    el('span.ttl', o.name), el('span.meta', o.at, ' ', el('button.btn.small', { onclick: guarded(() => saveOutput(o.name)) }, '받기')))))
                    : el('div.empty', '아직 만든 문서가 없습니다'));
            };

            pane.append(el('div', { style: 'padding: 16px; max-width: 860px' },
                el('h2', '문서 작성'),
                el('p.small', '업무 폴더의 한글·PDF·워드·엑셀 파일을 읽어 두고, 할 작업을 적으면 관련 파일을 찾아 문서를 만듭니다. '
                    + '파일 내용은 이 PC 밖으로 나가지 않고, AI 는 이 PC 의 LM Studio 만 씁니다 (학생 이름은 가려서 보냄).'),
                el('div.card', el('h3', '① 업무 폴더'),
                    el('div.field', el('span', '색인할 폴더'), el('div.row', folder, browse)),
                    el('label.check', withAI, el('span', 'AI 요약도 함께 만들기'),
                        el('span.hint', '찾기가 더 정확해지지만 파일당 30초~1분 걸립니다 (설정에서 AI 를 켜야 함)')),
                    el('div.row', indexBtn, stopBtn), indexInfo, jobLine),
                el('div.card', el('h3', '② 할 작업과 참고 파일'), task, el('div.row', { style: 'margin: 8px 0' }, findBtn), results),
                el('div.card', el('h3', '③ 학교 서식 (선택)'),
                    el('p.small', '학교에서 쓰는 서식(.hwp · .hwpx · .docx)을 올리면 그 서식 그대로 칸만 채웁니다.'), formInput, formInfo),
                el('div.card', el('h3', '④ 문서 만들기'), makeBtn, resultBox),
                el('div.card', el('h3', '만든 문서'), outputs)));
            guarded(refresh)();
            renderResults();
        } });
}

registerView({ id: 'documents', icon: '문서', label: '문서 작성', noSide: true, renderSide() {}, open: openDocuments });
