// 단축키(Alt+Shift+S) → 지금 보는 탭의 글을 업무 데스크로 보냄
import { sendTab } from './capture.js';

function toast(tabId, text, ok) {
    // 화면 위쪽 프레임에 잠깐 보였다 사라지는 알림 (보내졌는지 바로 알 수 있게)
    return chrome.scripting.executeScript({
        target: { tabId },
        args: [text, ok],
        func: (msg, good) => {
            const old = document.getElementById('__desk_toast');
            if (old) old.remove();
            const d = document.createElement('div');
            d.id = '__desk_toast';
            d.textContent = msg;
            d.style.cssText = 'position:fixed;top:16px;right:16px;z-index:2147483647;max-width:360px;padding:10px 14px;'
                + 'border-radius:8px;font:600 14px/1.4 sans-serif;color:#fff;box-shadow:0 2px 10px rgba(0,0,0,.3);'
                + 'background:' + (good ? '#1b7f3b' : '#b3261e');
            document.documentElement.appendChild(d);
            setTimeout(() => d.remove(), 3500);
        },
    }).catch(() => { /* 알림을 못 띄우는 화면이면 배지로만 알림 */ });
}

async function badge(tabId, text, color) {
    await chrome.action.setBadgeBackgroundColor({ tabId, color });
    await chrome.action.setBadgeText({ tabId, text });
    setTimeout(() => chrome.action.setBadgeText({ tabId, text: '' }).catch(() => {}), 4000);
}

export async function sendActiveTab() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || tab.id == null) return;
    // 옵션에서 '보내기 전에 미리 보기'를 켰으면 화면 위에 첫 몇 줄을 보여 주고 물어봄
    const confirm = text => chrome.scripting.executeScript({
        target: { tabId: tab.id },
        args: [text.split('\n').filter(l => l.trim()).slice(0, 12).join('\n'), text.length],
        func: (head, n) => window.confirm(`업무 데스크로 보낼까요? (${n}자)\n\n${head}${n > head.length ? '\n…' : ''}`),
    }).then(r => r[0]?.result === true).catch(() => true);
    try {
        const r = await sendTab(tab.id, { confirm });
        if (r.cancelled) {
            await toast(tab.id, '보내지 않았습니다', false);
            return;
        }
        const what = r.selected ? '고른 글' : '화면';
        const msg = r.duplicate
            ? '이미 수신함에 있는 내용입니다'
            : `업무 데스크로 보냈습니다 (${what} ${r.chars}자 · 일정 ${r.events}건, 마감 ${r.deadlines}건)`;
        await toast(tab.id, msg, true);
        await badge(tab.id, r.duplicate ? '=' : '✔', '#1b7f3b');
    } catch (e) {
        await toast(tab.id, '보내지 못했습니다: ' + e.message, false);
        await badge(tab.id, '!', '#b3261e');
    }
}

chrome.commands.onCommand.addListener(command => {
    if (command === 'send-page') sendActiveTab();
});
