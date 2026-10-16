import { useRef, useState } from 'react';
import { api, type Comparison as Row } from './api';

export function Comparison() {
  const [rows, setRows] = useState<Row[]>([]);
  const [message, setMessage] = useState('未読込');
  const loaded = useRef(false);
  const [days, setDays] = useState(30);
  const value = (row: Row, task: string, metric: string) => {
    const cell = row.accuracy.find(x => x.task === task && x.metric === metric && x.k === 20 && x.window_days === days && x.day_weight == null);
    return cell?.mean == null ? '未測定' : cell.mean.toFixed(5);
  };
  return <details id="comparison" onToggle={e => {
    if (e.currentTarget.open && !loaded.current) {
      loaded.current = true; setMessage('読込中');
      api<Row[]>('comparison').then(data => { setRows(data); setMessage(data.length ? '' : 'このデモには実データの評価値を表示しません。'); }).catch(error => { loaded.current = false; setMessage(String(error)); });
    }
  }}><summary>Validationの比較条件を見る</summary>
    <p>新規発見：New Recall@20 ／ 再聴取：Repeat Recall@20 ／ 総合推薦：Binary NDCG@20</p>
    <p>New NDCGは未聴曲限定です。総合NDCGは未測定で、代用しません。採用設定は確定しません。</p>
    <label>評価期間 <select value={days} onChange={e => setDays(Number(e.target.value))}>{[30, 90, 180].map(d => <option key={d} value={d}>{d}日</option>)}</select></label>
    <div className="table-scroll"><table><thead><tr>{['条件', 'New Recall@20', 'Repeat Recall@20', 'New NDCG@20', '総合NDCG@20'].map(t => <th key={t}>{t}</th>)}</tr></thead>
      <tbody>{rows.map(row => <tr key={row.condition}><td>{row.condition}</td><td>{value(row, 'discovery', 'recall')}</td><td>{value(row, 'repeat', 'repeat_recall')}</td><td>{value(row, 'discovery', 'binary_ndcg')}</td><td>未測定</td></tr>)}</tbody></table></div>
    {message && <p>{message}</p>}
    <details><summary>全K・多様性・効率の保存値</summary><pre>{JSON.stringify(rows, null, 2)}</pre></details>
  </details>;
}
