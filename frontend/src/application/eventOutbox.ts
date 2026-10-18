import type { EventInput } from '../domain/contracts';

type Event = EventInput & { event_id: string };
type Sender = (event: Event, keepalive?: boolean) => Promise<unknown>;
const KEEPALIVE_BYTES = 48 * 1024;
const MAX_PENDING = 5000;

// Retain unacknowledged events in memory and reuse their IDs for every attempt.
export function createEventOutbox(send: Sender, newId: () => string) {
  const pending = new Map<string, Event>();
  const receipts = new Map<string, { promise: Promise<void>; resolve: () => void }>();
  const normal = new Map<string, Promise<void>>();
  const background = new Set<string>();
  let serial = Promise.resolve();
  let keepaliveBytes = 0;

  async function attempt(event: Event, keepalive = false): Promise<void> {
    if (!pending.has(event.event_id)) return;
    try {
      await send(event, keepalive);
      if (pending.get(event.event_id) === event) {
        pending.delete(event.event_id);
        receipts.get(event.event_id)?.resolve();
        receipts.delete(event.event_id);
      }
    } catch (error) {
      // A concurrent keepalive attempt may already have received an acknowledgement.
      if (pending.get(event.event_id) === event) throw error;
    }
  }

  function schedule(event: Event): Promise<void> {
    const existing = normal.get(event.event_id);
    if (existing) return existing;
    const next = serial.then(() => {
      if (!pending.has(event.event_id)) return;
      const receipt = receipts.get(event.event_id)!;
      // Any successful attempt releases callers and the queue, even if the original fetch stalls.
      return Promise.race([attempt(event), receipt.promise]);
    });
    normal.set(event.event_id, next);
    serial = next.catch(() => {});
    const release = () => { if (normal.get(event.event_id) === next) normal.delete(event.event_id); };
    next.then(release, release);
    return next;
  }

  function emit(input: EventInput): Promise<void> {
    if (pending.size >= MAX_PENDING) return Promise.reject(new Error('未送信イベントの上限です。接続を確認してください'));
    const event = { ...input, event_id: newId() };
    let acknowledge!: () => void;
    const promise = new Promise<void>(resolve => { acknowledge = resolve; });
    receipts.set(event.event_id, { promise, resolve: acknowledge });
    pending.set(event.event_id, event);
    return schedule(event);
  }

  async function flush(keepalive = false): Promise<void> {
    if (!keepalive) {
      await Promise.all([...pending.values()].map(schedule));
      return;
    }
    // Bypass a stalled foreground request. Prioritize the final cumulative time snapshot.
    const events = [...pending.values()];
    const snapshots = events.filter(event => event.kind === 'playback_progress').reverse();
    const others = events.filter(event => event.kind !== 'playback_progress');
    const tasks: Promise<void>[] = [];
    for (const event of [...snapshots, ...others]) {
      if (background.has(event.event_id)) continue;
      const bytes = new TextEncoder().encode(JSON.stringify(event)).byteLength;
      if (keepaliveBytes + bytes > KEEPALIVE_BYTES) continue;
      background.add(event.event_id);
      keepaliveBytes += bytes;
      // Invoking attempt here starts fetch synchronously, before the lifecycle handler returns.
      const task = attempt(event, true).finally(() => {
        background.delete(event.event_id);
        keepaliveBytes -= bytes;
      });
      tasks.push(task);
    }
    await Promise.allSettled(tasks);
  }

  function clear() {
    pending.clear();
    for (const receipt of receipts.values()) receipt.resolve();
    receipts.clear();
    normal.clear();
    serial = Promise.resolve();
  }

  return { emit, flush, clear };
}
