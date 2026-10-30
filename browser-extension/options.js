import { loadSettings, checkBase, ping } from './capture.js';

const $ = id => document.getElementById(id);
const say = (text, cls) => { $('msg').textContent = text; $('msg').className = cls || ''; };

loadSettings().then(s => { $('base').value = s.base; $('key').value = s.key; });

$('save').onclick = async () => {
    try {
        const base = checkBase($('base').value.trim());
        await chrome.storage.local.set({ base, key: $('key').value.trim() });
        const r = await ping();
        say(`연결됨 (업무 데스크 ${r.version}). 이제 에듀파인 화면에서 Alt+Shift+S 를 눌러 보세요.`, 'ok');
    } catch (e) {
        say(e.message, 'bad');
    }
};
