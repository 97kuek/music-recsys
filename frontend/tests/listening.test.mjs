import assert from 'node:assert/strict';
import { test } from 'node:test';
import { listeningDelta } from '../src/domain/listening.ts';

test('seek、位置の巻戻し、未計測区間を聴取秒数に加算しない', () => {
  const previous = {wall:1000, position:1};
  assert.equal(listeningDelta(previous, {wall:2000, position:2}, false), 1);
  assert.equal(listeningDelta(previous, {wall:2000, position:25}, false), 0);
  assert.equal(listeningDelta(previous, {wall:2000, position:2}, true), 0);
  assert.equal(listeningDelta(previous, {wall:2000, position:0}, false), 0);
  assert.equal(listeningDelta(null, {wall:2000, position:25}, false), 0);
});

test('再生位置の増分より実経過時間を多く数えない', () => {
  assert.equal(listeningDelta({wall:0, position:0}, {wall:1000, position:1.4}, false), 1);
});
