import { useRef } from 'react';
import type { Status } from '../../domain/contracts';
import { MediaIcon } from './MediaIcon';
import { ParticipationNotice } from './ParticipationNotice';

export function Information({ status, onDelete }: { status: Status; onDelete?: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  return <><button className="icon-button" aria-label="このアプリについて" title="このアプリについて" onClick={() => dialog.current?.showModal()}><MediaIcon name="help" /></button>
    <dialog ref={dialog} className="information" aria-label="このアプリについて"><div className="section-heading"><h2>このアプリについて</h2><button className="icon-button" aria-label="説明を閉じる" onClick={() => dialog.current?.close()}><MediaIcon name="down" /></button></div>
      <div className="scroll-region"><p>{status.logging_mode === 'browser-memory' ? 'デモ音源・架空の曲名を使用しています。操作情報はブラウザ内のみで扱います。' : status.mode === 'demo' ? 'デモ音源・架空の曲名を使用しています。実曲の推薦評価とは異なります。' : '利用条件を確認できた音源のみ試聴できます。'}</p>
      <ParticipationNotice localOnly={status.logging_mode === 'browser-memory'} retentionDays={status.retention_days} />
      <p><a href="/notice.txt" target="_blank" rel="noreferrer">利用条件・音源について</a> · <a href="/license.txt" target="_blank" rel="noreferrer">ソフトウェアライセンス</a> · <a href="/third-party-licenses.txt" target="_blank" rel="noreferrer">第三者ライセンス</a></p>
      {onDelete && <button className="quiet" onClick={() => { dialog.current?.close(); onDelete(); }}>参加データを削除</button>}</div>
    </dialog></>;
}
