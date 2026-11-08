// 서술형·수행평가 손글씨 채점 (0.5.80)
// 평가(문항·모범 답안·채점 기준) → 답안지 올리기 → 이 PC 의 LM Studio 비전 모델로 손글씨 읽기 → 선생님 확인·고침
// → (선택) AI 점수 제안 → 선생님이 점수 확정 → CSV 내보내기. 답안지·점수는 이 PC 에만 저장하고 인터넷 AI 로 보내지 않음.
import { api } from '../api.js';
import { el, clear, guarded, toast } from '../ui.js';
import { registerView, openTab, openPopup, refreshSide, findTab } from '../shell.js';
import { icon } from '../icons.js';
import { store, saveSettings } from '../store.js';

let tasks = [];
let kinds = { essay: '서술형 평가', performance: '수행평가' };
let currentTask = null;    // 열린 평가 id
let currentSheet = null;   // 보고 있는 답안지 id
let poller = null;
const MAX_SIDE = 2200;     // 올리기 전에 긴 변을 이 크기로 줄임 (AI 가 읽기 충분, 빠름)

const fmt = v => (v == null ? '' : Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100));
const STATUS = { '': '아직 안 읽음', waiting: '읽기 대기', running: '읽는 중…', done: '읽음', error: '읽기 실패' };
const AI_STATUS = { waiting: '점수 제안 대기', running: '점수 제안 중…', error: '제안 실패' };

async function loadTasks() {
    const r = await api.get('/api/grading/tasks');
    tasks = r.tasks;
    kinds = r.kinds || kinds;
}

function renderSide(body, actions) {
    actions.append(el('button.icon-btn', { title: '새 평가 만들기', onclick: () => openTaskForm() }, icon('plus')));
    if (!tasks.length) {
        body.append(el('div.empty', '아직 평가가 없습니다. ＋ 를 눌러 평가를 만드세요.'));
        return;
    }
    body.append(el('ul.list', tasks.map(t => el('li', {
        className: t.id === currentTask ? 'on' : '',
        onclick: () => openTask(t.id),
    }, el('span.ttl', t.title),
    el('span.meta', el('span', kinds[t.kind] || t.kind), el('span', `문항 ${t.questions}`),
        el('span', `답안지 ${t.sheets}장${t.sheets ? ` · 읽음 ${t.read}` : ''}`))))));
}

// ---------------------------------------------------------------- 평가 만들기·고치기
function questionRow(q, list) {
    const num = el('input', { type: 'number', min: 1, max: 99, value: q.number, 'aria-label': '문항 번호', style: 'width: 5em' });
    const max = el('input', { type: 'number', min: 0, max: 1000, step: 0.5, value: q.max_score ?? 5, 'aria-label': '만점', style: 'width: 6em' });
    const prompt = el('textarea', { rows: 2, maxLength: 2000, placeholder: '문제 (손글씨 읽을 때 답 칸을 찾는 데 참고합니다)' }, q.prompt || '');
    const answer = el('textarea', { rows: 2, maxLength: 4000, placeholder: '모범 답안' }, q.answer || '');
    const rubric = el('textarea', { rows: 3, maxLength: 4000,
        placeholder: '채점 기준 (예: 빛에너지를 화학 에너지로 바꾼다는 내용 3점, 엽록체 언급 2점, 맞춤법은 감점 없음)' }, q.rubric || '');
    const row = el('section.card.grade-q',
        el('div.row', el('label.field.inline', el('span', '번호'), num), el('label.field.inline', el('span', '만점'), max),
            el('button.btn.small.danger', { type: 'button', onclick: () => { row.remove(); list.rows.delete(row); } }, '문항 빼기')),
        prompt, answer, rubric);
    list.rows.set(row, () => ({ number: Number(num.value), max_score: Number(max.value), prompt: prompt.value,
        answer: answer.value, rubric: rubric.value }));
    return row;
}

function openTaskForm(t = null) {
    openPopup({ id: 'grading-task', title: t ? '평가 고치기' : '새 평가', refresh: true, render(pane, tab) {
        const title = el('input', { type: 'text', maxLength: 100, value: t?.title || '', placeholder: '예: 2학년 과학 1학기 서술형' });
        const kind = el('select', Object.entries(kinds).map(([v, n]) => el('option', { value: v, selected: (t?.kind || 'essay') === v }, n)));
        const note = el('textarea', { rows: 2, maxLength: 2000, placeholder: '메모 (선택)' }, t?.note || '');
        const list = { rows: new Map() };
        const box = el('div');
        const qs = t?.questions?.length ? t.questions : [{ number: 1, max_score: 5 }];
        for (const q of qs) box.append(questionRow(q, list));
        const add = el('button.btn', { type: 'button', onclick: () => {
            const used = [...list.rows.values()].map(f => f().number);
            box.append(questionRow({ number: (used.length ? Math.max(...used) : 0) + 1, max_score: 5 }, list));
        } }, '＋ 문항 더하기');
        const save = el('button.btn.primary', { onclick: guarded(async () => {
            const body = { title: title.value, kind: kind.value, note: note.value, questions: [...list.rows.values()].map(f => f()) };
            if (t && t.sheets?.length && !window.confirm('빠진 문항 번호가 있으면 그 문항의 학생 답·점수도 지워집니다. 저장할까요?')) return;
            const r = t ? await api.put(`/api/grading/tasks/${t.id}`, body) : await api.post('/api/grading/tasks', body);
            tab.close();
            await loadTasks();
            toast('평가를 저장했습니다');
            openTask(r.id);
        }) }, '저장');
        pane.append(
            el('label.field', el('span', '평가 이름'), title),
            el('label.field', el('span', '종류'), kind), note,
            el('p.small', '문제·모범 답안·채점 기준은 AI 점수 제안에 쓰입니다. 비워 두면 손글씨 읽기만 합니다.'),
            box, el('div.row', add, save));
    } });
}

// ---------------------------------------------------------------- 답안지 올리기
const toBase64 = blob => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error('파일을 읽지 못했습니다'));
    r.readAsDataURL(blob);
});

async function shrink(file) {
    // 휴대폰 사진(4000px·5MB)을 그대로 보내면 느려서 긴 변 2200px JPEG 로 줄임. 방향(EXIF)은 브라우저가 맞춤
    try {
        const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
        const scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
        const c = document.createElement('canvas');
        c.width = Math.round(bmp.width * scale);
        c.height = Math.round(bmp.height * scale);
        const g = c.getContext('2d');
        g.fillStyle = '#fff';
        g.fillRect(0, 0, c.width, c.height);
        g.drawImage(bmp, 0, 0, c.width, c.height);
        bmp.close?.();
        const blob = await new Promise(res => c.toBlob(res, 'image/jpeg', 0.85));
        return { name: file.name, data: await toBase64(blob) };
    } catch (e) {
        throw new Error(`'${file.name}' 그림을 열지 못했습니다 (JPG·PNG 만 됩니다)`);
    }
}

function openUpload(t) {
    openPopup({ id: 'grading-upload', title: '답안지 올리기', wide: false, refresh: true, render(pane, tab) {
        const files = el('input', { type: 'file', multiple: true, accept: 'image/jpeg,image/png,application/pdf,.pdf' });
        const per = el('input', { type: 'number', min: 1, max: 6, value: 1, style: 'width: 5em' });
        const first = store.students.length ? Math.min(...store.students.map(s => s.number)) : 1;
        const start = el('input', { type: 'number', min: 0, max: 999, value: first, style: 'width: 6em' });
        const status = el('p.small', { role: 'status', 'aria-live': 'polite' });
        const go = el('button.btn.primary', { onclick: guarded(async () => {
            const list = [...files.files].sort((a, b) => a.name.localeCompare(b.name, 'ko', { numeric: true }));
            if (!list.length) throw new Error('답안지 파일을 고르세요');
            const n = Number(per.value);
            let next = Number(start.value);
            let made = 0;
            go.disabled = true;
            try {
                // 그림은 n장씩 한 학생으로 묶어 보냄, PDF 는 파일 하나씩 (서버가 쪽을 n장씩 나눔)
                const images = list.filter(f => f.type !== 'application/pdf' && !/\.pdf$/i.test(f.name));
                const pdfs = list.filter(f => !images.includes(f));
                if (images.length % n) throw new Error(`그림 ${images.length}장이 한 학생 ${n}장으로 나누어떨어지지 않습니다`);
                for (const f of pdfs) {
                    if (f.size > 14 * 1024 * 1024) throw new Error(`'${f.name}' 이 너무 큽니다 (PDF 는 14MB 이하로 나눠 올리세요)`);
                    status.textContent = `${f.name} 올리는 중…`;
                    const r = await api.post(`/api/grading/tasks/${t.id}/sheets`,
                        { files: [{ name: f.name, data: await toBase64(f) }], pages_per_sheet: n, start_number: next });
                    made += r.created.length;
                    next = r.next_number;
                }
                for (let i = 0; i < images.length; i += n) {
                    status.textContent = `그림 올리는 중… ${i + 1}/${images.length}`;
                    const group = [];
                    for (const f of images.slice(i, i + n)) group.push(await shrink(f));
                    const r = await api.post(`/api/grading/tasks/${t.id}/sheets`, { files: group, pages_per_sheet: n, start_number: next });
                    made += r.created.length;
                    next = r.next_number;
                }
            } finally {
                go.disabled = false;
                status.textContent = made ? `답안지 ${made}장을 올렸습니다.` : '';
                if (made) { await loadTasks(); refreshSide(); refreshTask(true); }
            }
            tab.close();
            toast(`답안지 ${made}장을 올렸습니다. [손글씨 읽기]를 누르세요.`);
        }) }, '올리기');
        pane.append(
            el('p.sub', '스캐너로 저장한 PDF·JPG 나 휴대폰 사진을 고르세요. 파일 이름 순서대로, 시작 번호부터 학생 명단 순서로 붙입니다 (나중에 바꿀 수 있음).'),
            el('label.field', el('span', '답안지 파일 (여러 개 고르기)'), files),
            el('div.row', el('label.field.inline', el('span', '한 학생 쪽 수'), per),
                el('label.field.inline', el('span', '시작 번호'), start)),
            el('p.small', '그림은 이 PC 의 업무 데스크 data 폴더에만 저장합니다. 스캔 PDF 는 쪽마다 그림 한 장(JPEG)인 것만 읽을 수 있습니다.'),
            go, status);
    } });
}

// ---------------------------------------------------------------- 평가 화면
async function loadImages(sheet, holder, urls) {
    for (let n = 1; n <= sheet.pages; n++) {
        try {
            const res = await api.get(`/api/grading/sheets/${sheet.id}/image/${n}`);
            const url = URL.createObjectURL(await res.blob());
            urls.push(url);
            holder.append(el('a', { href: url, target: '_blank', rel: 'noopener', title: '새 창에서 크게 보기' },
                el('img.grade-img', { src: url, alt: `${sheet.student_name || sheet.student_number + '번'} 답안지 ${n}쪽` })));
        } catch (e) {
            holder.append(el('p.small', `${n}쪽 그림을 불러오지 못했습니다: ${e.message}`));
        }
    }
}

function answerBox(t, sheet, q) {
    const a = sheet.answers[String(q.id)] || null;
    const path = a ? `/api/grading/answers/${a.id}` : `/api/grading/sheets/${sheet.id}/questions/${q.id}`;
    const save = async body => {
        const r = await api.patch(path, body);
        sheet.answers[String(q.id)] = r;
        return r;
    };
    const text = el('textarea.grade-text', { rows: 4, maxLength: 8000, placeholder: a ? '' : '아직 읽지 않았습니다. 직접 적어도 됩니다.',
        onchange: guarded(async () => { await save({ text: text.value }); toast('답을 고쳐 저장했습니다'); }) }, a?.text || '');
    const score = el('input', { type: 'number', min: 0, max: q.max_score, step: 0.5, value: fmt(a?.score), style: 'width: 6em',
        'aria-label': `${q.number}번 점수`,
        onchange: guarded(async () => {
            const v = score.value === '' ? null : Number(score.value);
            await save({ score: v });
            refreshTask();
        }) });
    const comment = el('input', { type: 'text', maxLength: 1000, value: a?.comment || '', placeholder: '메모 (선택)',
        onchange: guarded(async () => { await save({ comment: comment.value }); }) });
    const aiLine = el('div.grade-ai');
    if (a?.ai_score != null) {
        aiLine.append(el('span', `AI 제안 ${fmt(a.ai_score)}점`), a.ai_reason ? el('span.small', a.ai_reason) : null,
            el('button.btn.small', { onclick: guarded(async () => { await save({ accept_ai: true }); refreshTask(); }) }, '이 점수로'));
    } else if (AI_STATUS[a?.ai_status]) {
        aiLine.append(el('span.small', AI_STATUS[a.ai_status] + (a.ai_error ? ` · ${a.ai_error}` : '')));
    }
    const ask = a && el('button.btn.small', { title: '이 답만 AI 에 점수 제안 받기 (이 PC 의 LM Studio)',
        onclick: guarded(async () => { await api.post(`/api/grading/tasks/${t.id}/score`, { answer_ids: [a.id] }); refreshTask(true); }) }, 'AI 제안');
    const edited = a?.edited && a.ocr_text != null && a.ocr_text !== a.text
        ? el('details.small', el('summary', 'AI 가 읽은 원래 글'), el('pre.text', a.ocr_text)) : null;
    return el('section.card.grade-answer',
        el('div.row.grade-head', el('strong', `${q.number}번`), el('span.small', `만점 ${fmt(q.max_score)}점`),
            el('label.field.inline', el('span', '점수'), score), ask),
        text, edited, aiLine, comment);
}

function renderTask(pane, t) {
    const urls = [];
    pane.addEventListener('grade-cleanup', () => urls.forEach(u => URL.revokeObjectURL(u)), { once: true });
    const sheets = t.sheets;
    if (!sheets.some(s => s.id === currentSheet)) currentSheet = sheets[0]?.id ?? null;
    const sheet = sheets.find(s => s.id === currentSheet);
    const busy = t.pending > 0 || sheets.some(s => ['waiting', 'running'].includes(s.ocr_status)
        || Object.values(s.answers).some(a => ['waiting', 'running'].includes(a.ai_status)));
    const counts = { read: sheets.filter(s => s.ocr_status === 'done').length,
        failed: sheets.filter(s => s.ocr_status === 'error').length,
        scored: sheets.filter(s => s.scored === t.questions.length && t.questions.length).length };

    const model = el('input', { type: 'text', maxLength: 200, value: store.settings.grade_ocr_model || '', list: 'grade-models',
        placeholder: store.settings.ai_model ? `비우면 기본 AI 모델(${store.settings.ai_model})` : '예: qwen2.5-vl-7b-instruct' });
    const models = el('datalist#grade-models');
    const modelRow = el('details.card.grade-model', el('summary', '손글씨 읽기 모델 (LM Studio 비전 모델)'),
        el('p.small', '글씨를 읽으려면 LM Studio 에 그림을 읽는 모델(이름에 VL·vision 이 들어간 모델, 예: Qwen2.5-VL 7B)을 불러와야 합니다. 점수 제안은 설정의 기본 AI 모델을 씁니다. 모두 이 PC 안에서만 돕니다.'),
        el('div.row', model, models,
            el('button.btn.small', { onclick: guarded(async () => {
                const r = await api.post('/api/ai/check', {});
                clear(models, r.models.map(m => el('option', { value: m })));
                toast(`LM Studio 모델 ${r.models.length}개: ${r.models.join(', ')}`, 'info', 8000);
            }) }, '모델 목록'),
            el('button.btn.small.primary', { onclick: guarded(async () => {
                await saveSettings({ grade_ocr_model: model.value });
                toast('손글씨 읽기 모델을 저장했습니다');
            }) }, '저장')));

    const toolbar = el('div.row.grade-tools',
        el('button.btn', { onclick: () => openUpload(t) }, '답안지 올리기'),
        el('button.btn.primary', { disabled: !sheets.length, title: '아직 안 읽은(또는 실패한) 답안지를 이 PC 의 AI 로 읽습니다',
            onclick: guarded(async () => {
                const r = await api.post(`/api/grading/tasks/${t.id}/ocr`, {});
                toast(r.added ? `답안지 ${r.added}장 읽기를 시작했습니다. 한 장에 수십 초 걸릴 수 있습니다.` : '새로 읽을 답안지가 없습니다');
                refreshTask(true);
            }) }, '손글씨 읽기'),
        el('button.btn', { disabled: !counts.read, title: '아직 제안이 없는 답에 채점 기준으로 점수를 제안받습니다 (점수는 선생님이 정함)',
            onclick: guarded(async () => {
                if (!window.confirm('AI 점수 제안은 참고용입니다. 제안된 점수는 [이 점수로]를 누르거나 직접 넣어야 점수가 됩니다. 시작할까요?')) return;
                const r = await api.post(`/api/grading/tasks/${t.id}/score`, {});
                toast(r.added ? `답 ${r.added}개에 점수 제안을 시작했습니다` : '새로 제안받을 답이 없습니다');
                refreshTask(true);
            }) }, 'AI 점수 제안'),
        el('button.btn', { disabled: !sheets.length, onclick: guarded(async () => {
            const res = await api.get(`/api/grading/tasks/${t.id}/export`);
            const url = URL.createObjectURL(await res.blob());
            const link = el('a', { href: url, download: `채점-${t.title}.csv`, hidden: true });
            document.body.append(link);   // 문서에 붙여야 파일 이름이 지켜짐
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 5000);
        }) }, 'CSV(엑셀) 내보내기'),
        el('button.btn', { onclick: () => openTaskForm(t) }, '평가 고치기'),
        el('button.btn.danger', { onclick: guarded(async () => {
            if (!window.confirm(`'${t.title}' 평가와 답안지 그림 ${sheets.length}장, 점수를 모두 지울까요? 되돌릴 수 없습니다.`)) return;
            await api.del(`/api/grading/tasks/${t.id}`);
            findTab('grading')?.close();
            currentTask = null;
            await loadTasks();
            refreshSide();
        }) }, '평가 지우기'));

    const summary = el('p.small', { role: 'status', 'aria-live': 'polite' },
        `답안지 ${sheets.length}장 · 읽음 ${counts.read}${counts.failed ? ` · 실패 ${counts.failed}` : ''} · 채점 끝 ${counts.scored}`
        + ` · 만점 ${fmt(t.max_total)}점` + (busy ? ' · AI 작업 중… (자동으로 새로 고침)' : ''));

    const roster = el('ul.list.grade-roster', sheets.map(s => el('li', {
        className: s.id === currentSheet ? 'on' : '',
        onclick: () => { currentSheet = s.id; refreshTask(); },
    }, el('span.ttl', `${s.student_number ?? '-'}번 ${s.student_name || ''}`),
    el('span.meta', el('span', STATUS[s.ocr_status] || s.ocr_status),
        el('span', s.total != null ? `${fmt(s.total)}점 (${s.scored}/${t.questions.length})` : '점수 없음')))));

    const main = el('div.grade-main');
    if (!sheet) {
        main.append(el('div.empty', '답안지가 없습니다. [답안지 올리기]를 누르세요.'));
    } else {
        const i = sheets.indexOf(sheet);
        const num = el('input', { type: 'number', min: 0, max: 999, value: sheet.student_number ?? '', style: 'width: 5em', 'aria-label': '번호' });
        const name = el('input', { type: 'text', maxLength: 20, value: sheet.student_name || '', style: 'width: 8em', 'aria-label': '이름' });
        const who = el('div.row.grade-who',
            el('button.btn.small', { disabled: i <= 0, onclick: () => { currentSheet = sheets[i - 1].id; refreshTask(); } }, '◀ 앞 학생'),
            el('label.field.inline', el('span', '번호'), num), el('label.field.inline', el('span', '이름'), name),
            el('button.btn.small', { title: '번호만 바꾸면 학생 명단의 이름이 들어갑니다', onclick: guarded(async () => {
                const sameName = name.value === (sheet.student_name || '');
                await api.patch(`/api/grading/sheets/${sheet.id}`, {
                    student_number: num.value === '' ? null : Number(num.value),
                    student_name: sameName && String(sheet.student_number ?? '') !== num.value ? '' : name.value });
                refreshTask(true);
            }) }, '학생 바꾸기'),
            el('button.btn.small', { disabled: i >= sheets.length - 1, onclick: () => { currentSheet = sheets[i + 1].id; refreshTask(); } }, '다음 학생 ▶'),
            el('span.small', STATUS[sheet.ocr_status] + (sheet.ocr_error ? ` · ${sheet.ocr_error}` : '')),
            el('button.btn.small', { title: '이 답안지를 다시 읽습니다 (선생님이 고친 답은 그대로 둠)', onclick: guarded(async () => {
                await api.post(`/api/grading/tasks/${t.id}/ocr`, { sheet_ids: [sheet.id] });
                refreshTask(true);
            }) }, '다시 읽기'),
            el('button.btn.small.danger', { onclick: guarded(async () => {
                if (!window.confirm('이 답안지와 점수를 지울까요?')) return;
                await api.del(`/api/grading/sheets/${sheet.id}`);
                await loadTasks(); refreshSide(); refreshTask(true);
            }) }, '답안지 지우기'));
        const images = el('div.grade-images');
        loadImages(sheet, images, urls);
        const total = sheet.total != null ? `합계 ${fmt(sheet.total)} / ${fmt(t.max_total)}점` : '아직 점수 없음';
        const unparsed = sheet.ocr_status === 'done' && sheet.ocr_raw && !Object.keys(sheet.answers).length
            ? el('details.card', { open: true }, el('summary', 'AI 가 읽은 글 (문항으로 나누지 못함 — 아래 칸에 옮겨 주세요)'), el('pre.text', sheet.ocr_raw)) : null;
        main.append(who, el('div.grade-split', images,
            el('div.grade-answers', el('p.grade-total', total), unparsed, t.questions.map(q => answerBox(t, sheet, q)))));
    }

    pane.append(el('h1', t.title), el('p.sub', `${kinds[t.kind] || t.kind} · AI 가 읽은 글과 제안 점수는 틀릴 수 있으니 그림과 견주어 확인하세요. 내보내기에는 선생님이 정한 점수만 들어갑니다.`),
        modelRow, toolbar, summary, el('div.grade-layout', roster, main));
    return busy;
}

async function refreshTask(force = false) {
    const tab = findTab('grading');
    if (!tab || currentTask == null) return;
    // 선생님이 칸에 입력하는 중이면 자동 새로 고침은 미룸 (입력이 사라지지 않게)
    const typing = !force && tab.pane.contains(document.activeElement) && /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName);
    if (typing) return;
    tab.rerender();
}

function startPoll() {
    if (poller) return;
    poller = setInterval(() => {
        if (!findTab('grading')) { clearInterval(poller); poller = null; return; }
        if (findTab('grading').busy) refreshTask();
    }, 4000);
}

export function openTask(id) {
    currentTask = id;
    refreshSide();
    const tab = openTab({ id: 'grading', title: '채점', wide: true, refresh: true,
        onClose: () => { tab.pane.dispatchEvent(new Event('grade-cleanup')); currentTask = null; },
        render(pane, tabRef) {
            pane.dispatchEvent(new Event('grade-cleanup'));
            const body = el('div', el('p.small', '불러오는 중…'));
            pane.append(body);
            guarded(async () => {
                const t = await api.get(`/api/grading/tasks/${id}`);
                tabRef.setTitle(`채점 · ${t.title}`);
                const row = tasks.find(x => x.id === id);   // 왼쪽 목록의 장 수·읽은 수도 맞춤
                if (row && (row.sheets !== t.sheets.length || row.read !== t.sheets.filter(s => s.ocr_status === 'done').length)) {
                    Object.assign(row, { sheets: t.sheets.length, read: t.sheets.filter(s => s.ocr_status === 'done').length });
                    refreshSide();
                }
                clear(pane);
                tabRef.busy = renderTask(pane, t);
                if (tabRef.busy) startPoll();
            })();
        } });
    return tab;
}

registerView({ id: 'grading', icon: '✎', label: '채점', renderSide, open: guarded(async () => {
    await loadTasks();
    refreshSide();
}) });
