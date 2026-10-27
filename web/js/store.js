// 여러 화면이 함께 쓰는 상태 (설정·학생 명단·서버 상태)
import { api } from './api.js';
import { emit } from './shell.js';

export const store = {
    state: null,      // /api/state
    settings: {},
    students: [],
};

export async function loadState() {
    const s = await api.get('/api/state');
    store.state = s;
    store.settings = s.settings;
    emit('state', s);
    return s;
}

export async function loadStudents() {
    store.students = await api.get('/api/students');
    emit('students', store.students);
    return store.students;
}

export async function saveSettings(change) {
    const r = await api.put('/api/settings', change);
    store.settings = r.settings;
    emit('settings', r.settings);
    return r.settings;
}
