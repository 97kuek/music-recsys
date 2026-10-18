import { useState } from 'react';
import type { ReactNode } from 'react';
import type { OnlineAnalysis, OnlineExperiment } from '../../domain/onlineAnalysis';
import { useStudy } from '../StudyProvider';
import './online-analysis.css';

const number = (n: number | null | undefined) => n == null ? '欠測' : n.toFixed(3);
const rates = [
  ['like_rate', 'Like Rate'], ['play_start_rate', 'Play Start Rate'], ['engaged_listen_rate', 'Engaged Listen Rate（10秒）'],
  ['track_switch_rate', 'Track Switch Rate'], ['early_skip_rate', 'Early Skip Rate'],
] as const;

function Table({ headings, children }: {headings: string[]; children: ReactNode}) {
  return <div className="table-scroll"><table><thead><tr>{headings.map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}

function Bars({title, values, maximum}: {title: string; values: {label: string; value: number | null}[]; maximum?: number}) {
  const measured = values.filter(v => v.value != null);
  if (!measured.length) return <p>{title}：未測定・未回答</p>;
  const scale = maximum ?? (Math.max(...measured.map(v => v.value!)) || 1);
  return <figure><figcaption>{title}</figcaption><svg role="img" aria-label={title} viewBox={`0 0 640 ${values.length * 38 + 10}`}>
    {values.map((row, i) => <g key={row.label} transform={`translate(0 ${i * 38})`}><text x="0" y="25">{row.label.slice(0, 32)}</text>
      {row.value != null && <rect x="210" y="8" width={Math.max(0, row.value / scale * 330)} height="24" rx="3" />}
      <text x="550" y="25">{row.value == null ? '欠測' : number(row.value)}</text></g>)}
  </svg></figure>;
}

function ParticipantPlot({experiment}: {experiment: OnlineExperiment}) {
  const conditions = experiment.conditions;
  const participants = [...new Set(conditions.flatMap(c => c.initial.by_participant.map(p => p.participant_id)))];
  if (!conditions.some(c => c.initial.answered_participants)) return <p>参加者別満足度：未回答</p>;
  const x = (i: number) => 65 + i * (500 / Math.max(1, conditions.length - 1));
  const y = (v: number) => 200 - (v - 1) * 40;
  return <figure><figcaption>参加者ごとの初回満足度（再訪時は参加者内平均）</figcaption>
    <svg role="img" aria-label="参加者ごとの初回満足度" viewBox="0 0 640 250">
      {[1, 2, 3, 4, 5].map(n => <g key={n}><text x="12" y={y(n) + 5}>{n}</text><line x1="40" x2="600" y1={y(n)} y2={y(n)} className="grid" /></g>)}
      {conditions.map((c, i) => <text key={c.condition_id} x={x(i)} y="238" textAnchor="middle">{c.condition_id}</text>)}
      {participants.map((p, pi) => {
        const values = conditions.map(c => c.initial.by_participant.find(v => v.participant_id === p)?.value ?? null);
        return <g key={p} className={`participant-series series-${pi % 5}`}>
          {values.map((value, i) => value == null ? null : <g key={i}>
            {i > 0 && values[i - 1] != null && <line x1={x(i - 1)} y1={y(values[i - 1]!)} x2={x(i)} y2={y(value)} />}
            <circle cx={x(i)} cy={y(value)} r={5 + pi}><title>{p} / {conditions[i].condition_id} / {value}</title></circle>
          </g>)}
        </g>;
      })}
    </svg>
    <p>{participants.map((p, i) => <span className={`series-${i % 5}`} key={p}>● {p}　</span>)}</p>
  </figure>;
}

function Experiment({data}: {data: OnlineExperiment}) {
  const [stage, setStage] = useState('initial');
  const summaries = data.conditions.map(c => ({condition_id: c.condition_id, value: stage === 'initial' ? c.initial : c.final}));
  const strategy = data.plan?.design === 'exploratory_strategy' || data.outcomes.some(o => (o.adjustment_count ?? 0) > 0);
  const labelOrder = (order: number[]) => order.map(i => data.plan?.conditions[i]?.condition_id ?? `条件${i + 1}`).join(' → ');
  return <article>
    <h3>{data.protocol_id}</h3><p>参加者 {data.participants}人 / {data.session_count}セッション / 計画hash <code>{data.plan_sha256 ?? '旧ログ・未記録'}</code></p>
    <p>データ種別：{[...new Set(data.sessions.map(s => s.mode))].join(' / ') || '未観測'}（demoは合成操作確認）。配信config：<code>{data.config_sha256 ?? '旧ログ・未記録'}</code></p>
    <p>完了 {data.sessions.filter(s => s.status === 'completed').length} / 明示離脱 {data.diagnostics.abandoned_sessions} / 未終了 {data.diagnostics.unfinished_sessions}。未終了は離脱の確定ではありません。</p>
    <details><summary>割り当て・完了状況</summary>
      <Table headings={['仮名ID', '枠', '提示順序', '割当時刻']}>{data.assignments.map(a => <tr key={a.participant_id}><td>{a.participant_id}</td><td>{a.slot + 1}</td><td>{labelOrder(a.order)}</td><td>{a.assigned_at}</td></tr>)}</Table>
      <Table headings={['仮名ID', 'セッションID', '順序', '状態']}>{data.sessions.map(s => <tr key={s.session_id}><td>{s.participant_id}</td><td>{s.session_id}</td><td>{labelOrder(s.order)}</td><td>{s.status}</td></tr>)}</Table>
    </details>
    {strategy && <label>満足度の比較対象 <select value={stage} onChange={e => setStage(e.target.value)}><option value="initial">初回</option><option value="final">最終</option></select></label>}
    <Bars title={`${stage === 'initial' ? '初回' : '最終'}満足度の参加者平均`} maximum={5} values={summaries.map(c => ({label: c.condition_id, value: c.value.mean}))} />
    <p>平均・中央値は各参加者の有効セッション内平均を1人1値として集計します。分布と回答数はリスト単位です。</p>
    <Table headings={['条件', '平均', '中央値', '回答者数', '回答リスト', '欠測リスト', '回答率', '1 / 2 / 3 / 4 / 5']}>{summaries.map(c => <tr key={c.condition_id}>
      <td>{c.condition_id}</td><td>{number(c.value.mean)}</td><td>{number(c.value.median)}</td><td>{c.value.answered_participants}/{c.value.participants}</td><td>{c.value.answers}</td><td>{c.value.missing}</td><td>{number(c.value.response_rate)}</td><td>{[1,2,3,4,5].map(n => c.value.distribution?.[String(n)] ?? 0).join(' / ')}</td>
    </tr>)}</Table>
    {summaries.map(c => <Bars key={c.condition_id} title={`${c.condition_id}：満足度分布（回答リスト数）`} values={[1,2,3,4,5].map(n => ({label: `${n}点`, value: c.value.answers ? c.value.distribution?.[String(n)] ?? null : null}))} />)}
    <ParticipantPlot experiment={data} />
    <Table headings={['仮名ID', '条件', '参加者内平均', '回答 / 欠測', '元セッション']}>{summaries.flatMap(c => c.value.by_participant.map(p => <tr key={`${c.condition_id}:${p.participant_id}`}><td>{p.participant_id}</td><td>{c.condition_id}</td><td>{number(p.value)}</td><td>{p.answers} / {p.missing}</td><td>{p.session_ids.join(', ')}</td></tr>))}</Table>
    <details><summary>参加者内の対応差（B − A）</summary><Table headings={['仮名ID', '段階', 'A', 'B', 'A評価', 'B評価', '差']}>{data.paired_differences.map((p, i) => <tr key={i}><td>{p.participant_id}</td><td>{p.stage}</td><td>{p.condition_a}</td><td>{p.condition_b}</td><td>{number(p.a)}</td><td>{number(p.b)}</td><td>{number(p.difference_b_minus_a)}</td></tr>)}</Table></details>
    <h4>試聴・反応・新規発見</h4>
    <p>行動率は有効なリスト率を参加者内で平均してから参加者間で平均します。分子・分母・除外理由はリストCSV/JSONを参照してください。</p>
    <Table headings={['条件', '指標', '参加者平均', '有効リスト', '欠測リスト']}>{data.conditions.flatMap(c => rates.map(([key, label]) => <tr key={`${c.condition_id}:${key}`}><td>{c.condition_id}</td><td>{label}</td><td>{number(c.behavior[key].mean)}</td><td>{c.behavior[key].answers}</td><td>{c.behavior[key].missing}</td></tr>))}</Table>
    <details><summary>試聴閾値の感度分析・Discovery</summary>
      <Table headings={['条件', '閾値', 'Engaged Listen Rate', '回答 / 欠測']}>{data.conditions.flatMap(c => [5,10,15,20].map(t => <tr key={`${c.condition_id}:${t}`}><td>{c.condition_id}</td><td>{t}秒</td><td>{number(c.behavior.engaged_sensitivity[String(t)].mean)}</td><td>{c.behavior.engaged_sensitivity[String(t)].answers} / {c.behavior.engaged_sensitivity[String(t)].missing}</td></tr>))}</Table>
      <Table headings={['条件', '発見曲数', '成功リスト数', '成功リスト率', '回答 / 質問提示', '回答率']}>{data.conditions.map(c => <tr key={c.condition_id}><td>{c.condition_id}</td><td>{c.behavior.discovery.success_tracks ?? '欠測'}</td><td>{c.behavior.discovery.success_lists ?? '欠測'}</td><td>{number(c.behavior.discovery.success_list_rate)}</td><td>{c.behavior.discovery.answered} / {c.behavior.discovery.offered}</td><td>{number(c.behavior.discovery.response_rate)}</td></tr>)}</Table>
    </details>
    {strategy ? <>
      <h4>初回・調整後の推移</h4>
      {data.conditions.map(c => <Bars key={c.condition_id} title={`${c.condition_id}：段階別満足度`} maximum={5} values={[0,1,2].map(n => ({label: n ? `調整${n}` : '初回', value: c.stages[String(n)].mean}))} />)}
      <Table headings={['仮名ID / セッション', '条件', '初回', '調整1', '調整2', '調整数', '最終', '改善', '観測状態']}>{data.outcomes.map((o, i) => <tr key={i}><td>{o.participant_id}<br/>{o.session_id}</td><td>{o.condition_id}</td><td>{number(o.initial_satisfaction)}</td><td>{o.stages['1'].status}: {number(o.stages['1'].satisfaction)}</td><td>{o.stages['2'].status}: {number(o.stages['2'].satisfaction)}</td><td>{o.adjustment_count ?? '未開始'}</td><td>{number(o.final_satisfaction)}</td><td>{number(o.improvement)}</td><td>{o.improvement_status}</td></tr>)}</Table>
      <p>段階間の行動率差と満足度差はoutcomes出力のtransitionsへ記録します。未調整は改善0と扱いません。</p>
    </> : <p>LLM/Agent：実行本体は未実装。調整データがないため推移グラフを表示しません。</p>}
    <h4>効率性・欠測・順序</h4>
    <Bars title="条件別の総推論時間（秒）" values={data.conditions.map(c => ({label: c.condition_id, value: c.latency.inference_seconds.total}))} />
    <Bars title="条件別の総API料金（USD）" values={data.conditions.map(c => ({label: c.condition_id, value: c.usage.api_cost_usd.total}))} />
    <details><summary>計測内訳・欠測と順序効果</summary>
      <p>総計は観測された全要求で計測済みの場合のみ算出します。未開始・失敗要求の時間や料金は補完しません。</p>
      <Table headings={['条件', '区分', '計測', '総計', '計測済み小計', '計測 / 欠測']}>{data.conditions.flatMap(c => [['latency', c.latency], ['usage', c.usage]].flatMap(([kind, totals]) => Object.entries(totals).map(([key, total]) => <tr key={`${c.condition_id}:${kind}:${key}`}><td>{c.condition_id}</td><td>{String(kind)}</td><td>{key}</td><td>{number(total.total)}</td><td>{number(total.measured_subtotal)}</td><td>{total.measured} / {total.missing}</td></tr>)))}</Table>
      <pre>{JSON.stringify(data.diagnostics, null, 2)}</pre>
      <Table headings={['条件', '提示位置', '初回平均', '中央値', '回答 / 欠測']}>{data.order_effects.map(o => <tr key={`${o.condition_id}:${o.position}`}><td>{o.condition_id}</td><td>{o.position}</td><td>{number(o.mean)}</td><td>{number(o.median)}</td><td>{o.answers} / {o.missing}</td></tr>)}</Table>
    </details>
    <details><summary>リスト生データ・要求/event ID・出典hash</summary><pre>{JSON.stringify(data.lists, null, 2)}</pre></details>
  </article>;
}

export function OnlineAnalysisPanel() {
  const service = useStudy();
  const [data, setData] = useState<OnlineAnalysis>();
  const [index, setIndex] = useState(0);
  const [message, setMessage] = useState('未読込');
  const [busy, setBusy] = useState(false);
  const load = async () => {
    setBusy(true);
    try { const result = await service.onlineAnalysis(); setData(result); setIndex(0); setMessage(result.experiments.length ? '' : '期限内の比較ログ・実験計画がありません。'); }
    catch (error) { setMessage(String(error)); }
    finally { setBusy(false); }
  };
  return <details id="online-analysis" className="online-analysis"><summary>オンライン比較実験の分析</summary>
    <p>最大5人程度の探索的比較です。主指標はリスト満足度1〜5。方式の一般的優劣や統計的有意差を判定しません。</p>
    <button className="quiet" disabled={busy} onClick={load}>{busy ? '集計中' : '保存済みログを再集計'}</button><p role="status">{message}</p>
    {data && <>
      <p>集計時刻：{data.generated_at}</p>
      <label>実験・計画版 <select value={index} onChange={e => setIndex(Number(e.target.value))}>{data.experiments.map((e, i) => <option key={i} value={i}>{e.protocol_id} / {e.plan_sha256?.slice(0, 12) ?? '旧ログ'} / {e.config_sha256?.slice(0,8) ?? 'config不明'}</option>)}</select></label>
      <p>出力時に全実験を再集計します。保存した分析コピーの保持期限・本人削除は分析者が管理してください。</p>
      <a className="quiet" href={service.mediaUrl('/api/online-analysis/export?format=json')}>JSONを取得</a>{['lists','outcomes','assignments','sessions','conditions','paired_differences','order_effects'].map(table => <a className="quiet" key={table} href={service.mediaUrl(`/api/online-analysis/export?format=csv&table=${table}`)}>{table} CSV</a>)}
      {data.experiments[index] && <Experiment key={`${data.generated_at}:${index}`} data={data.experiments[index]} />}
    </>}
  </details>;
}
