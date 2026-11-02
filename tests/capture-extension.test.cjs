// 확장 프로그램이 어느 칸을 보낼지 고르는 규칙 (0.5.55, 에듀파인 실제 구조를 흉내 냄)
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const load = () => import(pathToFileURL(path.join(__dirname, '..', 'browser-extension', 'capture.js')).href);

const TOP = { top: true, host: 'klef.use.go.kr', path: '/keris_ui/main.do', url: 'https://klef.use.go.kr/keris_ui/main.do', title: 'K-에듀파인',
    text: '접근성모드로 전환을 원하시면 컨트롤 + 스페이스 키를 입력하세요.\n\n연장\n59:59\n결재(긴급) 7(0)|\n문서관리\n기안\n결재',
    fields: ['검색어'], selection: '', focused: false, visible: true };
const REG = { top: false, host: 'klef.use.go.kr', path: '/bms/cz/cb/dbx/retrieveDocRegistRegstrList.do',
    url: 'https://klef.use.go.kr/bms/cz/cb/dbx/retrieveDocRegistRegstrList.do', title: '',
    text: '열람\n여부\n게시일\n제목\n' + '2026-11-02 학생 상담 협의록 제출 안내\n'.repeat(60), fields: [], selection: '', focused: false, visible: true };
const WAIT = { top: false, host: 'klef.use.go.kr', path: '/bms/cz/cb/viw/retrieveSanctnWaitDocList.do',
    url: 'https://klef.use.go.kr/bms/cz/cb/viw/retrieveSanctnWaitDocList.do', title: '',
    text: '결재 대기\n2026-11-02 현장체험학습 결과 보고', fields: [], selection: '', focused: false, visible: true };
const OZ = { top: false, host: 'klef.use.go.kr', path: '/thirdParty/ozexeviewer/ozexeviewer.jsp', url: 'x', title: '', text: '', fields: [], selection: '', focused: false, visible: true };
const frames = (...over) => [TOP, REG, WAIT, OZ].map((f, i) => ({ ...f, ...(over[i] || {}) }));
const base = { mode: 'auto', skipTop: true, inputs: false, exclude: '접근성모드로 전환을 원하시면', picked: {} };

test('자동: 바깥 틀(메뉴·안내문)은 빼고 글이 가장 많은 목록 하나', async () => {
    const { combine } = await load();
    const c = combine(frames(), base);
    assert.deepEqual(c.paths, [REG.path]);
    assert.ok(!c.text.includes('접근성모드'));
    assert.ok(!c.text.includes('문서관리'));
    assert.equal(c.frame_url, REG.url);
});

test('자동: 마지막으로 누른 칸이 있으면 그 칸 (글이 적어도)', async () => {
    const { combine } = await load();
    assert.deepEqual(combine(frames({}, {}, { focused: true }), base).paths, [WAIT.path]);
});

test('자동: 숨은 칸은 빼고, 안쪽 칸에 글이 없으면 바깥 틀', async () => {
    const { combine } = await load();
    assert.deepEqual(combine(frames({}, { visible: false }), base).paths, [WAIT.path]);
    const onlyTop = combine([TOP, OZ], base);
    assert.deepEqual(onlyTop.paths, [TOP.path]);
    assert.ok(!onlyTop.text.includes('접근성모드'));                   // 빼기 목록은 그대로
});

test('한 번 고른 칸: 사이트별로 기억한 칸만, 없으면 안내', async () => {
    const { combine } = await load();
    const s = { ...base, mode: 'pick', picked: { 'klef.use.go.kr': WAIT.path } };
    assert.deepEqual(combine(frames(), s).paths, [WAIT.path]);
    assert.throws(() => combine(frames(), { ...base, mode: 'pick' }), /고르지 않았습니다/);
    assert.throws(() => combine([TOP, REG], s), /이 화면에 없습니다/);
    assert.deepEqual(combine(frames(), base, REG.path).paths, [REG.path]);   // 팝업의 [이 칸만 보내기]
});

test('마우스로 고른 글: 어느 방식이든 고른 글이 먼저, "고른 글만" 은 없으면 보내지 않음', async () => {
    const { combine } = await load();
    const c = combine(frames({}, {}, { selection: '현장체험학습 결과 보고' }), base);
    assert.equal(c.selected, true);
    assert.equal(c.text, '현장체험학습 결과 보고');
    assert.throws(() => combine(frames(), { ...base, mode: 'selection' }), /고르세요/);
});

test('전체: 바깥 틀 빼기 옵션과 입력 칸 옵션', async () => {
    const { combine } = await load();
    assert.deepEqual(combine(frames(), { ...base, mode: 'all' }).paths, [REG.path, WAIT.path]);
    const all = combine(frames(), { ...base, mode: 'all', skipTop: false, inputs: true });
    assert.deepEqual(all.paths, [TOP.path, REG.path, WAIT.path]);
    assert.ok(all.text.includes('검색어'));
    assert.ok(!combine(frames(), { ...base, mode: 'all', skipTop: false }).text.includes('검색어'));
});

test('빼기 목록: 문구가 든 줄을 지우고 빈 줄 묶음을 줄임', async () => {
    const { filterLines } = await load();
    assert.equal(filterLines('가\n\n\n로그아웃\n나\n\n다', ['로그아웃']), '가\n\n나\n\n다');
    assert.equal(filterLines('가\n나', []), '가\n나');
});
