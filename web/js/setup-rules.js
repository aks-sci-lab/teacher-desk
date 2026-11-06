// 설정 도우미·설정 칸 규칙 (0.5.79) — 화면 부품과 떼어 node 시험(tests/setup-rules.test.cjs)으로 확인
const has = v => !!(v && String(v).trim());

// 기존 사용자 판단: 학교·담임·쿨메신저·에듀파인·텔레그램 중 하나라도 이미 설정됨
export function alreadyConfigured(s) {
    return has(s.school_name) || has(s.teacher_name) || has(s.coolmessenger_name) || has(s.coolmessenger_path)
        || has(s.edufine_folder) || !!s.telegram_token_set;
}

// 켤 때 할 일: open 도우미 열기(새 사용자) · notify 한 번 안내(기존 사용자) · remind '나중에'를 눌렀음 · none
export function setupAction(s) {
    const w = s.setup_wizard || '';
    if (w === 'done' || w === 'notified') return 'none';
    if (w === 'later') return 'remind';
    return alreadyConfigured(s) ? 'notify' : 'open';
}

// 저장 전에 화면에서 먼저 살피는 값 (서버도 한 번 더 검사함). 문제가 없으면 ''
export function checkValue(key, value) {
    if (key === 'collect_times') {
        const bad = value.find(t => !/^\d{1,2}(:\d{1,2})?$/.test(t) || +t.split(':')[0] > 23 || +(t.split(':')[1] || 0) > 59);
        if (bad) return `시각이 잘못되었습니다: ${bad} — 예: 09:00, 12:00, 15:00`;
    }
    if ((key === 'grade' || key === 'class_no') && String(value).trim() && !/^\d{1,2}$/.test(String(value).trim())) {
        return '숫자만 입력하세요 (예: 2)';
    }
    if (key === 'pass_page_url' && String(value).trim() && !/^https:\/\/\S+$/.test(String(value).trim())) {
        return 'https:// 로 시작하는 주소를 입력하세요';
    }
    return '';
}

// 설정 검색: 낱말이 모두 들어 있으면 맞음 (대소문자 무시)
export function matches(text, query) {
    const words = String(query || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    const hay = String(text || '').toLowerCase();
    return words.every(w => hay.includes(w));
}
