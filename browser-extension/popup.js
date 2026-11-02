import { readTab, combine, sendTab, loadSettings, rememberPick, MODE_NAMES } from './capture.js';
import { isNeis, sendForms, fillNow } from './neis.js';

const $ = id => document.getElementById(id);
let frames = [];

function where(url) {
    try { const u = new URL(url); return u.host + u.pathname; } catch { return '(주소 없음)'; }
}

const say = (text, cls = 'muted') => { $('status').textContent = text; $('status').className = cls; };

async function send(tab, pickPath = null) {
    try {
        const r = await sendTab(tab.id, { pickPath });
        say(r.duplicate ? '이미 수신함에 있는 내용입니다' : `보냈습니다 (${r.chars}자 · 일정 ${r.events}건, 마감 ${r.deadlines}건)`, 'ok');
    } catch (e) {
        say('보내지 못했습니다: ' + e.message, 'bad');
    }
}

async function load() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const s = await loadSettings();
    try {
        frames = await readTab(tab.id);
    } catch (e) {
        say('이 화면은 읽을 수 없습니다: ' + e.message, 'bad');
        $('send').disabled = true;
        return;
    }
    const top = frames.find(f => f.top) || {};
    $('mode').textContent = `지금 방식: ${MODE_NAMES[s.mode] || s.mode}` + (s.mode === 'pick' && s.picked[top.host] ? ` (${s.picked[top.host]})` : '');
    let chosenPaths = [];
    try {
        const c = combine(frames, s);
        chosenPaths = c.paths;
        say(`지금 방식으로 보낼 글 ${c.text.length}자${c.selected ? ' (고른 글)' : ''}`);
    } catch (e) {
        say(e.message, 'bad');
    }
    for (const f of frames.filter(x => x.text || x.top)) {
        const li = document.createElement('li');
        if (!f.top && chosenPaths.includes(f.path)) li.className = 'chosen';   // 지금 방식이면 보낼 칸
        const row = document.createElement('div');
        row.className = 'row';
        const label = document.createElement('span');
        label.textContent = `${f.top ? '[바깥 틀] ' : ''}${where(f.url)} — 글 ${f.text.length}자`;
        row.append(label);
        if (f.text && !f.top) {
            const b = document.createElement('button');
            b.textContent = '이 칸만 보내기';
            b.onclick = async () => { await rememberPick(f.host, f.path); await send(tab, f.path); };
            row.append(b);
        }
        li.append(row);
        $('frames').append(li);
    }
    $('send').onclick = () => send(tab);
    // 나이스 화면이면 입력 칸 채우기 단추
    if (isNeis(top.host)) {
        $('neis').hidden = false;
        $('neisScan').onclick = async () => {
            try {
                const r = await sendForms(tab.id);
                say(`입력 칸 구조를 보냈습니다 (화면 ${r.forms}개, ${r.fields}칸, 값은 보내지 않음). 업무 데스크 '나이스 입력'에서 값을 정하세요`, 'ok');
            } catch (e) { say(e.message, 'bad'); }
        };
        $('neisFill').onclick = async () => {
            try {
                const r = await fillNow({ auto: false });
                say(r === null ? '업무 데스크에 채우기 요청이 없습니다'
                    : r.waiting ? '나이스 채우기 화면을 앞에 둔 뒤 다시 누르세요'
                    : r.cancelled ? '채우지 않았습니다'
                    : `${r.filled}칸을 채웠습니다${r.missing.length ? ` · 못 채운 칸: ${r.missing.join(', ')}` : ''}. 내용을 확인하고 직접 저장하세요`, r?.filled ? 'ok' : 'bad');
            } catch (e) { say('채우지 못했습니다: ' + e.message, 'bad'); }
        };
    }
}

// 화면 구조만 복사 (프레임 주소의 경로와 글자 수, 내용은 없음) — 읽기가 잘 안 될 때 개발 담당에게 전달용
$('copy').onclick = async () => {
    const info = frames.map(f => ({ top: f.top, where: where(f.url), chars: f.text.length, inputs: f.fields.length, focused: f.focused, visible: f.visible }));
    await navigator.clipboard.writeText(JSON.stringify(info, null, 2));
    say('구조를 복사했습니다 (글 내용은 들어 있지 않습니다)');
};
$('opts').onclick = () => chrome.runtime.openOptionsPage();
load();
