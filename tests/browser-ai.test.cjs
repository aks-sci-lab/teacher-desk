const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'js', 'browser-ai.js'), 'utf8');
const modulePromise = import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

test('브라우저 내장 AI 지원 검사와 세션 정리', async () => {
    const { askBrowser, browserAvailability } = await modulePromise;
    const original = globalThis.LanguageModel;
    const secure = globalThis.isSecureContext;
    try {
        globalThis.isSecureContext = true;
        delete globalThis.LanguageModel;
        await assert.rejects(browserAvailability(), /사용할 수 없습니다/);
        let destroyed = 0;
        let prompts = [];
        let creates = 0;
        globalThis.LanguageModel = {
            availability: async options => {
                assert.deepEqual(options.expectedOutputs, [{ type: 'text', languages: ['en'] }]);
                return 'available';
            },
            create: async options => {
                creates++;
                options.monitor({ addEventListener(name, fn) { fn({ loaded: 0.5 }); } });
                return {
                    prompt: async (question, opts) => { prompts.push(question); assert.ok(opts.signal); return 'Public answer'; },
                    destroy() { destroyed++; },
                };
            },
        };
        assert.equal(await browserAvailability(), 'available');
        let progress;
        assert.equal(await askBrowser('Why does the Moon shine?', {
            signal: new AbortController().signal, progress: message => { progress = message; },
        }), 'Public answer');
        assert.match(progress, /50%/);
        assert.deepEqual(prompts, ['Why does the Moon shine?']);
        assert.equal(destroyed, 1);
        await assert.rejects(askBrowser('한국어 질문'), /한국어 지원/);
        await assert.rejects(askBrowser(''), /4000자/);
        await assert.rejects(askBrowser('a'.repeat(4001)), /4000자/);
        assert.equal(creates, 1);
        globalThis.LanguageModel.create = async () => ({
            prompt: async () => { throw new Error('test failure'); },
            destroy() { destroyed++; },
        });
        await assert.rejects(askBrowser('English'), /test failure/);
        assert.equal(destroyed, 2);
        globalThis.LanguageModel.create = async () => ({
            prompt: async () => '', destroy() { destroyed++; },
        });
        await assert.rejects(askBrowser('English'), /답변을 반환/);
        assert.equal(destroyed, 3);
        globalThis.isSecureContext = false;
        await assert.rejects(browserAvailability(), /사용할 수 없습니다/);
    } finally {
        if (original === undefined) delete globalThis.LanguageModel;
        else globalThis.LanguageModel = original;
        if (secure === undefined) delete globalThis.isSecureContext;
        else globalThis.isSecureContext = secure;
    }
});
