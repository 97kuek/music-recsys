import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createStudyService } from '../src/application/studyService.ts';
import { createStudyGateway } from '../src/infrastructure/studyGateway.ts';
import { toggleSelection } from '../src/domain/contracts.ts';

test('初期選択の保存と画面遷移を終えてから推薦を要求する', async () => {
  const calls = [];
  const profile = { items: ['track'], artists: [] };
  const command = { goal: 'discover', top_k: 5 };
  const service = createStudyService({
    saveProfile: async value => { calls.push(['profile', value]); },
    progress: async phase => { calls.push(['progress', phase]); },
    recommend: async value => { calls.push(['recommend', value]); return { request_id: 'r' }; },
  }, () => 'event', { forgetLegacyProfile() {} });
  assert.deepEqual(await service.beginListening(profile, command), { request_id: 'r' });
  assert.deepEqual(calls, [['profile', profile], ['progress', 'listen'], ['recommend', command]]);
});

test('プロフィール保存失敗時は遷移も推論も実行しない', async () => {
  const service = createStudyService({
    saveProfile: async () => { throw new Error('save failed'); },
    progress: () => assert.fail('must not advance'),
    recommend: () => assert.fail('must not infer'),
  }, () => 'event', { forgetLegacyProfile() {} });
  await assert.rejects(service.beginListening({ items: [], artists: [] }, {}), /save failed/);
});

test('操作イベントへ発行IDを付け、入力を変更しない', async () => {
  const events = [];
  const event = { request_id: 'r', kind: 'like', item_id: 'track' };
  let id = 0;
  const service = createStudyService({ event: async value => events.push(value) }, () => `e${++id}`, { forgetLegacyProfile() {} });
  await service.emit(event); await service.emit(event);
  assert.deepEqual(events.map(value => value.event_id), ['e1', 'e2']);
  assert.equal(event.event_id, undefined);
});

test('プロフィールの選択解除と曲数制限を守る', () => {
  const profile = { items: ['a'], artists: [] };
  assert.deepEqual(toggleSelection(profile, 'items', 'a'), { items: [], artists: [] });
  const full = { items: Array.from({ length: 30 }, (_, i) => String(i)), artists: [] };
  assert.deepEqual(toggleSelection(full, 'items', 'extra'), full);
  assert.deepEqual(profile.items, ['a']);
});

test('通信Adapterがカタログ検索を符号化し、外部URLを再生応答で保持する', async () => {
  const calls = [];
  const gateway = createStudyGateway(async (...args) => { calls.push(args); return { url: 'https://audio.example/preview', external: true }; }, path => `/api/${path}`);
  await gateway.catalog('a & b', 20);
  assert.equal(calls[0][0], 'catalog?q=a%20%26%20b&offset=20');
  const playback = await gateway.playback('track', 'request');
  assert.deepEqual(calls[1], ['playbacks', 'POST', { request_id: 'request', item_id: 'track' }]);
  assert.equal(playback.url, 'https://audio.example/preview');
  assert.equal(playback.external, true);
});

test('送信失敗を保持し、同じIDで再送する', async () => {
  const calls = [];
  let fail = true;
  const service = createStudyService({ event: async event => {
    calls.push({ ...event }); if (fail) throw new Error('offline');
  } }, () => 'stable', { forgetLegacyProfile() {} });
  await assert.rejects(service.emit({ request_id: 'r', kind: 'like', item_id: 'a' }), /offline/);
  fail = false;
  await service.flushEvents();
  assert.deepEqual(calls.map(event => event.event_id), ['stable', 'stable']);
  await service.flushEvents();
  assert.equal(calls.length, 2);
});

test('応答待ちの通常送信を待たず、離脱時に同じイベントを送る', async () => {
  let release;
  const stalled = new Promise(resolve => { release = resolve; });
  const calls = [];
  const service = createStudyService({ event: async (event, keepalive) => {
    calls.push([event.event_id, keepalive]); if (!keepalive) await stalled;
  } }, () => 'event', { forgetLegacyProfile() {} });
  const foreground = service.emit({ request_id: 'r', kind: 'playback_progress', playback_id: 'p', item_id: 'a', listened_seconds: 3 });
  await Promise.resolve();
  const flush = service.flushEvents(true);
  // No timer, foreground completion or microtask is required to start the keepalive call.
  assert.deepEqual(calls, [['event', false], ['event', true]]);
  await flush;
  release(); await foreground;
  await service.flushEvents();
  assert.equal(calls.length, 2);
});

test('visibilitychangeとpagehideが続いても同じkeepalive送信を重ねない', async () => {
  let acknowledge;
  const pending = new Promise(resolve => { acknowledge = resolve; });
  const calls = [];
  const service = createStudyService({ event: async (event, keepalive) => {
    calls.push([event.event_id, keepalive]); if (keepalive) await pending; else throw new Error('offline');
  } }, () => 'event', { forgetLegacyProfile() {} });
  await assert.rejects(service.emit({ request_id: 'r', kind: 'like', item_id: 'a' }));
  const first = service.flushEvents(true);
  const second = service.flushEvents(true);
  assert.equal(calls.filter(([, keepalive]) => keepalive).length, 1);
  acknowledge(); await Promise.all([first, second]);
});

test('keepaliveの容量を制限し、最新の累積時間を優先する', async () => {
  const calls = [];
  let index = 0;
  const service = createStudyService({ event: async (event, keepalive) => {
    if (!keepalive) throw new Error('offline');
    calls.push(event);
  } }, () => `id-${++index}`, { forgetLegacyProfile() {} });
  for (let n = 0; n < 80; n++) await assert.rejects(service.emit({ request_id: 'r', kind: 'like', item_id: 'あ'.repeat(200) }));
  await assert.rejects(service.emit({ request_id: 'r', kind: 'playback_progress', item_id: 'a', playback_id: 'p', listened_seconds: 9 }));
  await service.flushEvents(true);
  assert.equal(calls[0].kind, 'playback_progress');
  assert.equal(calls[0].listened_seconds, 9);
  const bytes = calls.reduce((total, event) => total + new TextEncoder().encode(JSON.stringify(event)).byteLength, 0);
  assert.ok(bytes <= 48 * 1024);
  assert.ok(calls.length < 81);
});

test('削除後は未送信記録を次のセッションへ再送しない', async () => {
  let count = 0;
  const service = createStudyService({ event: async () => { count++; throw new Error('offline'); }, remove: async () => ({ deleted: true }) }, () => 'old', { forgetLegacyProfile() {} });
  await assert.rejects(service.emit({ request_id: 'r', kind: 'like', item_id: 'a' }));
  await service.remove();
  await service.flushEvents(true);
  assert.equal(count, 1);
});


test('keepaliveの確認応答後に通常送信が失敗しても再送済みデータを戻さない', async () => {
  let fail;
  const stalled = new Promise((_, reject) => { fail = reject; });
  let count = 0;
  const service = createStudyService({ event: async (_, keepalive) => {
    count++; if (!keepalive) await stalled;
  } }, () => 'acknowledged', { forgetLegacyProfile() {} });
  const foreground = service.emit({ request_id: 'r', kind: 'like', item_id: 'a' });
  await Promise.resolve();
  await service.flushEvents(true);
  fail(new Error('response lost'));
  await foreground;
  await service.flushEvents();
  assert.equal(count, 2);
});


test('離脱送信の確認応答で元の待機と次のイベントを解放する', { timeout: 1000 }, async () => {
  let release;
  const stalled = new Promise(resolve => { release = resolve; });
  let id = 0;
  const calls = [];
  const service = createStudyService({ event: async (event, keepalive) => {
    calls.push([event.event_id, keepalive]);
    if (event.event_id === 'e1' && !keepalive) await stalled;
  } }, () => `e${++id}`, { forgetLegacyProfile() {} });
  const first = service.emit({ request_id: 'r', kind: 'like', item_id: 'a' });
  await Promise.resolve();
  await service.flushEvents(true);
  await first;
  await service.emit({ request_id: 'r', kind: 'like', item_id: 'b' });
  assert.deepEqual(calls, [['e1', false], ['e1', true], ['e2', false]]);
  release();
});
