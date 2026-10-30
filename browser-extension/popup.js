import { readTab, combine, sendTab } from './capture.js';

const $ = id => document.getElementById(id);
let frames = [];

function where(url) {
    try { const u = new URL(url); return u.host + u.pathname; } catch { return '(주소 없음)'; }
}

async function load() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    try {
        frames = await readTab(tab.id);
    } catch (e) {
        $('status').textContent = '이 화면은 읽을 수 없습니다: ' + e.message;
        $('status').className = 'bad';
        $('send').disabled = true;
        return;
    }
    const c = combine(frames);
    $('status').textContent = `프레임 ${frames.length}개 · 보낼 글 ${c.text.length}자${c.selected ? ' (고른 글)' : ''}`;
    for (const f of frames) {
        const li = document.createElement('li');
        li.textContent = `${f.top ? '[위] ' : ''}${where(f.url)} — 글 ${f.text.length}자` + (f.fields.length ? `, 입력칸 ${f.fields.length}개` : '');
        $('frames').append(li);
    }
    $('send').onclick = async () => {
        $('send').disabled = true;
        try {
            const r = await sendTab(tab.id);
            $('status').textContent = r.duplicate ? '이미 수신함에 있는 내용입니다' : `보냈습니다 (일정 ${r.events}건, 마감 ${r.deadlines}건)`;
            $('status').className = 'ok';
        } catch (e) {
            $('status').textContent = '보내지 못했습니다: ' + e.message;
            $('status').className = 'bad';
        }
        $('send').disabled = false;
    };
}

// 화면 구조만 복사 (프레임 주소의 경로와 글자 수, 내용은 없음) — 읽기가 잘 안 될 때 개발 담당에게 전달용
$('copy').onclick = async () => {
    const info = frames.map(f => ({ top: f.top, where: where(f.url), chars: f.text.length, inputs: f.fields.length }));
    await navigator.clipboard.writeText(JSON.stringify(info, null, 2));
    $('status').textContent = '구조를 복사했습니다 (글 내용은 들어 있지 않습니다)';
};
$('opts').onclick = () => chrome.runtime.openOptionsPage();
load();
