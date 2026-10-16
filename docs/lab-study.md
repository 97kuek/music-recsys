# music-recsysのMusic4All被験者実験準備

確認日: 2026-10-16。Music4Allを優先、外部音楽サービスとCloudflareは選択肢として保持
現在は開発用検証。実被験者データ収集・実音源配信は開始しない
研究室サーバーの外部公開・ポート開放は禁止。Cloudflare Tunnel等による公開も行わない

## 現状と開始条件

| 対象 | 現状 | 開始までに必要なもの |
| --- | --- | --- |
| 音源 | リポジトリdata以下に実曲なし、独自合成WAV12本のみ | Music4All clip保存先・提供条件・制限付きWeb試聴の許諾 |
| 楽曲表示 | 学習カタログ45,888曲、タイトル/artist接続0曲 | 原版ID対応メタデータ |
| Popularity | 総再生回数/再生ユーザー数の実モデル2条件 | 試聴可能な共通候補への対応 |
| ItemCF | Cosine/Jaccard×20/50/100の実モデル6条件 | 比較条件を明示、採用モデルは未確定 |
| ALS/BPR | 実データ重み未発見、デモ用の小規模重みだけ | Validation比較設定を明示して学習＋export |
| 研究同意 | 開発用24時間保持・任意保存・削除のみ | 説明文/保持期間/撤回/参加者アクセス方針の決定 |
| 配置先 | 学習・評価は研究室、アプリは個人Mac | 実モデルbundleを移し、Dockerでローカル検証 |

今回の音源監査範囲は `data/`。既存学習IDとファイル名stemの対応は0/45,888
これは手元の確認範囲に対する値であり、Music4All全体の配布音源の存在率ではない
別サーバーや未マウント領域の音源は未確認。共有領域を無断探索しない
形式・実容量・音声長は未取得なので未測定。合成音源の容量をMusic4All音源容量としない

## 音源と許諾

[Music4All公式](https://sites.google.com/view/contact4music4all)は109,269曲・30秒clip・ID/曲名/artist/spotifyidと提供申請先を説明
取得申請先: **contact4music4all@gmail.com**（公式サイト確認日: 2026-10-16）
必要なのは30秒clip、元の16文字IDと曲名/artistの対応表、取得・利用条件の3点
公開Onion履歴を取得しただけではclipと曲名対応表は揃わない
[Onion v2](https://zenodo.org/records/15394646)は履歴・特徴量の公開であり、原曲をWeb配信する包括許諾とは扱わない
提供元へ取得・少人数の限定試聴について申請済み、回答待ち。確認する条件:
- 研究室所属前の教育プロジェクト、少人数の仮名ユーザーへの30秒clip試聴
- 個人Macでの複製・試聴、招待参加者に限定した認証付き遠隔アクセス
- タイトル・artist・画像等の表示、帰属表示、取得経路、期限、音源削除条件
- 行動ログ・主観評価の収集/分析、将来の研究発表・一般公開に別許諾が必要か
- 研究室が取得済みの場合、当初の提供条件が上記用途を含むか

音源の所在が判明したら読取り監査を再実行する。許諾のない音源は配信しない

```bash
.venv/bin/python -m music_recsys.webapp.audit \
  --config data/app/music4all/config.json --audio-root /許可された音源保存先 \
  --output data/app/music4all/audio-audit-new.json
```

監査は指定ルート、形式、容量、ID対応数、モデルhash/ロード/推論を保存。設定選択・再学習なし
現在の結果: `data/app/music4all/audit-20261016.json`

許諾後の登録は `music_recsys.webapp.media`。新規bundleだけを作り、既存結果は上書きしない
メタデータCSVは `item_id,title,artists`、複数artistは `|` 区切り
音源はファイルstemがitem_idと一致すること。曖昧一致・同一IDの複数音源・ルート外symlinkを拒否
別版の音声を同じIDへ手動流用しない。ファイル名一致だけで権利・録音同一性は証明できない
許諾JSONは人が確認した証拠への参照を記録する。任意の文字列で法的許諾が発生するわけではない

```json
{
  "web_preview": true,
  "access_scope": "restricted-study",
  "evidence_ref": "確認済み提供条件の内部参照",
  "expires_at": "許諾期限のtimezone付きISO日時",
  "item_ids": ["明示的に許諾された楽曲ID"]
}
```

```bash
.venv/bin/python -m music_recsys.webapp.media \
  --config data/app/music4all/config.json --metadata /private/tracks.csv \
  --audio-root /private/clips --permission /private/permission.json \
  --output data/app/music4all-listening
```

音源を新bundleへコピーしhash検証、許諾記録のhashを保持。コピー分の空き容量が必要
モデルは元の絶対パス参照を保持するので、別ホストへ移す際はモデル配置と参照を再確認する
WAV/MP3/FLAC/OGG/M4Aを登録可能、ブラウザcodec対応は実音源入手後に検証する
音声APIは認可済み推薦と再生IDを照合し、形式に応じたContent-Typeで返す
デモ音源を実曲IDへ貼り付けて実曲試聴済みとしない

## モデルと測定

実データ8モデルで3回warmup＋20要求、3曲seed・K=20、CPU/1スレッドの配信計算を実測

| 条件 | 中央値ms | 新規ユーザー |
| --- | ---: | --- |
| Popularity再生回数 / 再生ユーザー数 | 6.35 / 6.64 | 保存済み統計 |
| ItemCF Cosine20 / 50 / 100 | 7.10 / 6.77 / 6.31 | 選択曲の近傍 |
| ItemCF Jaccard20 / 50 / 100 | 6.25 / 6.25 / 6.27 | 選択曲の近傍 |

8モデル常駐Runtimeロード約0.91秒、最大RSS約364 MiB。FastAPI負荷試験・ネット往復は含まない
逐次ロード監査のRSS約168 MiBと全モデル常駐値を混同しない
sushiで62 GiB RAM、当該filesystem空き約20 GiB、RTX3090 24 GiBは確認時0%/1 MiB使用
tyan/microの構成・空き容量は未確認。このGPU情報を他ホストへ外挿しない
大規模学習前は再度ホストnvidia-smiを確認し、共有利用規則に従ってGPUを優先

ALSは保存済み楽曲因子のfold-in実装あり、今回実データ因子がなく未実測
BPR未知ユーザーはPopularity補完。これをBPR固有の性能として比較してはいけない
現行8モデルはValidationの学習入力から作成済み、17条件の評価結果は比較用で採用設定は未確定
ALS/BPR再学習は設定/計算計画を明示して別出力へexport。評価JSONを重みとしてロードしない
[モデル配信](model-serving.md)の保存・fold-in・候補制約を参照

## 操作とログ

曲を試聴して初期選択→推薦リスト→任意の満足度1問。条件秘匿を維持
通常モードは目的を選択。比較モードは現在discover固定・同じ初期選択・候補/K・各条件1要求
同一被験者に複数条件を提示し、seedと仮名IDで順序を記録。少人数での均等割付は未実装
モデル名/設定/実際のfallbackはサーバーログへ保存し被験者へ表示しない

追加ログ:
- セッションの初期プロフィール、各推薦時のプロフィールと実聴取曲を別保存
- 順位はresponse.item_idsの順序、条件・モデル版・offline_run_ref・目的・推論時間は既存契約
- listened_secondsは再生ID単位の累積実再生進行時間のクライアント計測値
- シークで進んだ位置を試聴時間に加算しない。position_secondsとは別、同じ再生IDは最大累積値を用いる
- response_latency_msはAPI呼出しから応答まで、display_latency_msは同じ開始からカード初回可視まで
- 後でスクロールしたカードのdisplay時間は操作待ちを含む。初回カード表示遅延と分けて分析する
- ブラウザ終了や通信切断前の未送信分は欠測。本人が本当に聴いた証明にはならない
- 初期選択は再生履歴・Repeat Recallの正解ではない

同意なしはメモリのみ、同意ありでも現状は開発用24時間SQLite。実験用保持期間を勝手に決めない
本人セッションから削除可能、再起動後の本人認証/撤回窓口とバックアップ削除は本番前に決定する
オフライン指標をオンライン主観評価へ混ぜず、モデル版・条件参照で対応付ける
LLM/AgentはStrategy交換で追加、今回実装しない

## アプリの配置

学習・評価は[研究室の標準運用](lab-operation.md)、参加者向けアプリは[個人Mac](mac-study.md)で動かす
ReactとAPIをFastAPIから同一Origin配信し、モデルは起動時にロードする
現行セッションはメモリ内なので1 workerを使い、ログSQLiteはMacの共通stateへ保存する
研究室サーバーの外部公開・ポート開放・Tunnel起動は行わない
Cloudflare公開プレビューは[合成デモ](cloudflare.md)で、研究室APIや実音源へ接続しない
遠隔試聴を追加する場合はMac側だけに参加者認証を設け、提供条件と同意範囲を確認する

## 検証と残作業

単体/統合テスト: `tests/test_lab_study.py`、既存webapp/study/foundation/model_artifactsと合わせて確認
合成音源で2条件秘匿→実再生進行→停止/時間記録→任意評価→終了→削除をブラウザ確認
実曲E2Eは音源・メタデータ・許諾が未取得なので未実行
順序は音源と提供条件確認→metadata/audio登録→ALS/BPR重み準備→条件固定→ログ/同意/アクセス方針確定→予備実験
