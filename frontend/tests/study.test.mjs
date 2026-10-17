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
