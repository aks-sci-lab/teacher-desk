import { loadSettings, checkBase, ping } from './capture.js';

const $ = id => document.getElementById(id);
const say = (text, cls) => { $('msg').textContent = text; $('msg').className = cls || ''; };

loadSettings().then(s => {
    $('base').value = s.base;
    $('key').value = s.key;
    $('mode').value = s.mode;
    $('skipTop').checked = s.skipTop;
    $('inputs').checked = s.inputs;
    $('preview').checked = s.preview;
    $('exclude').value = s.exclude;
});

$('save').onclick = async () => {
    try {
        const base = checkBase($('base').value.trim());
        await chrome.storage.local.set({
            base, key: $('key').value.trim(), mode: $('mode').value,
            skipTop: $('skipTop').checked, inputs: $('inputs').checked, preview: $('preview').checked,
            exclude: $('exclude').value,
        });
        const r = await ping();
        say(`저장했습니다. 연결됨 (업무 데스크 ${r.version}). 에듀파인 화면에서 Alt+Shift+S 를 눌러 보세요.`, 'ok');
    } catch (e) {
        say(e.message, 'bad');
    }
};
